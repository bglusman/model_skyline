from __future__ import annotations

import csv
import html
import io
from datetime import UTC
from email.utils import format_datetime
from typing import Any
from xml.etree import ElementTree as ET

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


def render_rss(
    snapshot: FrontierSnapshot,
    *,
    previous: FrontierSnapshot | None = None,
    link: str | None = None,
) -> str:
    """Render one change item; a publisher can retain items from older snapshots."""

    if previous is not None and (
        previous.frontier_id != snapshot.frontier_id or previous.workload != snapshot.workload
    ):
        raise ValueError("previous RSS snapshot must use the same frontier and workload")
    entrants, removals = _changes(snapshot, previous)
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
    item = ET.SubElement(channel, "item")
    ET.SubElement(
        item, "title"
    ).text = f"Frontier {_display(snapshot.snapshot_id)}: +{len(entrants)} / -{len(removals)}"
    guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid.text = _display(snapshot.snapshot_id)
    ET.SubElement(item, "pubDate").text = format_datetime(snapshot.generated_at.astimezone(UTC))
    if link:
        ET.SubElement(item, "link").text = _display(link)

    def escaped(value: Any) -> str:
        return html.escape(_display(value), quote=True)

    ordered = "".join(
        "<li>"
        + escaped(item.offering.offering_id)
        + " — "
        + "; ".join(
            f"{escaped(axis.metric)}: {escaped(item.axes[axis.metric].value)} {escaped(axis.unit)}"
            for axis in snapshot.axes
        )
        + "</li>"
        for item in snapshot.members
    )
    description = (
        f"<p>Entrants: {', '.join(escaped(value) for value in entrants) or 'none'}</p>"
        f"<p>Removals: {', '.join(escaped(value) for value in removals) or 'none'}</p>"
        f"<ol>{ordered}</ol>"
    )
    ET.SubElement(item, "description").text = description
    ET.SubElement(item, f"{{{RSS_NAMESPACE}}}snapshotId").text = _display(snapshot.snapshot_id)
    for offering_id in entrants:
        ET.SubElement(item, f"{{{RSS_NAMESPACE}}}entrant").text = _display(offering_id)
    for offering_id in removals:
        ET.SubElement(item, f"{{{RSS_NAMESPACE}}}removal").text = _display(offering_id)
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="unicode", xml_declaration=True) + "\n"
