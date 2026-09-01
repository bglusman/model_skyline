"""Pinned, fail-closed ARC-AGI-2 public-evaluation evidence collector.

The official Hugging Face dataset contains full attempt transcripts as well as
small ``*/results.json`` summaries.  This adapter fetches only the revision API
and the 32 reviewed summary paths at one immutable revision.  It never fetches,
stores, or publishes attempt content.  Folder names are retained only as opaque,
unreviewed system labels; they are never parsed into provider or route identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import time as time_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx

from model_skyline.adapters._publication import BundlePublicationError, publish_text_bundle
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes, content_hash
from model_skyline.io import dump_json
from model_skyline.models import MAX_SAFE_INTEGER, bounded_canonical_decimal
from model_skyline.quality_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    QualityComponentIdentity,
    QualityCount,
    QualityEvidenceRow,
    QualityEvidenceSet,
    QualityInvalidResult,
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

ARC_AGI_HF_DATASET_ID: Final = "arcprize/arc_agi_v2_public_eval"
ARC_AGI_HF_REVISION: Final = "026789c1c12a4c34580a32e84dcaf5630d7e8f31"
ARC_AGI_HF_LAST_MODIFIED: Final = datetime(2026, 6, 4, 16, 45, 3, tzinfo=UTC)
ARC_AGI_HF_REVISION_API: Final = (
    "https://huggingface.co/api/datasets/arcprize/arc_agi_v2_public_eval/"
    f"revision/{ARC_AGI_HF_REVISION}"
)
ARC_AGI_HF_RESOLVE_PREFIX: Final = (
    "https://huggingface.co/api/resolve-cache/datasets/"
    f"arcprize/arc_agi_v2_public_eval/{ARC_AGI_HF_REVISION}/"
)
ARC_AGI_HF_DATASET_URL: Final = (
    f"https://huggingface.co/datasets/arcprize/arc_agi_v2_public_eval/tree/{ARC_AGI_HF_REVISION}"
)

ARC_AGI_TASK_REPOSITORY: Final = "arcprize/ARC-AGI-2"
ARC_AGI_TASK_COMMIT: Final = "f3283f727488ad98fe575ea6a5ac981e4a188e49"
ARC_AGI_TASK_EVALUATION_TREE: Final = "8d04288aac3146b7c47d0b799c18bc9c0217d838"
ARC_AGI_TASK_URL: Final = f"https://github.com/{ARC_AGI_TASK_REPOSITORY}/tree/{ARC_AGI_TASK_COMMIT}"
ARC_AGI_TASK_LICENSE_URL: Final = (
    f"https://github.com/{ARC_AGI_TASK_REPOSITORY}/blob/{ARC_AGI_TASK_COMMIT}/LICENSE"
)
ARC_AGI_HARNESS_REPOSITORY: Final = "arcprize/arc-agi-benchmarking"
ARC_AGI_HARNESS_COMMIT: Final = "28e67d54b05df5be10281892243c509a42a874f1"
ARC_AGI_HARNESS_URL: Final = (
    f"https://github.com/{ARC_AGI_HARNESS_REPOSITORY}/tree/{ARC_AGI_HARNESS_COMMIT}"
)
ARC_AGI_HARNESS_LICENSE_URL: Final = (
    f"https://github.com/{ARC_AGI_HARNESS_REPOSITORY}/blob/{ARC_AGI_HARNESS_COMMIT}/LICENSE.md"
)

ARC_AGI_EXPECTED_TASKS: Final = 120
ARC_AGI_TASK_SET_SHA256: Final = "54ca25cdc4444e5669e272e25cbe301bbfe3aa81da8f555126095153aff69425"
ARC_AGI_ADAPTER_ID: Final = "model-skyline/arc-agi-2-public-eval"
ARC_AGI_ADAPTER_VERSION: Final = "1"

ARC_AGI_EXPECTED_RESULT_PATHS: Final = (
    "claude-opus-4-5-20251101-thinking-16k/results.json",
    "claude-opus-4-5-20251101-thinking-1k/results.json",
    "claude-opus-4-5-20251101-thinking-32k/results.json",
    "claude-opus-4-5-20251101-thinking-64k/results.json",
    "claude-opus-4-5-20251101-thinking-8k/results.json",
    "claude-opus-4-5-20251101-thinking-none/results.json",
    "claude-opus-4-6-thinking-120K-high/results.json",
    "claude-opus-4-6-thinking-120K-low/results.json",
    "claude-opus-4-6-thinking-120K-max/results.json",
    "claude-opus-4-6-thinking-120K-medium/results.json",
    "claude-opus-4-8-high/results.json",
    "claude-opus-4-8-low/results.json",
    "claude-opus-4-8-max/results.json",
    "claude-opus-4-8-medium/results.json",
    "gemini-3-1-pro-preview/results.json",
    "gemini-3-deep-think-preview/results.json",
    "gemini-3-flash-preview-thinking-high/results.json",
    "gemini-3-flash-preview-thinking-low/results.json",
    "gemini-3-flash-preview-thinking-medium/results.json",
    "gemini-3-flash-preview-thinking-minimal/results.json",
    "gemini-3-pro-preview/results.json",
    "gpt-5-1-2025-11-13-thinking-high/results.json",
    "gpt-5-1-2025-11-13-thinking-low/results.json",
    "gpt-5-1-2025-11-13-thinking-medium/results.json",
    "gpt-5-1-2025-11-13-thinking-none/results.json",
    "gpt-5-2-2025-12-11-thinking-high/results.json",
    "gpt-5-2-2025-12-11-thinking-low/results.json",
    "gpt-5-2-2025-12-11-thinking-medium/results.json",
    "gpt-5-2-2025-12-11-thinking-none/results.json",
    "gpt-5-2-2025-12-11-thinking-xhigh/results.json",
    "gpt-5-2-pro-2025-12-11-high/results.json",
    "gpt-5-2-pro-2025-12-11-medium/results.json",
)

DEFAULT_TIMEOUT_SECONDS: Final = 30.0
MAX_TIMEOUT_SECONDS: Final = 60.0
MAX_REVISION_BYTES: Final = 2_000_000
MAX_RESULT_BYTES: Final = 2_000_000
MAX_TOTAL_RESULT_BYTES: Final = 32_000_000
MAX_JSON_DEPTH: Final = 20
MAX_JSON_NODES: Final = 500_000
MAX_JSON_STRING_LENGTH: Final = 65_536
MAX_SIBLINGS: Final = 20_000
MAX_ACCOUNTING_VALUE: Final = Decimal("1e24")
SCORE_TOLERANCE: Final = Decimal("0.000000000001")
ACCOUNTING_TOLERANCE: Final = Decimal("0.000000001")

EVIDENCE_FILENAME: Final = "quality-evidence.json"
INVENTORY_FILENAME: Final = "inventory.json"
MANIFEST_FILENAME: Final = "capture.json"

_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_TASK_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_RESULT_QUALITY_FIELDS = frozenset({"score", "total_tasks", "task_results"})
_RESULT_ACCOUNTING_FIELDS = frozenset(
    {
        "avg_cost_per_attempt",
        "avg_cost_per_task",
        "avg_duration_per_task",
        "avg_output_tokens_per_task",
        "avg_prompt_tokens_per_task",
        "avg_reasoning_tokens_per_task",
        "avg_total_tokens_per_task",
        "num_attempts_with_empty_list",
        "total_attempts",
        "total_cost",
    }
)
_TASK_ALLOWED_FIELDS = frozenset(
    {
        "attempts",
        "cost",
        "duration",
        "num_attempts_with_empty_list",
        "output_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "score",
        "total_tokens",
    }
)
_RIGHTS_REVIEWED_AT = datetime(2026, 8, 31, tzinfo=UTC)


class ArcAgiAdapterError(ValueError):
    """The pinned ARC-AGI-2 source cannot be acquired or normalized safely."""


class _ArcAgiCaptureTrust(StrEnum):
    OPERATOR_RAW_BOUND = "operator_raw_bound"
    OFFICIAL_FIXED_NETWORK = "official_fixed_network"


@dataclass(frozen=True, slots=True)
class LoadedArcAgiSource:
    revision_metadata: bytes
    result_files: tuple[tuple[str, bytes], ...]
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class ArcAgiCapture:
    evidence: QualityEvidenceSet
    revision_metadata_sha256: str
    result_file_sha256: tuple[tuple[str, str], ...]
    rows_seen: int
    valid_rows: int
    invalid_rows: int

    def inventory(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for row in self.evidence.rows:
            rows.append(
                {
                    "row_id": row.row_id,
                    "subject_identity_sha256": row.subject_identity_sha256,
                    "system_label": row.subject.system_label,
                    "route_disclosure": row.subject.route_disclosure.value,
                    "result": (
                        {"status": "valid", "result_sha256": row.result_sha256}
                        if row.result is not None
                        else {
                            "status": "invalid",
                            "code": (row.invalid_result.code if row.invalid_result else "invalid"),
                            "result_sha256": row.result_sha256,
                        }
                    ),
                }
            )
        return {
            "schema_version": "model-skyline/arc-agi-2-inventory/v1alpha1",
            "adapter_id": ARC_AGI_ADAPTER_ID,
            "adapter_version": ARC_AGI_ADAPTER_VERSION,
            "raw_audit_sha256": self.evidence.raw_audit_sha256,
            "source_identity_sha256": self.evidence.source_identity_sha256,
            "rights_sha256": self.evidence.rights_sha256,
            "cohort": {
                "split": "public-evaluation",
                "expected_tasks": ARC_AGI_EXPECTED_TASKS,
                "task_set_sha256": ARC_AGI_TASK_SET_SHA256,
            },
            "counts": {
                "seen": self.rows_seen,
                "valid": self.valid_rows,
                "invalid": self.invalid_rows,
            },
            "rows": rows,
        }

    def manifest(self, *, output_sha256: Mapping[str, str] | None = None) -> dict[str, Any]:
        return {
            "schema_version": "model-skyline/arc-agi-2-capture/v1alpha1",
            "adapter_id": ARC_AGI_ADAPTER_ID,
            "adapter_version": ARC_AGI_ADAPTER_VERSION,
            "source": {
                "capture_method": self.evidence.raw_audit.capture_method,
                "dataset_id": ARC_AGI_HF_DATASET_ID,
                "locator": self.evidence.raw_audit.source_locator,
                "revision": ARC_AGI_HF_REVISION,
                "registered_revision_api": ARC_AGI_HF_REVISION_API,
                "revision_metadata_sha256": self.revision_metadata_sha256,
                "retrieved_at": self.evidence.raw_audit.retrieved_at.isoformat(),
                "result_files": [
                    {"path": path, "sha256": digest} for path, digest in self.result_file_sha256
                ],
            },
            "references": {
                "task_repository": {
                    "commit": ARC_AGI_TASK_COMMIT,
                    "evaluation_tree": ARC_AGI_TASK_EVALUATION_TREE,
                    "role": "expected-target-not-generation-attestation",
                    "url": ARC_AGI_TASK_URL,
                },
                "evaluator_harness": {
                    "commit": ARC_AGI_HARNESS_COMMIT,
                    "role": "schema-reference-not-generation-attestation",
                    "url": ARC_AGI_HARNESS_URL,
                },
            },
            "source_identity_sha256": self.evidence.source_identity_sha256,
            "rights": self.evidence.rights.model_dump(mode="json"),
            "cohort": {
                "expected_tasks": ARC_AGI_EXPECTED_TASKS,
                "task_set_sha256": ARC_AGI_TASK_SET_SHA256,
                "task_digest_algorithm": "sha256(sorted-task-id + newline)",
            },
            "rows": {
                "seen": self.rows_seen,
                "valid": self.valid_rows,
                "invalid": self.invalid_rows,
            },
            "outputs": {
                "evidence": EVIDENCE_FILENAME,
                "inventory": INVENTORY_FILENAME,
                **({"sha256": dict(output_sha256)} if output_sha256 is not None else {}),
            },
            "warnings": [
                "Folder names yield only dataset-path model claims; they are not provider, "
                "endpoint, harness, or OfferingKey assertions.",
                "Only exact 120-task rows with the pinned task-set digest and a recomputed "
                "aggregate score become valid quality results.",
                "The task-ID digest does not attest task contents; the task and harness "
                "commits are reference-only, and the HF revision is a conservative source pin.",
                "Caller-supplied normalization is operator-labeled and raw-bound; only the "
                "fixed network capture path receives registered-source semantics.",
                "Accounting is retained only by independently coherent dimension; zero or "
                "invalid reported cost is unknown rather than free.",
                "Dataset lastModified is an upload/publication observation, not a run time.",
                "Attempt content, raw result content, and task identifiers are not retained.",
                "Publication permission remains unknown pending an operator rights review.",
            ],
        }


class _DuplicateJsonKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError


def _parse_decimal(value: str) -> Decimal:
    if len(value) > 1_024:
        raise ValueError
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError from exc


def _parse_integer(value: str) -> int:
    if len(value) > 1_024:
        raise ValueError
    return int(value)


def _preflight_json(raw: bytes, *, label: str, maximum: int) -> None:
    if not isinstance(raw, bytes):
        raise ArcAgiAdapterError(f"{label} must be bytes")
    if len(raw) > maximum:
        raise ArcAgiAdapterError(f"{label} exceeds the {maximum}-byte limit")
    depth = 0
    nodes = 1
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in {0x7B, 0x5B}:
            depth += 1
            nodes += 1
            if depth > MAX_JSON_DEPTH:
                raise ArcAgiAdapterError(f"{label} exceeds the JSON nesting limit")
        elif byte in {0x3A, 0x2C}:
            nodes += 1
            if nodes > MAX_JSON_NODES:
                raise ArcAgiAdapterError(f"{label} exceeds the JSON structural token limit")
        elif byte in {0x7D, 0x5D}:
            depth -= 1
            if depth < 0:
                break


def _validate_json_shape(value: Any, *, label: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ArcAgiAdapterError(f"{label} exceeds the JSON structural token limit")
        if depth > MAX_JSON_DEPTH:
            raise ArcAgiAdapterError(f"{label} exceeds the JSON nesting limit")
        if isinstance(current, Mapping):
            for key, child in current.items():
                if not isinstance(key, str) or len(key) > MAX_JSON_STRING_LENGTH:
                    raise ArcAgiAdapterError(f"{label} contains an invalid object key")
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str) and len(current) > MAX_JSON_STRING_LENGTH:
            raise ArcAgiAdapterError(f"{label} contains an oversized string")


def _decode_json(raw: bytes, *, label: str, maximum: int) -> Any:
    _preflight_json(raw, label=label, maximum=maximum)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
        RecursionError,
        MemoryError,
    ):
        raise ArcAgiAdapterError(f"{label} is not valid bounded duplicate-key-free JSON") from None
    _validate_json_shape(value, label=label)
    return value


def _timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ArcAgiAdapterError(f"{field} must be a bounded ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ArcAgiAdapterError(f"{field} must be an ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ArcAgiAdapterError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _validated_revision_metadata(raw: bytes) -> datetime:
    value = _decode_json(raw, label="ARC-AGI revision metadata", maximum=MAX_REVISION_BYTES)
    if not isinstance(value, Mapping):
        raise ArcAgiAdapterError("ARC-AGI revision metadata must be an object")
    if value.get("id") != ARC_AGI_HF_DATASET_ID or value.get("author") != "arcprize":
        raise ArcAgiAdapterError("ARC-AGI revision metadata identifies another dataset")
    if value.get("sha") != ARC_AGI_HF_REVISION:
        raise ArcAgiAdapterError("ARC-AGI revision metadata does not match the pinned revision")
    if value.get("private") is not False or value.get("gated") is not False:
        raise ArcAgiAdapterError("pinned ARC-AGI dataset is no longer public and ungated")
    if value.get("disabled") is not False:
        raise ArcAgiAdapterError("pinned ARC-AGI dataset is disabled")
    tags = value.get("tags")
    if not isinstance(tags, list) or "license:mit" not in tags:
        raise ArcAgiAdapterError("ARC-AGI revision metadata omits the reviewed MIT dataset tag")
    card = value.get("cardData")
    if not isinstance(card, Mapping) or card.get("license") != "mit":
        raise ArcAgiAdapterError("ARC-AGI dataset card license drifted")
    siblings = value.get("siblings")
    if not isinstance(siblings, list) or len(siblings) > MAX_SIBLINGS:
        raise ArcAgiAdapterError("ARC-AGI revision siblings must be a bounded array")
    result_paths: list[str] = []
    for sibling in siblings:
        if not isinstance(sibling, Mapping) or set(sibling) != {"rfilename"}:
            raise ArcAgiAdapterError("ARC-AGI revision sibling schema drifted")
        filename = sibling.get("rfilename")
        if not isinstance(filename, str) or not filename or len(filename) > 512:
            raise ArcAgiAdapterError("ARC-AGI revision contains an invalid sibling path")
        if filename.endswith("/results.json"):
            result_paths.append(filename)
    if tuple(sorted(result_paths)) != ARC_AGI_EXPECTED_RESULT_PATHS:
        raise ArcAgiAdapterError("ARC-AGI revision result-path cohort drifted")
    last_modified = _timestamp(value.get("lastModified"), field="lastModified")
    if last_modified != ARC_AGI_HF_LAST_MODIFIED:
        raise ArcAgiAdapterError("ARC-AGI revision publication timestamp drifted")
    return last_modified


def _folder(path: str) -> str:
    suffix = "/results.json"
    if not path.endswith(suffix):
        raise ArcAgiAdapterError("ARC-AGI result path is invalid")
    folder = path[: -len(suffix)]
    if _FOLDER_RE.fullmatch(folder) is None:
        raise ArcAgiAdapterError("ARC-AGI result folder is not a safe opaque identifier")
    return folder


def _subject(path: str) -> QualitySubjectIdentity:
    folder = _folder(path)
    return QualitySubjectIdentity(
        row_id=f"arc-agi-2/public-eval/{folder}",
        kind=QualitySubjectKind.SINGLE_MODEL_SYSTEM,
        system_label=folder,
        model_claims=(
            QualityModelClaim(
                model_id=folder,
                display_name=folder,
                claims={
                    "claim_source": "hugging-face-dataset-folder",
                    "dataset_path_only": True,
                    "identity_reviewed": False,
                    "not_a_route_assertion": True,
                },
            ),
        ),
        benchmark_agent=None,
        route_disclosure=QualityRouteDisclosure.UNKNOWN,
        reasoning_claims={"folder_label_is_unreviewed": True},
        attempt_claims={"folder_label_is_not_attempt_policy": True},
    )


def _invalid(code: str, detail: str, *, raw_sha256: str) -> QualityInvalidResult:
    return QualityInvalidResult(
        code=code,
        detail=detail,
        selected_value_sha256=raw_sha256,
    )


def _decimal(
    value: Any,
    *,
    minimum: Decimal = Decimal(0),
    maximum: Decimal = MAX_ACCOUNTING_VALUE,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError
    try:
        parsed = bounded_canonical_decimal(Decimal(value))
    except (InvalidOperation, ValueError):
        raise ValueError from None
    if not minimum <= parsed <= maximum:
        raise ValueError
    return parsed


def _count(value: Any, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= MAX_SAFE_INTEGER
    ):
        raise ValueError
    return int(value)


def _task_set_sha256(task_ids: Sequence[str]) -> str:
    payload = "".join(f"{task_id}\n" for task_id in sorted(task_ids)).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _close(first: Decimal, second: Decimal, *, tolerance: Decimal) -> bool:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        return (first - second).copy_abs() <= tolerance


def _task_decimals(
    tasks: Sequence[Mapping[str, Any]],
    field: str,
) -> tuple[Decimal, ...] | None:
    try:
        return tuple(_decimal(task.get(field)) for task in tasks)
    except ValueError:
        return None


def _task_counts(
    tasks: Sequence[Mapping[str, Any]],
    field: str,
) -> tuple[int, ...] | None:
    try:
        return tuple(_count(task.get(field)) for task in tasks)
    except ValueError:
        return None


def _average_matches(
    values: Sequence[Decimal | int],
    supplied: Any,
    *,
    denominator: int = ARC_AGI_EXPECTED_TASKS,
) -> tuple[bool, Decimal]:
    with localcontext(POLICY_DECIMAL_CONTEXT):
        total = sum((Decimal(value) for value in values), Decimal(0))
        average = total / Decimal(denominator)
    try:
        source_average = _decimal(supplied)
    except ValueError:
        return False, average
    return _close(source_average, average, tolerance=ACCOUNTING_TOLERANCE), average


def _accounting_measurements(
    document: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[list[QualityMeasurement], list[QualityCount], dict[str, bool]]:
    measurements: list[QualityMeasurement] = []
    counts: list[QualityCount] = []
    status: dict[str, bool] = {}

    attempts = _task_counts(tasks, "attempts")
    attempts_coherent = False
    total_attempts = 0
    if attempts is not None:
        total_attempts = sum(attempts)
        try:
            source_total_attempts = _count(document.get("total_attempts"))
        except ValueError:
            source_total_attempts = -1
        attempts_coherent = (
            total_attempts <= MAX_SAFE_INTEGER and source_total_attempts == total_attempts
        )
    status["attempts"] = attempts_coherent
    if attempts_coherent:
        counts.append(
            QualityCount(
                id="arc_agi_2_reported_total_attempts",
                role=QualityMeasurementRole.OTHER,
                value=total_attempts,
            )
        )

    costs = _task_decimals(tasks, "cost")
    cost_coherent = False
    if costs is not None and attempts_coherent:
        with localcontext(POLICY_DECIMAL_CONTEXT):
            total_cost = sum(costs, Decimal(0))
            average_cost = total_cost / ARC_AGI_EXPECTED_TASKS
            average_attempt_cost = total_cost / total_attempts if total_attempts else Decimal(0)
        try:
            source_total_cost = _decimal(document.get("total_cost"))
            source_average_cost = _decimal(document.get("avg_cost_per_task"))
            source_average_attempt_cost = _decimal(document.get("avg_cost_per_attempt"))
        except ValueError:
            pass
        else:
            cost_coherent = (
                total_attempts > 0
                and total_cost > 0
                and _close(source_total_cost, total_cost, tolerance=ACCOUNTING_TOLERANCE)
                and _close(source_average_cost, average_cost, tolerance=ACCOUNTING_TOLERANCE)
                and _close(
                    source_average_attempt_cost,
                    average_attempt_cost,
                    tolerance=ACCOUNTING_TOLERANCE,
                )
            )
            if cost_coherent:
                measurements.extend(
                    (
                        QualityMeasurement(
                            id="arc_agi_2_reported_cost_per_task_usd",
                            role=QualityMeasurementRole.COST,
                            value=average_cost,
                            unit="USD/task",
                            sample_count=ARC_AGI_EXPECTED_TASKS,
                        ),
                        QualityMeasurement(
                            id="arc_agi_2_reported_total_cost_usd",
                            role=QualityMeasurementRole.COST,
                            value=total_cost,
                            unit="USD",
                            sample_count=ARC_AGI_EXPECTED_TASKS,
                        ),
                    )
                )
    status["cost"] = cost_coherent

    dimensions = (
        (
            "output_tokens",
            "avg_output_tokens_per_task",
            "arc_agi_2_reported_output_tokens_per_task",
            "tokens/task",
            QualityMeasurementRole.TOKEN_USAGE,
            True,
        ),
        (
            "total_tokens",
            "avg_total_tokens_per_task",
            "arc_agi_2_reported_total_tokens_per_task",
            "tokens/task",
            QualityMeasurementRole.TOKEN_USAGE,
            True,
        ),
        (
            "prompt_tokens",
            "avg_prompt_tokens_per_task",
            "arc_agi_2_reported_prompt_tokens_per_task",
            "tokens/task",
            QualityMeasurementRole.TOKEN_USAGE,
            True,
        ),
        (
            "reasoning_tokens",
            "avg_reasoning_tokens_per_task",
            "arc_agi_2_reported_reasoning_tokens_per_task",
            "tokens/task",
            QualityMeasurementRole.TOKEN_USAGE,
            True,
        ),
        (
            "duration",
            "avg_duration_per_task",
            "arc_agi_2_reported_duration_per_task_seconds",
            "seconds/task",
            QualityMeasurementRole.LATENCY,
            False,
        ),
    )
    for task_field, aggregate_field, measurement_id, unit, role, integral in dimensions:
        raw_values: Sequence[Decimal | int] | None = (
            _task_counts(tasks, task_field) if integral else _task_decimals(tasks, task_field)
        )
        coherent = False
        if raw_values is not None:
            coherent, average = _average_matches(raw_values, document.get(aggregate_field))
            if coherent:
                measurements.append(
                    QualityMeasurement(
                        id=measurement_id,
                        role=role,
                        value=average,
                        unit=unit,
                        sample_count=ARC_AGI_EXPECTED_TASKS,
                    )
                )
        status[task_field] = coherent

    empty_counts = _task_counts(tasks, "num_attempts_with_empty_list")
    empty_coherent = False
    if empty_counts is not None:
        total_empty = sum(empty_counts)
        try:
            source_total_empty = _count(document.get("num_attempts_with_empty_list"))
        except ValueError:
            source_total_empty = -1
        empty_coherent = total_empty <= MAX_SAFE_INTEGER and source_total_empty == total_empty
        if empty_coherent:
            counts.append(
                QualityCount(
                    id="arc_agi_2_reported_empty_list_attempts",
                    role=QualityMeasurementRole.OTHER,
                    value=total_empty,
                )
            )
    status["empty_list_attempts"] = empty_coherent
    return measurements, counts, status


def _validated_result(
    raw: bytes,
    *,
    observed_at: datetime,
) -> QualityResult | QualityInvalidResult:
    raw_digest = hashlib.sha256(raw).hexdigest()
    try:
        value = _decode_json(raw, label="ARC-AGI result", maximum=MAX_RESULT_BYTES)
    except ArcAgiAdapterError as exc:
        return _invalid("schema_invalid", str(exc), raw_sha256=raw_digest)
    if not isinstance(value, Mapping):
        return _invalid(
            "schema_invalid",
            "The result document is not an object.",
            raw_sha256=raw_digest,
        )
    supplied_fields = set(value)
    if not supplied_fields >= _RESULT_QUALITY_FIELDS or supplied_fields - (
        _RESULT_QUALITY_FIELDS | _RESULT_ACCOUNTING_FIELDS
    ):
        return _invalid(
            "schema_invalid",
            "The result document has missing or unreviewed fields.",
            raw_sha256=raw_digest,
        )
    raw_tasks = value.get("task_results")
    if not isinstance(raw_tasks, Mapping) or len(raw_tasks) > ARC_AGI_EXPECTED_TASKS:
        return _invalid(
            "schema_invalid",
            "task_results is not a bounded object.",
            raw_sha256=raw_digest,
        )
    task_ids: list[str] = []
    tasks: list[Mapping[str, Any]] = []
    scores: list[Decimal] = []
    outcomes: list[dict[str, str]] = []
    for task_id, task in raw_tasks.items():
        if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
            return _invalid(
                "schema_invalid",
                "A task result has an invalid task identifier.",
                raw_sha256=raw_digest,
            )
        if not isinstance(task, Mapping) or "score" not in task or set(task) - _TASK_ALLOWED_FIELDS:
            return _invalid(
                "schema_invalid",
                "A task result has missing or unreviewed fields.",
                raw_sha256=raw_digest,
            )
        try:
            score = _decimal(task.get("score"), maximum=Decimal(1))
        except ValueError:
            return _invalid(
                "schema_invalid",
                "A task score is outside the reviewed zero-to-one domain.",
                raw_sha256=raw_digest,
            )
        task_ids.append(task_id)
        tasks.append(task)
        scores.append(score)
        outcomes.append({"task_id": task_id, "score": format(score, "f")})
    try:
        declared_tasks = _count(value.get("total_tasks"))
    except ValueError:
        return _invalid(
            "schema_invalid",
            "total_tasks is not a nonnegative integer.",
            raw_sha256=raw_digest,
        )
    if declared_tasks != len(tasks) or len(tasks) != ARC_AGI_EXPECTED_TASKS:
        return _invalid(
            "incomplete_task_cohort",
            "The result does not contain the exact 120-task public-evaluation cohort.",
            raw_sha256=raw_digest,
        )
    task_set_digest = _task_set_sha256(task_ids)
    if task_set_digest != ARC_AGI_TASK_SET_SHA256:
        return _invalid(
            "task_set_mismatch",
            "The result task identifiers do not match the pinned public-evaluation set.",
            raw_sha256=raw_digest,
        )
    with localcontext(POLICY_DECIMAL_CONTEXT):
        score_sum = sum(scores, Decimal(0))
        score_percent = score_sum * Decimal(100) / ARC_AGI_EXPECTED_TASKS
    try:
        source_score = _decimal(value.get("score"), maximum=Decimal(ARC_AGI_EXPECTED_TASKS))
    except ValueError:
        return _invalid(
            "schema_invalid",
            "The aggregate source score is invalid.",
            raw_sha256=raw_digest,
        )
    if not _close(source_score, score_sum, tolerance=SCORE_TOLERANCE):
        return _invalid(
            "aggregate_score_mismatch",
            "The aggregate source score disagrees with recomputed per-task results.",
            raw_sha256=raw_digest,
        )

    accounting_measurements, accounting_counts, accounting_status = _accounting_measurements(
        value, tasks
    )
    fully_solved = sum(score == Decimal(1) for score in scores)
    measurements = [
        QualityMeasurement(
            id="arc_agi_2_public_eval_score_percent",
            role=QualityMeasurementRole.QUALITY,
            value=score_percent,
            unit="percent",
            sample_count=ARC_AGI_EXPECTED_TASKS,
        ),
        *accounting_measurements,
    ]
    counts = [
        QualityCount(
            id="arc_agi_2_evaluated_tasks",
            role=QualityMeasurementRole.QUALITY,
            value=ARC_AGI_EXPECTED_TASKS,
        ),
        QualityCount(
            id="arc_agi_2_fully_solved_tasks",
            role=QualityMeasurementRole.QUALITY,
            value=fully_solved,
        ),
        *accounting_counts,
    ]
    return QualityResult(
        primary_metric="arc_agi_2_public_eval_score_percent",
        measurements=tuple(measurements),
        counts=tuple(counts),
        observed_at=observed_at,
        metadata={
            "accounting_coherent": accounting_status,
            "aggregate_score_validated_against_per_task": True,
            "observed_at_semantics": "dataset-revision-publication-not-run-time",
            "score_recomputed_from_per_task": True,
            "source_score_is_sum_not_percent": True,
            "task_outcome_sha256": content_hash(
                sorted(outcomes, key=lambda outcome: outcome["task_id"])
            ),
            "task_set_sha256": task_set_digest,
        },
    )


def _default_rights() -> QualityRights:
    return QualityRights(
        license_expression="MIT AND Apache-2.0",
        terms_locator=ARC_AGI_HF_DATASET_URL,
        publication_permission=QualityPublicationPermission.UNKNOWN,
        reviewed_at=_RIGHTS_REVIEWED_AT,
        review_evidence=(
            "The pinned Hugging Face result dataset and benchmarking harness declare MIT; "
            "the pinned ARC-AGI-2 task repository declares Apache-2.0. Publication remains "
            "unknown pending operator review of dataset-card applicability, attribution, and "
            "the intended downstream projection."
        ),
        metadata={
            "component_licenses": {
                "harness": "MIT",
                "result_dataset": "MIT",
                "task_data": "Apache-2.0",
            },
            "harness_license_url": ARC_AGI_HARNESS_LICENSE_URL,
            "task_license_url": ARC_AGI_TASK_LICENSE_URL,
        },
    )


def _unreviewed_operator_rights() -> QualityRights:
    return QualityRights(
        license_expression="NOASSERTION",
        terms_locator=None,
        publication_permission=QualityPublicationPermission.UNKNOWN,
        reviewed_at=_RIGHTS_REVIEWED_AT,
        review_evidence=(
            "Caller-supplied result bytes have no adapter-attested acquisition provenance "
            "or rights. Publication remains disabled until an operator supplies an explicit "
            "reviewed assertion."
        ),
        metadata={"operator_review_required": True},
    )


def _source_identity(
    *,
    capture_digest: str,
    capture_trust: _ArcAgiCaptureTrust,
) -> QualitySourceIdentity:
    source_version = f"hf-revision/{ARC_AGI_HF_REVISION}"
    if capture_trust is _ArcAgiCaptureTrust.OPERATOR_RAW_BOUND:
        source_version += f"/operator-raw-sha256:{capture_digest}"
    return QualitySourceIdentity(
        source_id="arc-agi-2/public-evaluation",
        # The result summaries cannot attest task bytes. Conservatively bind the
        # immutable publication revision so a future same-ID cohort requires a
        # fresh source review instead of silently inheriting mappings.
        source_version=source_version,
        benchmark=QualityComponentIdentity(
            id="ARC-AGI-2",
            version="2",
            configuration={"benchmark_project": ARC_AGI_TASK_REPOSITORY},
        ),
        dataset=QualityComponentIdentity(
            id="ARC-AGI-2/public-evaluation-task-content",
            version="unattested",
            configuration={
                "expected_tasks": ARC_AGI_EXPECTED_TASKS,
                "observed_task_id_set_sha256": ARC_AGI_TASK_SET_SHA256,
                "result_summaries_attest_task_content": False,
                "task_set_sha256_algorithm": "sha256(sorted-task-id + newline)",
            },
        ),
        split="public-evaluation",
        evaluator_harness=QualityComponentIdentity(
            id="ARC-AGI-2/evaluator-harness",
            version="unattested",
            configuration={
                "result_summaries_attest_harness_revision": False,
            },
        ),
        scorer=QualityComponentIdentity(
            id="arc-agi-2-public-eval-task-score",
            version="1",
            configuration={
                "aggregate": "100 * sum(per_task_score) / 120",
                "unit": "percent",
            },
        ),
        protocol=QualityComponentIdentity(
            id="arc-agi-public-evaluation-result-summary",
            version="unattested-attempt-policy",
            configuration={
                "attempt_policy_attested": False,
                "reported_total_attempts_are_route_free_telemetry": True,
            },
        ),
        projection=QualityComponentIdentity(
            id=ARC_AGI_ADAPTER_ID,
            version=ARC_AGI_ADAPTER_VERSION,
            configuration={
                "aggregate_validation": "per-task-decimal-recomputation",
                "folder_identity": "opaque-unreviewed-label",
                "requires_complete_task_set": True,
            },
        ),
        scope={
            "capture_trust": capture_trust.value,
            "hf_dataset_id": ARC_AGI_HF_DATASET_ID,
            "hf_revision": ARC_AGI_HF_REVISION,
            "hf_revision_semantics": "conservative-source-pin-because-task-content-unattested",
            "expected_tasks": ARC_AGI_EXPECTED_TASKS,
            "task_set_sha256": ARC_AGI_TASK_SET_SHA256,
        },
    )


def _normalize_arc_agi_public_eval_bytes(
    revision_metadata: bytes,
    result_files: Mapping[str, bytes],
    *,
    retrieved_at: datetime,
    rights: QualityRights | None = None,
    capture_trust: _ArcAgiCaptureTrust,
) -> ArcAgiCapture:
    """Normalize one exact multifile capture under an explicit acquisition trust."""

    if retrieved_at.tzinfo is None:
        raise ArcAgiAdapterError("retrieved_at must include a timezone")
    retrieved_at = retrieved_at.astimezone(UTC)
    observed_at = _validated_revision_metadata(revision_metadata)
    if retrieved_at < observed_at:
        raise ArcAgiAdapterError("retrieved_at predates the pinned revision publication")
    if not isinstance(result_files, Mapping) or any(
        not isinstance(path, str) or not isinstance(raw, bytes)
        for path, raw in result_files.items()
    ):
        raise ArcAgiAdapterError("result_files must map paths to bytes")
    if tuple(sorted(result_files)) != ARC_AGI_EXPECTED_RESULT_PATHS:
        raise ArcAgiAdapterError("result_files must exactly match the pinned 32-path cohort")
    total_bytes = sum(len(raw) for raw in result_files.values())
    if total_bytes > MAX_TOTAL_RESULT_BYTES:
        raise ArcAgiAdapterError("ARC-AGI result capture exceeds the aggregate byte limit")

    file_digests = tuple(
        (path, hashlib.sha256(result_files[path]).hexdigest())
        for path in ARC_AGI_EXPECTED_RESULT_PATHS
    )
    revision_digest = hashlib.sha256(revision_metadata).hexdigest()
    capture_index = canonical_bytes(
        {
            "revision_api_sha256": revision_digest,
            "result_files": [{"path": path, "sha256": digest} for path, digest in file_digests],
        }
    )
    capture_digest = quality_raw_sha256(capture_index)
    rows: list[QualityEvidenceRow] = []
    for path in ARC_AGI_EXPECTED_RESULT_PATHS:
        state = _validated_result(result_files[path], observed_at=observed_at)
        rows.append(
            QualityEvidenceRow(
                subject=_subject(path),
                result=state if isinstance(state, QualityResult) else None,
                invalid_result=state if isinstance(state, QualityInvalidResult) else None,
            )
        )
    official_capture = capture_trust is _ArcAgiCaptureTrust.OFFICIAL_FIXED_NETWORK
    selected_rights = rights or (
        _default_rights() if official_capture else _unreviewed_operator_rights()
    )
    if not isinstance(selected_rights, QualityRights):
        raise ArcAgiAdapterError("rights must be a validated QualityRights assertion")
    evidence = QualityEvidenceSet(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        raw_audit=QualityRawAudit(
            source_locator=(
                ARC_AGI_HF_REVISION_API
                if official_capture
                else f"operator-supplied-arc-agi-capture:sha256:{capture_digest}"
            ),
            raw_sha256=capture_digest,
            retrieved_at=retrieved_at,
            upstream_revision=ARC_AGI_HF_REVISION,
            capture_method=(
                "https-get-fixed-hf-revision-multifile"
                if official_capture
                else "operator-supplied-fixed-revision-multifile"
            ),
            parser_implementation=QualityComponentIdentity(
                id=ARC_AGI_ADAPTER_ID,
                version=ARC_AGI_ADAPTER_VERSION,
                configuration={
                    "duplicate_key_rejection": True,
                    "python_json_decimal": True,
                    "result_content_retained": False,
                },
            ),
            metadata={
                "attempt_content_fetched": False,
                "capture_trust": capture_trust.value,
                "expected_result_files": len(ARC_AGI_EXPECTED_RESULT_PATHS),
                "revision_api_sha256": revision_digest,
                "result_file_set_sha256": content_hash(
                    [{"path": path, "sha256": digest} for path, digest in file_digests]
                ),
            },
        ),
        source_identity=_source_identity(
            capture_digest=capture_digest,
            capture_trust=capture_trust,
        ),
        rights=QualityRights.model_validate(selected_rights.model_dump(mode="json")),
        rows=tuple(rows),
    )
    valid_rows = sum(row.result is not None for row in evidence.rows)
    return ArcAgiCapture(
        evidence=evidence,
        revision_metadata_sha256=revision_digest,
        result_file_sha256=file_digests,
        rows_seen=len(evidence.rows),
        valid_rows=valid_rows,
        invalid_rows=len(evidence.rows) - valid_rows,
    )


def normalize_arc_agi_public_eval_bytes(
    revision_metadata: bytes,
    result_files: Mapping[str, bytes],
    *,
    retrieved_at: datetime,
    rights: QualityRights | None = None,
) -> ArcAgiCapture:
    """Normalize operator-supplied bytes with raw-bound, unreviewed provenance."""

    return _normalize_arc_agi_public_eval_bytes(
        revision_metadata,
        result_files,
        retrieved_at=retrieved_at,
        rights=rights,
        capture_trust=_ArcAgiCaptureTrust.OPERATOR_RAW_BOUND,
    )


def normalize_arc_agi_public_eval_source(
    source: LoadedArcAgiSource,
    *,
    rights: QualityRights | None = None,
) -> ArcAgiCapture:
    if not isinstance(source, LoadedArcAgiSource):
        raise ArcAgiAdapterError("source must be a LoadedArcAgiSource")
    return normalize_arc_agi_public_eval_bytes(
        source.revision_metadata,
        dict(source.result_files),
        retrieved_at=source.retrieved_at,
        rights=rights,
    )


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArcAgiAdapterError("timeout_seconds must be a number")
    if not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise ArcAgiAdapterError(
            f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS}"
        )
    return float(value)


def _fixed_result_url(path: str) -> str:
    if path not in ARC_AGI_EXPECTED_RESULT_PATHS:
        raise ArcAgiAdapterError("refusing an unreviewed ARC-AGI result path")
    return ARC_AGI_HF_RESOLVE_PREFIX + quote(path, safe="")


def _fetch(
    client: httpx.Client,
    url: str,
    *,
    maximum: int,
    media_types: frozenset[str],
    require_repo_commit: bool,
    deadline: float | None = None,
) -> bytes:
    remaining = None if deadline is None else deadline - time_module.monotonic()
    if remaining is not None and remaining <= 0:
        raise ArcAgiAdapterError("ARC-AGI capture exceeded its total time limit")
    try:
        with client.stream(
            "GET",
            url,
            headers={
                "Accept": ", ".join(sorted(media_types)),
                "Accept-Encoding": "identity",
                "User-Agent": f"model-skyline/{ARC_AGI_ADAPTER_VERSION} arc-agi-collector",
            },
            timeout=remaining if remaining is not None else client.timeout,
        ) as response:
            if deadline is not None and time_module.monotonic() >= deadline:
                raise ArcAgiAdapterError("ARC-AGI capture exceeded its total time limit")
            if response.is_redirect:
                raise ArcAgiAdapterError("ARC-AGI fixed source redirected")
            if response.status_code != 200:
                raise ArcAgiAdapterError(
                    f"ARC-AGI fixed source returned HTTP {response.status_code}"
                )
            content_encoding = response.headers.get("content-encoding", "identity").casefold()
            if content_encoding not in {"", "identity"}:
                raise ArcAgiAdapterError("ARC-AGI fixed source used content encoding")
            media_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
            if media_type not in media_types:
                raise ArcAgiAdapterError("ARC-AGI fixed source returned an unexpected media type")
            if require_repo_commit and response.headers.get("x-repo-commit") != ARC_AGI_HF_REVISION:
                raise ArcAgiAdapterError("ARC-AGI result response omitted the pinned repo commit")
            raw_length = response.headers.get("content-length")
            if raw_length is not None:
                try:
                    declared_length = int(raw_length)
                except ValueError:
                    raise ArcAgiAdapterError(
                        "ARC-AGI fixed source returned an invalid content length"
                    ) from None
                if declared_length < 0 or declared_length > maximum:
                    raise ArcAgiAdapterError("ARC-AGI fixed source exceeds the byte limit")
            chunks: list[bytes] = []
            consumed = 0
            for chunk in response.iter_bytes():
                if deadline is not None and time_module.monotonic() >= deadline:
                    raise ArcAgiAdapterError("ARC-AGI capture exceeded its total time limit")
                consumed += len(chunk)
                if consumed > maximum:
                    raise ArcAgiAdapterError("ARC-AGI fixed source exceeds the byte limit")
                chunks.append(chunk)
            if deadline is not None and time_module.monotonic() >= deadline:
                raise ArcAgiAdapterError("ARC-AGI capture exceeded its total time limit")
            return b"".join(chunks)
    except ArcAgiAdapterError:
        raise
    except httpx.HTTPError as exc:
        raise ArcAgiAdapterError(f"cannot fetch pinned ARC-AGI source: {exc}") from exc


def load_arc_agi_public_eval_source(
    *,
    retrieved_at: datetime | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> LoadedArcAgiSource:
    """Fetch only the fixed revision metadata and reviewed result summaries."""

    timeout = _timeout(timeout_seconds)
    deadline = time_module.monotonic() + timeout
    timestamp = retrieved_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ArcAgiAdapterError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(UTC)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    with httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout),
        limits=limits,
        trust_env=False,
    ) as client:
        revision_metadata = _fetch(
            client,
            ARC_AGI_HF_REVISION_API,
            maximum=MAX_REVISION_BYTES,
            media_types=frozenset({"application/json"}),
            require_repo_commit=False,
            deadline=deadline,
        )
        _validated_revision_metadata(revision_metadata)
        result_files: list[tuple[str, bytes]] = []
        total = 0
        for path in ARC_AGI_EXPECTED_RESULT_PATHS:
            raw = _fetch(
                client,
                _fixed_result_url(path),
                maximum=MAX_RESULT_BYTES,
                media_types=frozenset({"application/json", "text/plain"}),
                require_repo_commit=True,
                deadline=deadline,
            )
            total += len(raw)
            if total > MAX_TOTAL_RESULT_BYTES:
                raise ArcAgiAdapterError("ARC-AGI result capture exceeds the aggregate byte limit")
            result_files.append((path, raw))
    return LoadedArcAgiSource(
        revision_metadata=revision_metadata,
        result_files=tuple(result_files),
        retrieved_at=timestamp,
    )


def capture_arc_agi_public_eval(
    *,
    retrieved_at: datetime | None = None,
    rights: QualityRights | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ArcAgiCapture:
    source = load_arc_agi_public_eval_source(
        retrieved_at=retrieved_at,
        timeout_seconds=timeout_seconds,
    )
    return _normalize_arc_agi_public_eval_bytes(
        source.revision_metadata,
        dict(source.result_files),
        retrieved_at=source.retrieved_at,
        rights=rights,
        capture_trust=_ArcAgiCaptureTrust.OFFICIAL_FIXED_NETWORK,
    )


def write_arc_agi_public_eval_capture(
    capture: ArcAgiCapture,
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Atomically publish a private evidence/inventory bundle without raw inputs."""

    if not isinstance(capture, ArcAgiCapture):
        raise ArcAgiAdapterError("capture must be an ArcAgiCapture")
    evidence_text = dump_json(capture.evidence)
    inventory_text = json.dumps(capture.inventory(), indent=2, ensure_ascii=False) + "\n"
    output_sha256 = {
        EVIDENCE_FILENAME: hashlib.sha256(evidence_text.encode("utf-8")).hexdigest(),
        INVENTORY_FILENAME: hashlib.sha256(inventory_text.encode("utf-8")).hexdigest(),
    }
    manifest_text = (
        json.dumps(
            capture.manifest(output_sha256=output_sha256),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        return publish_text_bundle(
            output_directory,
            {
                EVIDENCE_FILENAME: evidence_text,
                INVENTORY_FILENAME: inventory_text,
                MANIFEST_FILENAME: manifest_text,
            },
            manifest_name=MANIFEST_FILENAME,
            overwrite=overwrite,
            directory_mode=0o700,
            file_mode=0o600,
        )
    except BundlePublicationError as exc:
        raise ArcAgiAdapterError(str(exc)) from exc


# Short compatibility-style alias for library callers.
write_arc_agi_capture = write_arc_agi_public_eval_capture
