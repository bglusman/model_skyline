"""Normalize prompt-free structured-decision and compound-system runs.

The input contract intentionally contains case digests and scored outcomes, not
prompts, model responses, or hidden reference answers.  One run describes one
exact routable offering.  A compound offering identifies every component and
the routing/guidance policy that connects them, so a cheap router cannot receive
credit while the fallback worker's time and cost disappear.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Final, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.models import (
    CanonicalDecimal,
    CanonicalJsonObject,
    FrozenModel,
    Observation,
    ObservationCatalog,
    OfferingKey,
    OfferingObservation,
    PositiveSafeCount,
    SafeCount,
    Sha256Digest,
    SourceReference,
    WorkloadReference,
)

STRUCTURED_DECISION_RUN_SCHEMA_VERSION: Final = "model-skyline/structured-decision-run/v1alpha1"
STRUCTURED_DECISION_ADAPTER_ID: Final = "model-skyline/structured-decision-run"
STRUCTURED_DECISION_ADAPTER_VERSION: Final = "2"
MAX_STRUCTURED_DECISION_RUN_BYTES: Final = 64_000_000
_FORBIDDEN_METADATA_KEYS: Final = frozenset(
    {
        "answer",
        "answers",
        "expected",
        "expected_answer",
        "message",
        "messages",
        "model_output",
        "prompt",
        "prompts",
        "raw_output",
        "reference_answer",
        "response",
        "responses",
        "state",
    }
)


class StructuredDecisionAdapterError(ValueError):
    """A structured-decision run is incomplete, incoherent, or unsafe to import."""


class StructuredDecisionWorkloadIdentity(FrozenModel):
    """Fields that must remain identical for candidates to be comparable."""

    suite_id: str = Field(min_length=1, max_length=512)
    suite_version: str = Field(min_length=1, max_length=512)
    case_manifest_sha256: Sha256Digest
    case_set_sha256: Sha256Digest
    case_count: PositiveSafeCount
    harness_id: str = Field(min_length=1, max_length=512)
    harness_version: str = Field(min_length=1, max_length=512)
    scorer_version: str = Field(min_length=1, max_length=512)
    oracle_kind: Literal[
        "deterministic",
        "human_labels",
        "model_consensus",
        "environment_verifier",
    ]
    repetitions_per_case: PositiveSafeCount
    concurrency: PositiveSafeCount = 1


def structured_decision_workload_version(
    workload: StructuredDecisionWorkloadIdentity,
) -> str:
    """Derive a portable version from every comparability-critical field."""

    return f"structured-decision-sha256:{content_hash(workload.model_dump(mode='json'))}"


def structured_decision_case_set_sha256(
    cases: Iterable[tuple[str, str]],
) -> str:
    """Hash the exact sorted case-ID/digest set bound to a workload."""

    normalized = sorted(
        ({"case_id": case_id, "case_sha256": case_sha256} for case_id, case_sha256 in cases),
        key=lambda item: (item["case_id"], item["case_sha256"]),
    )
    return content_hash(normalized)


class CompoundComponent(FrozenModel):
    """One exact model/component and how the compound policy may activate it."""

    component_id: str = Field(min_length=1, max_length=256)
    role: Literal["decision", "router", "worker", "fallback", "guardrail", "verifier"]
    resource_class: Literal["light", "heavy"]
    activation: Literal[
        "always",
        "router_selected",
        "confidence_fallback",
        "policy_trigger",
        "post_action",
        "on_failure",
    ]
    offering: OfferingKey
    guidance_sha256: Sha256Digest


class ComponentUsage(FrozenModel):
    """Calls and attributed cost for one declared component in one case."""

    component_id: str = Field(min_length=1, max_length=256)
    calls: SafeCount
    cost_usd: CanonicalDecimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=12)


class StructuredDecisionSystemIdentity(FrozenModel):
    """The complete candidate, including routing policy and every component."""

    kind: Literal["decision_component", "single_model_system", "compound_model_system"]
    routing_policy_sha256: Sha256Digest
    components: tuple[CompoundComponent, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def component_shape_matches_kind(self) -> Self:
        component_ids = [component.component_id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component_id values must be unique")
        if self.kind == "decision_component":
            if len(self.components) != 1 or self.components[0].role != "decision":
                raise ValueError("decision_component requires exactly one decision component")
        elif self.kind == "single_model_system":
            if len(self.components) != 1 or self.components[0].role != "worker":
                raise ValueError("single_model_system requires exactly one worker component")
        else:
            roles = {component.role for component in self.components}
            if len(self.components) < 2:
                raise ValueError("compound_model_system requires at least two components")
            if "worker" not in roles and "fallback" not in roles:
                raise ValueError("compound_model_system requires a worker or fallback component")
            if not roles.intersection({"router", "decision", "guardrail"}):
                raise ValueError(
                    "compound_model_system requires a router, decision, or guardrail component"
                )
        return self


class StructuredDecisionCaseResult(FrozenModel):
    """Prompt-free outcome for one case repetition."""

    case_id: str = Field(min_length=1, max_length=512)
    case_sha256: Sha256Digest
    repetition: PositiveSafeCount
    decision_correct: bool
    final_success: bool
    schema_valid: bool
    abstained: bool
    unsafe_action: bool
    latency_seconds: CanonicalDecimal = Field(ge=0, max_digits=38, decimal_places=9)
    total_cost_usd: CanonicalDecimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=12
    )
    model_calls: SafeCount
    heavy_model_calls: SafeCount
    component_usage: tuple[ComponentUsage, ...] = Field(min_length=1, max_length=16)
    brier_score: CanonicalDecimal | None = Field(
        default=None, ge=0, le=1, max_digits=18, decimal_places=12
    )
    decision_max_probability: CanonicalDecimal | None = Field(
        default=None, ge=0, le=1, max_digits=18, decimal_places=12
    )
    expected_probability: CanonicalDecimal | None = Field(
        default=None, ge=0, le=1, max_digits=18, decimal_places=12
    )
    primary_success: bool | None = None
    router_decision_correct: bool | None = None
    router_abstained: bool | None = None
    router_max_probability: CanonicalDecimal | None = Field(
        default=None, ge=0, le=1, max_digits=18, decimal_places=12
    )
    router_brier_score: CanonicalDecimal | None = Field(
        default=None, ge=0, le=1, max_digits=18, decimal_places=12
    )
    tool_selection_correct: bool | None = None
    tool_arguments_correct: bool | None = None
    tool_sequence_correct: bool | None = None
    tool_policy_compliant: bool | None = None
    tool_side_effects_correct: bool | None = None

    @model_validator(mode="after")
    def outcome_is_coherent(self) -> Self:
        if self.heavy_model_calls > self.model_calls:
            raise ValueError("heavy_model_calls cannot exceed model_calls")
        component_ids = [usage.component_id for usage in self.component_usage]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component_usage component_id values must be unique")
        if sum(usage.calls for usage in self.component_usage) != self.model_calls:
            raise ValueError("component_usage calls must sum to model_calls")
        if self.final_success and (not self.schema_valid or self.unsafe_action):
            raise ValueError("a successful result must be schema-valid and safe")
        if self.decision_correct and not self.schema_valid:
            raise ValueError("a correct decision must be schema-valid")
        router_diagnostics = (
            self.router_decision_correct,
            self.router_abstained,
            self.router_max_probability,
            self.router_brier_score,
        )
        if any(value is None for value in router_diagnostics) and any(
            value is not None for value in router_diagnostics
        ):
            raise ValueError(
                "router correctness, abstention, probability, and Brier diagnostics "
                "must be reported together"
            )
        if self.final_success and any(
            value is False
            for value in (
                self.tool_selection_correct,
                self.tool_arguments_correct,
                self.tool_sequence_correct,
                self.tool_policy_compliant,
                self.tool_side_effects_correct,
            )
        ):
            raise ValueError("a successful tool result cannot fail an applicable tool check")
        return self


class StructuredDecisionRun(FrozenModel):
    """One exact decision component or complete single/compound system run."""

    schema_version: Literal["model-skyline/structured-decision-run/v1alpha1"]
    observed_at: datetime
    workload_id: str = Field(min_length=1, max_length=512)
    workload_unit: str = Field(min_length=1, max_length=512)
    workload: StructuredDecisionWorkloadIdentity
    benchmark_source: SourceReference
    offering: OfferingKey
    system: StructuredDecisionSystemIdentity
    cost_basis: Literal["billed", "provider_reported", "reconstructed", "unavailable"]
    results: tuple[StructuredDecisionCaseResult, ...] = Field(min_length=1, max_length=1_000_000)
    metadata: CanonicalJsonObject = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("metadata")
    @classmethod
    def metadata_is_prompt_free(cls, value: CanonicalJsonObject) -> CanonicalJsonObject:
        pending: list[dict[str, Any]] = [value]
        while pending:
            current = pending.pop()
            for key, child in current.items():
                normalized_key = key.lower().replace("-", "_")
                if normalized_key in _FORBIDDEN_METADATA_KEYS:
                    raise ValueError(
                        "metadata cannot contain prompts, states, answers, messages, or responses"
                    )
                if isinstance(child, dict):
                    pending.append(child)
                elif isinstance(child, list):
                    pending.extend(item for item in child if isinstance(item, dict))
        return value

    @model_validator(mode="after")
    def run_is_complete_and_exact(self) -> Self:
        if any(
            value is None
            for value in (
                self.benchmark_source.url,
                self.benchmark_source.license,
                self.benchmark_source.methodology,
                self.benchmark_source.raw_sha256,
                self.benchmark_source.retrieved_at,
            )
        ):
            raise ValueError(
                "benchmark_source requires URL, license, methodology, raw hash, and retrieval time"
            )
        if (
            self.benchmark_source.retrieved_at is not None
            and self.benchmark_source.retrieved_at > self.observed_at
        ):
            raise ValueError("benchmark_source cannot be retrieved after the run was observed")
        keys = [(result.case_id, result.repetition) for result in self.results]
        duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
        if duplicates:
            raise ValueError("case_id/repetition values must be unique")

        repetitions: dict[str, set[int]] = defaultdict(set)
        digests: dict[str, set[str]] = defaultdict(set)
        for result in self.results:
            repetitions[result.case_id].add(result.repetition)
            digests[result.case_id].add(result.case_sha256)
        expected_repetitions = set(range(1, self.workload.repetitions_per_case + 1))
        if any(values != expected_repetitions for values in repetitions.values()):
            raise ValueError("every case must contain the exact configured repetitions")
        if any(len(values) != 1 for values in digests.values()):
            raise ValueError("one case_id cannot map to multiple case digests")
        if len(digests) != self.workload.case_count:
            raise ValueError("result case count must equal the workload case_count")
        case_set_sha256 = structured_decision_case_set_sha256(
            (case_id, next(iter(case_digests))) for case_id, case_digests in digests.items()
        )
        if case_set_sha256 != self.workload.case_set_sha256:
            raise ValueError("result case IDs and digests do not match workload case_set_sha256")

        costs = [result.total_cost_usd for result in self.results]
        component_ids = {component.component_id for component in self.system.components}
        heavy_component_ids = {
            component.component_id
            for component in self.system.components
            if component.resource_class == "heavy"
        }
        for result in self.results:
            usage_by_id = {usage.component_id: usage for usage in result.component_usage}
            if set(usage_by_id) != component_ids:
                raise ValueError("every result must report usage for every declared component")
            heavy_calls = sum(
                usage_by_id[component_id].calls for component_id in heavy_component_ids
            )
            if result.heavy_model_calls != heavy_calls:
                raise ValueError(
                    "heavy_model_calls must equal calls attributed to heavy components"
                )
        if self.cost_basis == "unavailable":
            if any(value is not None for value in costs):
                raise ValueError("unavailable cost basis cannot contain cost values")
            if any(
                usage.cost_usd is not None
                for result in self.results
                for usage in result.component_usage
            ):
                raise ValueError("unavailable cost basis cannot contain component costs")
        elif any(value is None for value in costs):
            raise ValueError("a declared cost basis requires cost for every result")
        else:
            for result in self.results:
                component_costs = [usage.cost_usd for usage in result.component_usage]
                if any(value is None for value in component_costs):
                    raise ValueError(
                        "a declared cost basis requires cost for every component usage"
                    )
                if (
                    sum((value for value in component_costs if value is not None), Decimal(0))
                    != result.total_cost_usd
                ):
                    raise ValueError("component costs must sum to total_cost_usd")

        if self.system.kind == "decision_component":
            if "structured-decisions" not in self.offering.capabilities:
                raise ValueError(
                    "decision_component offerings require the structured-decisions capability"
                )
            if any(result.final_success != result.decision_correct for result in self.results):
                raise ValueError("decision_component final_success must equal decision_correct")
            if any(result.router_decision_correct is not None for result in self.results):
                raise ValueError("decision_component results cannot report a separate router")
        elif self.system.kind == "compound_model_system":
            if "compound-system" not in self.offering.capabilities:
                raise ValueError(
                    "compound_model_system offerings require the compound-system capability"
                )
            if self.offering.agent_harness is None:
                raise ValueError("compound_model_system requires an exact agent_harness")
        elif any(result.primary_success is not None for result in self.results):
            raise ValueError("only compound_model_system results can report primary_success")
        return self


def _mean(values: list[Decimal]) -> Decimal:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        return sum(values, Decimal(0)) / Decimal(len(values))


def _percent(numerator: int, denominator: int) -> Decimal:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        return Decimal(numerator) * Decimal(100) / Decimal(denominator)


def _quantile_type7(values: list[Decimal], probability: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    with localcontext(POLICY_DECIMAL_CONTEXT):
        position = Decimal(len(ordered) - 1) * probability
        lower = int(position)
        fraction = position - Decimal(lower)
        return ordered[lower] + (ordered[lower + 1] - ordered[lower]) * fraction


def _observation(
    value: Decimal,
    *,
    unit: str,
    run: StructuredDecisionRun,
    source: SourceReference,
    sample_count: int,
) -> Observation:
    return Observation(
        value=value,
        unit=unit,
        sample_count=sample_count,
        observed_at=run.observed_at,
        source=source,
    )


def normalize_structured_decision_run(
    run: StructuredDecisionRun,
    *,
    raw_sha256: str,
    retrieved_at: datetime,
) -> ObservationCatalog:
    """Project one validated prompt-free run into frontier-ready signals."""

    if retrieved_at.tzinfo is None:
        raise StructuredDecisionAdapterError("retrieved_at must include a timezone")
    results = list(run.results)
    count = len(results)
    successes = sum(result.final_success for result in results)
    non_abstained = [result for result in results if not result.abstained]
    source = SourceReference(
        id=f"structured-decision-run:sha256:{raw_sha256}",
        version=STRUCTURED_DECISION_RUN_SCHEMA_VERSION,
        url=run.benchmark_source.url,
        terms_url=run.benchmark_source.terms_url,
        license=run.benchmark_source.license,
        methodology=(
            "Prompt-free per-case structured-decision outcomes normalized by "
            f"{STRUCTURED_DECISION_ADAPTER_ID}@{STRUCTURED_DECISION_ADAPTER_VERSION}; "
            "all model calls, fallback calls, latency, and declared cost belong to the exact "
            "routable offering. Benchmark methodology: "
            f"{run.benchmark_source.methodology}"
        ),
        raw_sha256=raw_sha256,
        retrieved_at=retrieved_at.astimezone(UTC),
    )

    signals: dict[str, Observation] = {}

    def add(signal: str, value: Decimal, unit: str, sample_count: int = count) -> None:
        signals[signal] = _observation(
            value,
            unit=unit,
            run=run,
            source=source,
            sample_count=sample_count,
        )

    add(
        "structured_decision_accuracy_percent",
        _percent(sum(result.decision_correct for result in results), count),
        "percent",
    )
    add("structured_final_success_percent", _percent(successes, count), "percent")
    add(
        "structured_schema_valid_percent",
        _percent(sum(result.schema_valid for result in results), count),
        "percent",
    )
    add(
        "structured_autonomous_coverage_percent",
        _percent(len(non_abstained), count),
        "percent",
    )
    if non_abstained:
        add(
            "structured_autonomous_accuracy_percent",
            _percent(sum(result.final_success for result in non_abstained), len(non_abstained)),
            "percent",
            len(non_abstained),
        )
    add(
        "structured_unsafe_action_percent",
        _percent(sum(result.unsafe_action for result in results), count),
        "percent",
    )
    add(
        "structured_p95_latency_seconds",
        _quantile_type7([result.latency_seconds for result in results], Decimal("0.95")),
        "s",
    )
    add(
        "structured_mean_model_calls_per_case",
        _mean([Decimal(result.model_calls) for result in results]),
        "call/case",
    )
    add(
        "structured_mean_heavy_model_calls_per_case",
        _mean([Decimal(result.heavy_model_calls) for result in results]),
        "call/case",
    )

    costs = [result.total_cost_usd for result in results]
    if all(value is not None for value in costs):
        complete_costs = [value for value in costs if value is not None]
        total_cost = sum(complete_costs, Decimal(0))
        add("structured_mean_cost_usd_per_case", _mean(complete_costs), "USD/case")
        if successes:
            with localcontext(POLICY_DECIMAL_CONTEXT):
                add(
                    "structured_cost_usd_per_success",
                    total_cost / Decimal(successes),
                    "USD/success",
                )

    brier_scores = [result.brier_score for result in results]
    if all(value is not None for value in brier_scores):
        add(
            "structured_mean_brier_score",
            _mean([value for value in brier_scores if value is not None]),
            "score",
        )

    primary_results = [result for result in results if result.primary_success is not None]
    if primary_results:
        add(
            "structured_primary_success_percent",
            _percent(
                sum(bool(result.primary_success) for result in primary_results),
                len(primary_results),
            ),
            "percent",
            len(primary_results),
        )
        primary_errors = [result for result in primary_results if not result.primary_success]
        if primary_errors:
            add(
                "structured_primary_error_rescue_percent",
                _percent(
                    sum(result.final_success for result in primary_errors),
                    len(primary_errors),
                ),
                "percent",
                len(primary_errors),
            )
        primary_successes = [result for result in primary_results if result.primary_success]
        if primary_successes:
            add(
                "structured_primary_override_harm_percent",
                _percent(
                    sum(not result.final_success for result in primary_successes),
                    len(primary_successes),
                ),
                "percent",
                len(primary_successes),
            )

    routed = [result for result in results if result.router_decision_correct is not None]
    if routed:
        add(
            "structured_router_decision_accuracy_percent",
            _percent(sum(bool(result.router_decision_correct) for result in routed), len(routed)),
            "percent",
            len(routed),
        )
        add(
            "structured_router_autonomous_coverage_percent",
            _percent(sum(not bool(result.router_abstained) for result in routed), len(routed)),
            "percent",
            len(routed),
        )
        add(
            "structured_router_mean_max_probability",
            _mean(
                [
                    result.router_max_probability
                    for result in routed
                    if result.router_max_probability is not None
                ]
            ),
            "probability",
            len(routed),
        )
        add(
            "structured_router_mean_brier_score",
            _mean(
                [
                    result.router_brier_score
                    for result in routed
                    if result.router_brier_score is not None
                ]
            ),
            "score",
            len(routed),
        )
        router_errors = [result for result in routed if not result.router_decision_correct]
        if router_errors:
            add(
                "structured_router_error_rescue_percent",
                _percent(sum(result.final_success for result in router_errors), len(router_errors)),
                "percent",
                len(router_errors),
            )
        router_successes = [result for result in routed if result.router_decision_correct]
        if router_successes:
            add(
                "structured_router_override_harm_percent",
                _percent(
                    sum(not result.final_success for result in router_successes),
                    len(router_successes),
                ),
                "percent",
                len(router_successes),
            )

    for field, signal in (
        ("tool_selection_correct", "structured_tool_selection_accuracy_percent"),
        ("tool_arguments_correct", "structured_tool_argument_accuracy_percent"),
        ("tool_sequence_correct", "structured_tool_sequence_accuracy_percent"),
        ("tool_policy_compliant", "structured_tool_policy_compliance_percent"),
        ("tool_side_effects_correct", "structured_tool_side_effect_accuracy_percent"),
    ):
        applicable = [
            getattr(result, field) for result in results if getattr(result, field) is not None
        ]
        if applicable:
            add(signal, _percent(sum(applicable), len(applicable)), "percent", len(applicable))

    metadata: dict[str, Any] = {
        "adapter": {
            "id": STRUCTURED_DECISION_ADAPTER_ID,
            "version": STRUCTURED_DECISION_ADAPTER_VERSION,
        },
        "system": run.system.model_dump(mode="json"),
        "system_kind": run.system.kind,
        "compound": run.system.kind == "compound_model_system",
        "component_count": len(run.system.components),
        "cost_basis": run.cost_basis,
        "workload_identity": run.workload.model_dump(mode="json"),
        "benchmark_source": run.benchmark_source.model_dump(mode="json"),
        "case_count": len({result.case_id for result in results}),
        "result_count": count,
        "contains_prompt_or_response_text": False,
        "run_metadata": run.metadata,
    }
    return ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WorkloadReference(
            id=run.workload_id,
            version=structured_decision_workload_version(run.workload),
            unit=run.workload_unit,
        ),
        offerings=[
            OfferingObservation(
                offering=run.offering,
                signals=signals,
                metadata=metadata,
                default_source=source,
            )
        ],
    )


def _read_json_bytes(raw: bytes, *, label: str) -> tuple[dict[str, Any], str]:
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_STRUCTURED_DECISION_RUN_BYTES:
        raise StructuredDecisionAdapterError(
            f"{label} exceeds the {MAX_STRUCTURED_DECISION_RUN_BYTES}-byte input limit"
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise StructuredDecisionAdapterError(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            object_pairs_hook=unique_object,
        )
    except StructuredDecisionAdapterError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        InvalidOperation,
        RecursionError,
        ValueError,
    ) as exc:
        raise StructuredDecisionAdapterError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise StructuredDecisionAdapterError(f"{label} must contain one JSON object")
    return decoded, hashlib.sha256(raw).hexdigest()


def _read_regular_file(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise StructuredDecisionAdapterError(f"cannot open {label} {source}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise StructuredDecisionAdapterError(f"{label} must be a regular file")
            if before.st_size > MAX_STRUCTURED_DECISION_RUN_BYTES:
                raise StructuredDecisionAdapterError(
                    f"{label} exceeds the {MAX_STRUCTURED_DECISION_RUN_BYTES}-byte input limit"
                )
            raw = handle.read(MAX_STRUCTURED_DECISION_RUN_BYTES + 1)
            after = os.fstat(handle.fileno())
    except StructuredDecisionAdapterError:
        raise
    except OSError as exc:
        raise StructuredDecisionAdapterError(f"cannot read {label} {source}: {exc}") from exc
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity or len(raw) != before.st_size:
        raise StructuredDecisionAdapterError(f"{label} changed while it was read")
    if len(raw) > MAX_STRUCTURED_DECISION_RUN_BYTES:
        raise StructuredDecisionAdapterError(
            f"{label} exceeds the {MAX_STRUCTURED_DECISION_RUN_BYTES}-byte input limit"
        )
    return raw


def normalize_structured_decision_bytes(
    raw: bytes,
    *,
    retrieved_at: datetime,
) -> ObservationCatalog:
    """Validate and normalize one in-memory run document."""

    document, raw_sha256 = _read_json_bytes(raw, label="structured-decision run")
    try:
        run = StructuredDecisionRun.model_validate(document)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in errors[:20]
        )
        if len(errors) > 20:
            details += f"; {len(errors) - 20} additional validation errors omitted"
        raise StructuredDecisionAdapterError(f"invalid structured-decision run: {details}") from exc
    except (TypeError, ValueError) as exc:
        raise StructuredDecisionAdapterError("invalid structured-decision run") from exc
    return normalize_structured_decision_run(
        run,
        raw_sha256=raw_sha256,
        retrieved_at=retrieved_at,
    )


def normalize_structured_decision_file(
    path: str | Path,
    *,
    retrieved_at: datetime,
) -> ObservationCatalog:
    """Read one stable regular file and normalize its prompt-free outcomes."""

    return normalize_structured_decision_bytes(
        _read_regular_file(path, label="structured-decision run"),
        retrieved_at=retrieved_at,
    )
