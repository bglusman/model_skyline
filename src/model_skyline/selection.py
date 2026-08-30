from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Any

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.models import (
    FrontierSnapshot,
    Goal,
    InsufficientCandidates,
    ModelChoice,
    ProjectConfig,
    SelectionSnapshot,
)


def _hash(value: Any, length: int = 20) -> str:
    return content_hash(value)[:length]


def _sort_members(
    snapshot: FrontierSnapshot,
    order_by: str,
) -> tuple[Any, ...]:
    axes = {axis.metric: axis for axis in snapshot.axes}
    if order_by not in axes:
        raise ValueError(f"selection order_by {order_by!r} is not a frontier axis")
    primary = axes[order_by]
    secondary = next(axis for axis in snapshot.axes if axis.metric != order_by)

    def preference(value: Decimal, goal: Goal) -> Decimal:
        return value if goal is Goal.MINIMIZE else -value

    with localcontext(POLICY_DECIMAL_CONTEXT):
        return tuple(
            sorted(
                snapshot.members,
                key=lambda item: (
                    preference(item.axes[primary.metric].value, primary.goal),
                    preference(item.axes[secondary.metric].value, secondary.goal),
                    item.offering.offering_id,
                ),
            )
        )


def _diverse(
    members: Iterable[Any],
    *,
    count: int,
    max_per_provider: int | None,
) -> tuple[Any, ...]:
    selected: list[Any] = []
    provider_counts: dict[str, int] = {}
    for member in members:
        provider = member.offering.provider
        current = provider_counts.get(provider, 0)
        if max_per_provider is not None and current >= max_per_provider:
            continue
        selected.append(member)
        provider_counts[provider] = current + 1
        if len(selected) == count:
            break
    return tuple(selected)


def select_models(
    config: ProjectConfig,
    snapshot: FrontierSnapshot,
    selection_id: str,
) -> SelectionSnapshot:
    try:
        definition = config.selections[selection_id]
    except KeyError as exc:
        raise ValueError(f"unknown selection {selection_id!r}") from exc
    if definition.frontier != snapshot.frontier_id:
        raise ValueError(
            f"selection {selection_id!r} expects frontier {definition.frontier!r}, "
            f"not {snapshot.frontier_id!r}"
        )
    order_by = definition.order_by or snapshot.order_by
    selected = _diverse(
        _sort_members(snapshot, order_by),
        count=definition.count,
        max_per_provider=definition.max_per_provider,
    )
    if not selected:
        raise ValueError("frontier has no selectable members")
    if (
        len(selected) < definition.count
        and definition.on_insufficient is InsufficientCandidates.ERROR
    ):
        raise ValueError(
            f"selection requires {definition.count} candidates but only {len(selected)} satisfy it"
        )
    choices = tuple(
        ModelChoice(
            offering=item.offering,
            axes=item.axes,
            metadata=item.metadata,
        )
        for item in selected
    )
    value = SelectionSnapshot(
        snapshot_id="pending",
        policy_hash=_hash(
            {
                "selection_id": selection_id,
                "definition": definition.model_dump(mode="json"),
            },
            length=64,
        ),
        frontier_snapshot_id=snapshot.snapshot_id,
        selection_id=selection_id,
        frontier_id=snapshot.frontier_id,
        workload=snapshot.workload,
        strategy=definition.strategy,
        order_by=order_by,
        requested_count=definition.count,
        max_per_provider=definition.max_per_provider,
        on_insufficient=definition.on_insufficient,
        generated_at=snapshot.generated_at,
        valid_until=snapshot.generated_at + timedelta(seconds=definition.snapshot_ttl_seconds),
        default=choices[0],
        fallbacks=choices[1:],
    )
    snapshot_id = _hash(value.model_dump(mode="json", exclude={"snapshot_id"}), length=64)
    return value.model_copy(update={"snapshot_id": snapshot_id})


def selection_hash(snapshot: SelectionSnapshot) -> str:
    return _hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}), length=64)
