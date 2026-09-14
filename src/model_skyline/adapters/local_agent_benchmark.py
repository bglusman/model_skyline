"""Normalize prompt-free local real-world agent benchmark summaries.

The adapter accepts task digests, scores, timings, and check counts—not prompts,
answers, trajectories, or model messages.  It emits route-free quality evidence;
an operator must still reconcile the subject to an exact :class:`OfferingKey`.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT
from model_skyline.quality_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    QualityComponentIdentity,
    QualityCount,
    QualityEvidenceRow,
    QualityEvidenceSet,
    QualityMeasurement,
    QualityMeasurementRole,
    QualityModelClaim,
    QualityPublicationPermission,
    QualityRawAudit,
    QualityResult,
    QualityRights,
    QualityRouteDisclosure,
    QualitySourceIdentity,
    QualitySubjectIdentity,
    QualitySubjectKind,
    quality_raw_sha256,
)

LOCAL_AGENT_SUMMARY_SCHEMA: Final = "model-skyline/local-agent-benchmark-summary/v1"
LOCAL_AGENT_ADAPTER_ID: Final = "model-skyline/local-agent-benchmark-summary"
LOCAL_AGENT_ADAPTER_VERSION: Final = "1"
MAX_INPUT_BYTES: Final = 64_000_000
MAX_TASKS: Final = 10_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LocalAgentBenchmarkKind(StrEnum):
    RESEARCHCLAWBENCH = "researchclawbench"
    BROWSECOMP = "browsecomp"
    GAIA = "gaia"


_BENCHMARK_IDENTITIES: Final = {
    LocalAgentBenchmarkKind.RESEARCHCLAWBENCH: ("ResearchClawBench", 40),
    LocalAgentBenchmarkKind.BROWSECOMP: ("BrowseComp", 1_266),
    LocalAgentBenchmarkKind.GAIA: ("GAIA", 466),
}
_QUALITY_SIGNALS: Final = {
    LocalAgentBenchmarkKind.RESEARCHCLAWBENCH: "measured_research_task_quality_percent",
    LocalAgentBenchmarkKind.BROWSECOMP: "measured_browsing_answer_accuracy_percent",
    LocalAgentBenchmarkKind.GAIA: "measured_general_assistant_exact_match_percent",
}
_COHORT_FIELDS: Final = {
    LocalAgentBenchmarkKind.RESEARCHCLAWBENCH: frozenset(
        {
            "workspace_manifest_sha256",
            "source_snapshot_sha256",
            "judge_model",
            "judge_revision",
            "network_policy",
        }
    ),
    LocalAgentBenchmarkKind.BROWSECOMP: frozenset(
        {
            "encrypted_dataset_sha256",
            "grader_model",
            "grader_revision",
            "search_provider",
            "browser_provider",
            "execution_window_started_at",
            "execution_window_finished_at",
            "tool_budget",
        }
    ),
    LocalAgentBenchmarkKind.GAIA: frozenset(
        {
            "attachment_manifest_sha256",
            "attachment_policy",
            "web_policy",
            "dataset_access",
        }
    ),
}


class LocalAgentBenchmarkAdapterError(ValueError):
    """A local benchmark summary violates the prompt-free evidence contract."""


def _object(
    value: object,
    *,
    field: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LocalAgentBenchmarkAdapterError(f"{field} must be an object")
    keys = frozenset(value)
    if not required <= keys or keys - (required | optional):
        raise LocalAgentBenchmarkAdapterError(f"{field} does not match the reviewed field contract")
    return value


def _string(value: object, *, field: str, maximum: int = 2_048) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LocalAgentBenchmarkAdapterError(f"{field} must be a bounded non-empty string")
    return value


def _sha256(value: object, *, field: str) -> str:
    result = _string(value, field=field, maximum=64)
    if _SHA256_RE.fullmatch(result) is None:
        raise LocalAgentBenchmarkAdapterError(f"{field} must be a lowercase SHA-256 digest")
    return result


def _count(value: object, *, field: str, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 2**53 - 1:
        qualifier = "positive " if positive else "non-negative "
        raise LocalAgentBenchmarkAdapterError(f"{field} must be a safe {qualifier}integer")
    return value


def _decimal(
    value: object,
    *,
    field: str,
    minimum: Decimal = Decimal(0),
    maximum: Decimal | None = None,
) -> Decimal:
    if not isinstance(value, str):
        raise LocalAgentBenchmarkAdapterError(f"{field} must be an exact decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise LocalAgentBenchmarkAdapterError(f"{field} must be a finite decimal string") from exc
    if not result.is_finite() or result < minimum or (maximum is not None and result > maximum):
        raise LocalAgentBenchmarkAdapterError(f"{field} is outside its accepted range")
    return result


def _timestamp(value: object, *, field: str) -> datetime:
    raw = _string(value, field=field, maximum=64)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LocalAgentBenchmarkAdapterError(f"{field} must be an ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        raise LocalAgentBenchmarkAdapterError(f"{field} must include a timezone")
    return result.astimezone(UTC)


def _component(value: object, *, field: str) -> QualityComponentIdentity:
    item = _object(
        value,
        field=field,
        required=frozenset({"id", "version", "configuration_sha256"}),
    )
    return QualityComponentIdentity(
        id=_string(item.get("id"), field=f"{field}.id"),
        version=_string(item.get("version"), field=f"{field}.version"),
        configuration={
            "configuration_sha256": _sha256(
                item.get("configuration_sha256"), field=f"{field}.configuration_sha256"
            )
        },
    )


def _checks(value: object, *, field: str) -> tuple[int, int]:
    item = _object(
        value,
        field=field,
        required=frozenset({"passed", "total"}),
    )
    passed = _count(item.get("passed"), field=f"{field}.passed")
    total = _count(item.get("total"), field=f"{field}.total")
    if passed > total:
        raise LocalAgentBenchmarkAdapterError(f"{field}.passed cannot exceed total")
    return passed, total


def _percent(passed: int, total: int, *, field: str) -> Decimal:
    if total <= 0:
        raise LocalAgentBenchmarkAdapterError(f"{field} requires at least one check")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        return Decimal(passed) * Decimal(100) / Decimal(total)


def _quantile_type7(values: list[Decimal], quantile: Decimal) -> Decimal:
    ordered = sorted(values)
    if not ordered:
        raise LocalAgentBenchmarkAdapterError("task timings must not be empty")
    if len(ordered) == 1:
        return ordered[0]
    with localcontext(POLICY_DECIMAL_CONTEXT):
        position = Decimal(len(ordered) - 1) * quantile
        lower_index = int(position)
        fraction = position - Decimal(lower_index)
        upper_index = min(lower_index + 1, len(ordered) - 1)
        return ordered[lower_index] + fraction * (ordered[upper_index] - ordered[lower_index])


def _cohort(
    value: object,
    *,
    kind: LocalAgentBenchmarkKind,
    retrieved_at: datetime,
) -> dict[str, Any]:
    required = _COHORT_FIELDS[kind]
    cohort = _object(value, field="benchmark.cohort", required=required)
    result: dict[str, Any] = {}
    for key in sorted(required):
        raw = cohort[key]
        if key.endswith("_sha256"):
            result[key] = _sha256(raw, field=f"benchmark.cohort.{key}")
        elif key == "tool_budget":
            result[key] = _count(raw, field="benchmark.cohort.tool_budget", positive=True)
        elif key.startswith("execution_window_"):
            result[key] = _timestamp(raw, field=f"benchmark.cohort.{key}").isoformat()
        else:
            result[key] = _string(raw, field=f"benchmark.cohort.{key}")
    if kind is LocalAgentBenchmarkKind.BROWSECOMP:
        started = _timestamp(
            cohort["execution_window_started_at"],
            field="benchmark.cohort.execution_window_started_at",
        )
        finished = _timestamp(
            cohort["execution_window_finished_at"],
            field="benchmark.cohort.execution_window_finished_at",
        )
        if started > finished or finished > retrieved_at:
            raise LocalAgentBenchmarkAdapterError(
                "BrowseComp execution window must be ordered and no later than retrieval"
            )
    if kind is LocalAgentBenchmarkKind.GAIA and cohort["dataset_access"] != "gated":
        raise LocalAgentBenchmarkAdapterError("GAIA dataset_access must be 'gated'")
    return result


def normalize_local_agent_benchmark_bytes(
    raw: bytes,
    *,
    retrieved_at: datetime,
    source_locator: str,
) -> QualityEvidenceSet:
    """Normalize one bounded, prompt-free local benchmark summary."""

    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_INPUT_BYTES:
        raise LocalAgentBenchmarkAdapterError(f"summary exceeds {MAX_INPUT_BYTES} bytes")
    if retrieved_at.tzinfo is None:
        raise LocalAgentBenchmarkAdapterError("retrieved_at must include a timezone")
    retrieved_at = retrieved_at.astimezone(UTC)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalAgentBenchmarkAdapterError("summary is not valid UTF-8 JSON") from exc
    root = _object(
        decoded,
        field="summary",
        required=frozenset(
            {
                "schema_version",
                "contains_prompts_or_model_messages",
                "benchmark",
                "subject",
                "rights",
                "observed_at",
                "tasks",
            }
        ),
    )
    if root["schema_version"] != LOCAL_AGENT_SUMMARY_SCHEMA:
        raise LocalAgentBenchmarkAdapterError("unsupported summary schema_version")
    if root["contains_prompts_or_model_messages"] is not False:
        raise LocalAgentBenchmarkAdapterError("summary is not marked prompt-free")

    benchmark = _object(
        root["benchmark"],
        field="benchmark",
        required=frozenset(
            {
                "kind",
                "dataset_id",
                "dataset_revision",
                "split",
                "task_manifest_sha256",
                "task_count",
                "task_set_kind",
                "harness",
                "scorer",
                "protocol",
                "cohort",
            }
        ),
    )
    try:
        kind = LocalAgentBenchmarkKind(benchmark["kind"])
    except (TypeError, ValueError) as exc:
        raise LocalAgentBenchmarkAdapterError("benchmark.kind is unsupported") from exc
    dataset_id = _string(benchmark["dataset_id"], field="benchmark.dataset_id")
    dataset_revision = _string(benchmark["dataset_revision"], field="benchmark.dataset_revision")
    split = _string(benchmark["split"], field="benchmark.split")
    task_manifest_sha256 = _sha256(
        benchmark["task_manifest_sha256"], field="benchmark.task_manifest_sha256"
    )
    declared_task_count = _count(
        benchmark["task_count"], field="benchmark.task_count", positive=True
    )
    task_set_kind = _string(benchmark["task_set_kind"], field="benchmark.task_set_kind")
    if task_set_kind not in {"full", "screen"}:
        raise LocalAgentBenchmarkAdapterError("benchmark.task_set_kind must be full or screen")
    benchmark_name, full_task_count = _BENCHMARK_IDENTITIES[kind]
    if task_set_kind == "full" and declared_task_count != full_task_count:
        raise LocalAgentBenchmarkAdapterError(
            "full benchmark task_count does not match the reviewed benchmark identity"
        )
    cohort = _cohort(benchmark["cohort"], kind=kind, retrieved_at=retrieved_at)
    harness = _component(benchmark["harness"], field="benchmark.harness")
    scorer = _component(benchmark["scorer"], field="benchmark.scorer")
    protocol = _component(benchmark["protocol"], field="benchmark.protocol")

    observed_at = _timestamp(root["observed_at"], field="observed_at")
    if observed_at > retrieved_at:
        raise LocalAgentBenchmarkAdapterError("observed_at cannot be later than retrieved_at")
    if kind is LocalAgentBenchmarkKind.BROWSECOMP:
        window_started = _timestamp(
            cohort["execution_window_started_at"],
            field="benchmark.cohort.execution_window_started_at",
        )
        window_finished = _timestamp(
            cohort["execution_window_finished_at"],
            field="benchmark.cohort.execution_window_finished_at",
        )
        if not window_started <= observed_at <= window_finished:
            raise LocalAgentBenchmarkAdapterError(
                "BrowseComp observed_at must fall inside its execution window"
            )
    tasks = root["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        raise LocalAgentBenchmarkAdapterError("tasks must be a bounded non-empty array")
    if len(tasks) != declared_task_count:
        raise LocalAgentBenchmarkAdapterError("task_count does not match task records")

    task_digests: set[str] = set()
    scores: list[Decimal] = []
    walls: list[Decimal] = []
    tool_passed = tool_total = grounding_passed = grounding_total = 0
    task_required = frozenset({"task_sha256", "score_percent", "wall_seconds", "tool_checks"})
    if kind is LocalAgentBenchmarkKind.RESEARCHCLAWBENCH:
        task_required |= {"grounding_checks"}
    for index, raw_task in enumerate(tasks):
        task = _object(raw_task, field=f"tasks[{index}]", required=task_required)
        digest = _sha256(task["task_sha256"], field=f"tasks[{index}].task_sha256")
        if digest in task_digests:
            raise LocalAgentBenchmarkAdapterError("task digests must be unique")
        task_digests.add(digest)
        score = _decimal(
            task["score_percent"],
            field=f"tasks[{index}].score_percent",
            maximum=Decimal(100),
        )
        if kind is not LocalAgentBenchmarkKind.RESEARCHCLAWBENCH and score not in {
            Decimal(0),
            Decimal(100),
        }:
            raise LocalAgentBenchmarkAdapterError("BrowseComp and GAIA task scores must be binary")
        scores.append(score)
        walls.append(_decimal(task["wall_seconds"], field=f"tasks[{index}].wall_seconds"))
        passed, total = _checks(task["tool_checks"], field=f"tasks[{index}].tool_checks")
        tool_passed += passed
        tool_total += total
        if kind is LocalAgentBenchmarkKind.RESEARCHCLAWBENCH:
            passed, total = _checks(
                task["grounding_checks"], field=f"tasks[{index}].grounding_checks"
            )
            grounding_passed += passed
            grounding_total += total

    task_digest_set_sha256 = hashlib.sha256(
        "\n".join(sorted(task_digests)).encode("ascii")
    ).hexdigest()
    if task_digest_set_sha256 != task_manifest_sha256:
        raise LocalAgentBenchmarkAdapterError(
            "task_manifest_sha256 does not match the canonical task digest set"
        )

    with localcontext(POLICY_DECIMAL_CONTEXT):
        quality = sum(scores, Decimal(0)) / Decimal(len(scores))
    p95_wall = _quantile_type7(walls, Decimal("0.95"))
    tool_percent = _percent(tool_passed, tool_total, field="tool checks")
    quality_signal = _QUALITY_SIGNALS[kind]
    measurements = [
        QualityMeasurement(
            id=quality_signal,
            role=QualityMeasurementRole.QUALITY,
            value=quality,
            unit="percent",
            sample_count=len(scores),
        ),
        QualityMeasurement(
            id="local_tool_call_success_percent",
            role=QualityMeasurementRole.OTHER,
            value=tool_percent,
            lower=tool_percent,
            upper=tool_percent,
            unit="percent",
            sample_count=tool_total,
        ),
        QualityMeasurement(
            id="p95_agent_task_wall_seconds",
            role=QualityMeasurementRole.LATENCY,
            value=p95_wall,
            unit="s",
            sample_count=len(walls),
        ),
    ]
    if kind is LocalAgentBenchmarkKind.RESEARCHCLAWBENCH:
        grounding_percent = _percent(grounding_passed, grounding_total, field="grounding checks")
        measurements.append(
            QualityMeasurement(
                id="research_evidence_grounding_success_percent",
                role=QualityMeasurementRole.OTHER,
                value=grounding_percent,
                lower=grounding_percent,
                upper=grounding_percent,
                unit="percent",
                sample_count=grounding_total,
            )
        )

    subject_input = _object(
        root["subject"],
        field="subject",
        required=frozenset(
            {
                "row_id",
                "system_label",
                "model_id",
                "model_revision",
                "artifact_sha256",
                "quantization",
                "runtime_id",
                "runtime_version",
                "runtime_configuration_sha256",
                "benchmark_agent",
                "reasoning_claims",
                "attempt_claims",
            }
        ),
    )
    reasoning_claims = _object(
        subject_input["reasoning_claims"],
        field="subject.reasoning_claims",
        required=frozenset(),
        optional=frozenset({"mode", "budget", "thinking_preserved"}),
    )
    normalized_reasoning_claims: dict[str, Any] = {}
    if "mode" in reasoning_claims:
        normalized_reasoning_claims["mode"] = _string(
            reasoning_claims["mode"], field="subject.reasoning_claims.mode"
        )
    if "budget" in reasoning_claims:
        normalized_reasoning_claims["budget"] = _count(
            reasoning_claims["budget"], field="subject.reasoning_claims.budget"
        )
    if "thinking_preserved" in reasoning_claims:
        thinking_preserved = reasoning_claims["thinking_preserved"]
        if not isinstance(thinking_preserved, bool):
            raise LocalAgentBenchmarkAdapterError(
                "subject.reasoning_claims.thinking_preserved must be boolean"
            )
        normalized_reasoning_claims["thinking_preserved"] = thinking_preserved
    attempt_claims = _object(
        subject_input["attempt_claims"],
        field="subject.attempt_claims",
        required=frozenset({"concurrency", "attempts_per_task"}),
    )
    concurrency = _count(
        attempt_claims["concurrency"],
        field="subject.attempt_claims.concurrency",
        positive=True,
    )
    attempts_per_task = _count(
        attempt_claims["attempts_per_task"],
        field="subject.attempt_claims.attempts_per_task",
        positive=True,
    )
    if attempts_per_task != 1:
        raise LocalAgentBenchmarkAdapterError(
            "summary v1 requires exactly one recorded attempt per task"
        )
    subject = QualitySubjectIdentity(
        row_id=_string(subject_input["row_id"], field="subject.row_id"),
        kind=QualitySubjectKind.SINGLE_MODEL_SYSTEM,
        system_label=_string(subject_input["system_label"], field="subject.system_label"),
        model_claims=(
            QualityModelClaim(
                model_id=_string(subject_input["model_id"], field="subject.model_id"),
                revision=_string(subject_input["model_revision"], field="subject.model_revision"),
                claims={
                    "artifact_sha256": _sha256(
                        subject_input["artifact_sha256"], field="subject.artifact_sha256"
                    ),
                    "quantization": _string(
                        subject_input["quantization"], field="subject.quantization"
                    ),
                    "runtime_id": _string(subject_input["runtime_id"], field="subject.runtime_id"),
                    "runtime_version": _string(
                        subject_input["runtime_version"], field="subject.runtime_version"
                    ),
                    "runtime_configuration_sha256": _sha256(
                        subject_input["runtime_configuration_sha256"],
                        field="subject.runtime_configuration_sha256",
                    ),
                },
            ),
        ),
        benchmark_agent=_component(
            subject_input["benchmark_agent"], field="subject.benchmark_agent"
        ),
        route_disclosure=QualityRouteDisclosure.EXACT,
        reasoning_claims=normalized_reasoning_claims,
        attempt_claims={
            "concurrency": concurrency,
            "attempts_per_task": attempts_per_task,
        },
    )

    rights_input = _object(
        root["rights"],
        field="rights",
        required=frozenset(
            {
                "license_expression",
                "terms_locator",
                "publication_permission",
                "reviewed_at",
                "review_evidence",
            }
        ),
    )
    try:
        permission = QualityPublicationPermission(rights_input["publication_permission"])
    except (TypeError, ValueError) as exc:
        raise LocalAgentBenchmarkAdapterError("rights.publication_permission is invalid") from exc
    if (
        kind in {LocalAgentBenchmarkKind.BROWSECOMP, LocalAgentBenchmarkKind.GAIA}
        and permission is QualityPublicationPermission.UNRESTRICTED
    ):
        raise LocalAgentBenchmarkAdapterError(
            "BrowseComp and GAIA summaries cannot claim unrestricted publication"
        )
    reviewed_at = _timestamp(rights_input["reviewed_at"], field="rights.reviewed_at")
    if reviewed_at > retrieved_at:
        raise LocalAgentBenchmarkAdapterError("rights.reviewed_at cannot be later than retrieval")

    source_identity = QualitySourceIdentity(
        source_id=f"local-real-world-agent/{kind.value}",
        source_version=dataset_revision,
        benchmark=QualityComponentIdentity(id=benchmark_name, version=dataset_revision),
        dataset=QualityComponentIdentity(id=dataset_id, version=dataset_revision),
        split=split,
        evaluator_harness=harness,
        scorer=scorer,
        protocol=protocol,
        projection=QualityComponentIdentity(
            id=LOCAL_AGENT_ADAPTER_ID,
            version=LOCAL_AGENT_ADAPTER_VERSION,
        ),
        scope={
            "benchmark_kind": kind.value,
            "task_manifest_sha256": task_manifest_sha256,
            "task_count": declared_task_count,
            "task_set_kind": task_set_kind,
            "full_task_count": full_task_count,
            "cohort": cohort,
        },
    )
    result = QualityResult(
        primary_metric=quality_signal,
        measurements=tuple(measurements),
        counts=tuple(
            [
                QualityCount(
                    id="scored_tasks",
                    role=QualityMeasurementRole.QUALITY,
                    value=len(scores),
                ),
                QualityCount(
                    id="tool_checks",
                    role=QualityMeasurementRole.OTHER,
                    value=tool_total,
                ),
            ]
            + (
                [
                    QualityCount(
                        id="correct_tasks",
                        role=QualityMeasurementRole.QUALITY,
                        value=sum(score == Decimal(100) for score in scores),
                    )
                ]
                if kind is not LocalAgentBenchmarkKind.RESEARCHCLAWBENCH
                else []
            )
        ),
        observed_at=observed_at,
        metadata={
            "aggregate_recomputed_from_prompt_free_task_records": True,
            "task_digest_set_sha256": task_digest_set_sha256,
            "p95_method": "Hyndman-Fan type 7",
        },
    )
    return QualityEvidenceSet(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        raw_audit=QualityRawAudit(
            source_locator=_string(source_locator, field="source_locator", maximum=4_096),
            raw_sha256=quality_raw_sha256(raw),
            retrieved_at=retrieved_at,
            upstream_revision=dataset_revision,
            capture_method="operator-prompt-free-summary",
            parser_implementation=QualityComponentIdentity(
                id=LOCAL_AGENT_ADAPTER_ID,
                version=LOCAL_AGENT_ADAPTER_VERSION,
            ),
            metadata={
                "benchmark_kind": kind.value,
                "contains_prompts_or_model_messages": False,
                "task_count": len(tasks),
            },
        ),
        source_identity=source_identity,
        rights=QualityRights(
            license_expression=_string(
                rights_input["license_expression"], field="rights.license_expression"
            ),
            terms_locator=_string(
                rights_input["terms_locator"], field="rights.terms_locator", maximum=4_096
            ),
            publication_permission=permission,
            reviewed_at=reviewed_at,
            review_evidence=_string(
                rights_input["review_evidence"], field="rights.review_evidence"
            ),
            metadata={"task_contents_omitted": True},
        ),
        rows=(QualityEvidenceRow(subject=subject, result=result),),
    )


def normalize_local_agent_benchmark_file(
    path: Path,
    *,
    retrieved_at: datetime,
) -> QualityEvidenceSet:
    """Load one regular-file summary and preserve its path only as audit provenance."""

    if path.is_symlink() or not path.is_file():
        raise LocalAgentBenchmarkAdapterError("summary input must be a regular non-symlink file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise LocalAgentBenchmarkAdapterError(f"summary exceeds {MAX_INPUT_BYTES} bytes")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LocalAgentBenchmarkAdapterError("summary input could not be read") from exc
    return normalize_local_agent_benchmark_bytes(
        raw,
        retrieved_at=retrieved_at,
        source_locator=str(path),
    )
