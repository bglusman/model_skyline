from __future__ import annotations

import csv
import html
import io
from collections.abc import Iterable, Mapping
from datetime import UTC
from email.utils import format_datetime
from typing import Any

# ElementTree is used only to construct RSS; this module never parses XML.
from xml.etree import ElementTree as ET  # nosec B405  # nosemgrep

from model_skyline.models import FrontierSnapshot

RSS_NAMESPACE = "urn:model-skyline:rss:1.0"
ET.register_namespace("skyline", RSS_NAMESPACE)


def _display(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", r"\r").replace("\n", r"\n").replace("\t", r"\t")


def _rows(snapshot: FrontierSnapshot) -> tuple[list[str], list[list[str]]]:
    metadata_fields = tuple(
        dict.fromkeys(field for item in snapshot.members for field in item.metadata)
    )
    headers = [
        "rank",
        "offering",
        "model",
        "provider",
        *(_display(axis.metric) for axis in snapshot.axes),
        *(_display(field) for field in metadata_fields),
    ]
    rows: list[list[str]] = []
    for rank, item in enumerate(snapshot.members, start=1):
        rows.append(
            [
                str(rank),
                _display(item.offering.offering_id),
                _display(item.offering.model_id),
                _display(item.offering.provider),
                *(_display(item.axes[axis.metric].value) for axis in snapshot.axes),
                *(_display(item.metadata.get(field)) for field in metadata_fields),
            ]
        )
    return headers, rows


def render_table(snapshot: FrontierSnapshot) -> str:
    headers, rows = _rows(snapshot)
    if not rows:
        return "No eligible frontier members.\n"
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def line(values: list[str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    separator = "-+-".join("-" * width for width in widths)
    return "\n".join([line(headers), separator, *(line(row) for row in rows)]) + "\n"


def _spreadsheet_safe(value: str) -> str:
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(snapshot: FrontierSnapshot, *, spreadsheet_safe: bool = True) -> str:
    headers, rows = _rows(snapshot)
    if spreadsheet_safe:
        headers = [_spreadsheet_safe(value) for value in headers]
        numeric_columns = {0, 4, 5}
        rows = [
            [
                value if index in numeric_columns else _spreadsheet_safe(value)
                for index, value in enumerate(row)
            ]
            for row in rows
        ]
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def _changes(
    current: FrontierSnapshot,
    previous: FrontierSnapshot | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    current_ids = {item.offering.offering_id for item in current.members}
    previous_ids = {item.offering.offering_id for item in previous.members} if previous else set()
    return tuple(sorted(current_ids - previous_ids)), tuple(sorted(previous_ids - current_ids))


def _offering_view(item: Any) -> dict[str, Any]:
    payload: dict[str, Any] = item.offering.model_dump(mode="json")
    if payload.get("billing_mode") is None:
        payload.pop("billing_mode", None)
    return payload


def frontier_view(snapshot: FrontierSnapshot) -> tuple[Any, ...]:
    """Return the routing- and display-relevant semantic frontier view."""

    return tuple(
        (
            _offering_view(item),
            tuple(
                (
                    axis.metric,
                    item.axes[axis.metric].model_dump(
                        mode="json",
                        include={"value", "lower", "upper"},
                    ),
                )
                for axis in snapshot.axes
            ),
            item.metadata,
        )
        for item in snapshot.members
    )


def _baseline_reset(current: FrontierSnapshot, previous: FrontierSnapshot | None) -> bool:
    return previous is None or (
        previous.workload != current.workload
        or previous.config_hash != current.config_hash
        or previous.axes != current.axes
        or previous.order_by != current.order_by
        or previous.uncertainty != current.uncertainty
    )


def _rank_and_value_changes(
    current: FrontierSnapshot,
    previous: FrontierSnapshot | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if previous is None:
        return (), ()
    current_by_id = {
        item.offering.offering_id: (rank, item)
        for rank, item in enumerate(current.members, start=1)
    }
    previous_by_id = {
        item.offering.offering_id: (rank, item)
        for rank, item in enumerate(previous.members, start=1)
    }
    common = sorted(set(current_by_id) & set(previous_by_id))
    rank_changes = tuple(
        f"{offering_id}:{previous_by_id[offering_id][0]}->{current_by_id[offering_id][0]}"
        for offering_id in common
        if previous_by_id[offering_id][0] != current_by_id[offering_id][0]
    )
    value_changes = tuple(
        offering_id
        for offering_id in common
        if any(
            current_by_id[offering_id][1].axes[axis.metric]
            != previous_by_id[offering_id][1].axes[axis.metric]
            for axis in current.axes
        )
    )
    return rank_changes, value_changes


def _append_rss_item(
    channel: ET.Element,
    snapshot: FrontierSnapshot,
    previous: FrontierSnapshot | None,
    *,
    link: str | None,
    baseline_reset: bool,
) -> None:
    entrants, removals = _changes(snapshot, None if baseline_reset else previous)
    rank_changes, value_changes = _rank_and_value_changes(
        snapshot,
        None if baseline_reset else previous,
    )
    item = ET.SubElement(channel, "item")
    if baseline_reset:
        title = f"Frontier {_display(snapshot.snapshot_id)}: baseline reset"
    else:
        title = (
            f"Frontier {_display(snapshot.snapshot_id)}: +{len(entrants)} / -{len(removals)}"
            f" / ranks {len(rank_changes)} / values {len(value_changes)}"
        )
    ET.SubElement(item, "title").text = title
    guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid.text = _display(snapshot.snapshot_id)
    ET.SubElement(item, "pubDate").text = format_datetime(snapshot.generated_at.astimezone(UTC))
    if link:
        ET.SubElement(item, "link").text = _display(link)

    def escaped(value: Any) -> str:
        return html.escape(_display(value), quote=True)

    ordered = "".join(
        "<li>"
        + escaped(member.offering.offering_id)
        + " — "
        + "; ".join(
            f"{escaped(axis.metric)}: "
            f"{escaped(member.axes[axis.metric].value)} {escaped(axis.unit)}"
            for axis in snapshot.axes
        )
        + "</li>"
        for member in snapshot.members
    )
    description = (
        f"<p>Baseline reset: {'yes' if baseline_reset else 'no'}</p>"
        f"<p>Entrants: {', '.join(escaped(value) for value in entrants) or 'none'}</p>"
        f"<p>Removals: {', '.join(escaped(value) for value in removals) or 'none'}</p>"
        f"<p>Rank changes: "
        f"{', '.join(escaped(value) for value in rank_changes) or 'none'}</p>"
        f"<p>Value changes: "
        f"{', '.join(escaped(value) for value in value_changes) or 'none'}</p>"
        f"<ol>{ordered}</ol>"
    )
    ET.SubElement(item, "description").text = description
    ET.SubElement(item, f"{{{RSS_NAMESPACE}}}snapshotId").text = _display(snapshot.snapshot_id)
    ET.SubElement(item, f"{{{RSS_NAMESPACE}}}baselineReset").text = (
        "true" if baseline_reset else "false"
    )
    for offering_id in entrants:
        ET.SubElement(item, f"{{{RSS_NAMESPACE}}}entrant").text = _display(offering_id)
    for offering_id in removals:
        ET.SubElement(item, f"{{{RSS_NAMESPACE}}}removal").text = _display(offering_id)
    for change in rank_changes:
        ET.SubElement(item, f"{{{RSS_NAMESPACE}}}rankChange").text = _display(change)
    for offering_id in value_changes:
        ET.SubElement(item, f"{{{RSS_NAMESPACE}}}valueChange").text = _display(offering_id)


def render_rss_history(
    snapshots: Iterable[FrontierSnapshot],
    *,
    max_items: int = 20,
    channel_link: str | None = None,
    item_links: Mapping[str, str] | None = None,
) -> str:
    """Render retained semantic changes from validated immutable snapshots."""

    if not 1 <= max_items <= 1000:
        raise ValueError("RSS max_items must be between 1 and 1000")
    by_id: dict[str, FrontierSnapshot] = {}
    for snapshot in snapshots:
        existing = by_id.get(snapshot.snapshot_id)
        if existing is not None and existing != snapshot:
            raise ValueError("one RSS snapshot id maps to multiple artifacts")
        by_id[snapshot.snapshot_id] = snapshot
    if not by_id:
        raise ValueError("RSS history requires at least one snapshot")
    ordered = sorted(
        by_id.values(),
        key=lambda value: (value.generated_at, value.snapshot_id),
    )
    frontier_ids = {snapshot.frontier_id for snapshot in ordered}
    if len(frontier_ids) != 1:
        raise ValueError("RSS history snapshots must use one frontier id")

    meaningful: list[tuple[FrontierSnapshot, FrontierSnapshot | None, bool]] = []
    previous: FrontierSnapshot | None = None
    for snapshot in ordered:
        reset = _baseline_reset(snapshot, previous)
        if reset or previous is None or frontier_view(snapshot) != frontier_view(previous):
            meaningful.append((snapshot, previous, reset))
        previous = snapshot
    retained = list(reversed(meaningful[-max_items:]))
    latest = ordered[-1]

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{_display(latest.frontier_id)} model skyline"
    ET.SubElement(
        channel, "description"
    ).text = f"Changes to the {_display(latest.frontier_id)} workload-specific Pareto frontier."
    ET.SubElement(channel, "link").text = _display(
        channel_link or f"urn:model-skyline:{latest.frontier_id}"
    )
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        latest.generated_at.astimezone(UTC)
    )
    links = item_links or {}
    for snapshot, prior, reset in retained:
        _append_rss_item(
            channel,
            snapshot,
            prior,
            link=links.get(snapshot.snapshot_id),
            baseline_reset=reset,
        )
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="unicode", xml_declaration=True) + "\n"


def render_rss(
    snapshot: FrontierSnapshot,
    *,
    previous: FrontierSnapshot | None = None,
    link: str | None = None,
) -> str:
    """Render one change item; publishers should use :func:`render_rss_history`."""

    if previous is not None and (
        previous.frontier_id != snapshot.frontier_id or previous.workload != snapshot.workload
    ):
        raise ValueError("previous RSS snapshot must use the same frontier and workload")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{_display(snapshot.frontier_id)} model skyline"
    ET.SubElement(
        channel, "description"
    ).text = f"Changes to the {_display(snapshot.frontier_id)} workload-specific Pareto frontier."
    ET.SubElement(channel, "link").text = _display(
        link or f"urn:model-skyline:{snapshot.frontier_id}"
    )
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        snapshot.generated_at.astimezone(UTC)
    )
    _append_rss_item(
        channel,
        snapshot,
        previous,
        link=link,
        baseline_reset=_baseline_reset(snapshot, previous),
    )
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="unicode", xml_declaration=True) + "\n"
