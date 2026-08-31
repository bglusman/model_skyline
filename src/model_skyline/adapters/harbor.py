"""Fail-closed adapter for captured Harbor Terminal-Bench leaderboards.

The Harbor CLI is deliberately outside this trust boundary.  A collector captures
``harbor hub leaderboard show ... --json`` and an operator reviews an exact mapping
from selected row UUIDs to complete :class:`OfferingKey` values.  This module only
parses bounded local JSON; it never executes a configured command or guesses a route
from leaderboard labels.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field, ValidationError, model_validator

from model_skyline.adapters._publication import (
    BundlePublicationError,
    publish_text_bundle,
)
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, canonical_bytes
from model_skyline.io import dump_json
from model_skyline.models import (
    FORBIDDEN_TEXT_RE,
    FrozenModel,
    Observation,
    ObservationCatalog,
    ObservationRequirements,
    OfferingObservation,
    ProjectConfig,
    SignalMetric,
    SourceReference,
    WorkloadProfile,
    WorkloadReference,
    bounded_canonical_decimal,
)
from model_skyline.quality_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    MappedQualityRow,
    QualityComponentIdentity,
    QualityCount,
    QualityEvidenceRow,
    QualityEvidenceSet,
    QualityImportOutcome,
    QualityImportReport,
    QualityInvalidResult,
    QualityMeasurement,
    QualityMeasurementRole,
    QualityModelClaim,
    QualityPublicationScope,
    QualityRawAudit,
    QualityReconciliation,
    QualityResult,
    QualityRights,
    QualityRouteDisclosure,
    QualitySourceIdentity,
    QualitySubjectIdentity,
    QualitySubjectKind,
    quality_raw_sha256,
    reconcile_quality_evidence,
)

PARSER_VERSION = "harbor-terminal-bench-leaderboard-json@2"
CONFIG_SCHEMA_VERSION = "model-skyline/harbor-terminal-bench-import-config/v1alpha1"

DEFAULT_MAX_SOURCE_BYTES = 16_000_000
HARD_MAX_SOURCE_BYTES = 64_000_000
MAX_MAPPING_BYTES = 2_000_000
MAX_JSON_DEPTH = 48
MAX_JSON_NODES = 2_000_000
MAX_JSON_STRUCTURAL_TOKENS = 4_000_000
MAX_JSON_STRING_LENGTH = 65_536
MAX_ROWS = 10_000
MAX_TEXT_LENGTH = 2_048

CATALOG_FILENAME = "observations.json"
CONFIG_FILENAME = "frontier.yaml"
MAPPING_FILENAME = "mapping.json"
EVIDENCE_FILENAME = "evidence.json"
IMPORT_REPORT_FILENAME = "import-report.json"
MANIFEST_FILENAME = "import.json"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUIRED_METADATA_FIELDS = frozenset(
    {
        "agent_display",
        "agent_org",
        "date",
        "display_date",
        "model_display",
        "model_org",
        "reasoning_effort",
    }
)
_REQUIRED_METRIC_FIELDS = frozenset(
    {
        "accuracy",
        "accuracy_ci95_half_width",
        "display_accuracy",
        "display_cost",
        "display_total_tokens",
        "total_tokens",
        "total_cost_usd",
        "n_trials",
    }
)
_PARSED_METRIC_FIELDS = _REQUIRED_METRIC_FIELDS | frozenset(
    {
        "successes",
        "pass_at_2",
        "pass_at_3",
        "pass_at_4",
        "pass_at_5",
        "uncached_input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "avg_trial_duration_sec",
    }
)


class HarborAdapterError(ValueError):
    """A Harbor snapshot or reviewed mapping is unsafe or ambiguous."""


class HarborTerminalBenchImportConfig(FrozenModel):
    """Acquisition provenance, rights review, and exact generic reconciliation."""

    schema_version: Literal["model-skyline/harbor-terminal-bench-import-config/v1alpha1"]
    source_url: str = Field(min_length=1, max_length=2_083)
    methodology_url: str = Field(min_length=1, max_length=2_083)
    capture_tool: Literal["harbor"]
    capture_tool_version: str = Field(min_length=1, max_length=128)
    publication_scope: QualityPublicationScope = QualityPublicationScope.INTERNAL
    rights: QualityRights
    reconciliation: QualityReconciliation

    @model_validator(mode="after")
    def provenance_and_targets_are_safe(self) -> HarborTerminalBenchImportConfig:
        _validated_https_url(self.source_url, field="source_url")
        _validated_https_url(self.methodology_url, field="methodology_url")
        if self.rights.terms_locator is None:
            raise ValueError("rights.terms_locator must be explicit")
        _validated_https_url(self.rights.terms_locator, field="rights.terms_locator")
        reviewed_text = (
            self.capture_tool_version,
            self.rights.license_expression,
            self.rights.review_evidence,
            *(entry.review_evidence for entry in self.reconciliation.entries),
        )
        if any(FORBIDDEN_TEXT_RE.search(value) is not None for value in reviewed_text):
            raise ValueError("reviewed configuration text contains forbidden controls")
        for entry in self.reconciliation.entries:
            if "tools" not in entry.offering.capabilities:
                raise ValueError("Terminal-Bench reconciliation targets require tools")
            if entry.offering.provider == "unknown":
                raise ValueError("Terminal-Bench reconciliation targets require a provider")
            if entry.offering.agent_harness is None:
                raise ValueError(
                    "Terminal-Bench reconciliation targets require a production agent_harness"
                )
            offering_text = (
                entry.offering.offering_id,
                entry.offering.model_id,
                entry.offering.provider,
                entry.offering.endpoint,
                entry.offering.billing_mode,
                entry.offering.region,
                entry.offering.service_tier,
                entry.offering.quantization,
                entry.offering.reasoning_effort,
                entry.offering.agent_harness,
            )
            if any(
                value is not None and FORBIDDEN_TEXT_RE.search(value) is not None
                for value in offering_text
            ):
                raise ValueError("offering identity contains forbidden controls")
            _validated_offering_endpoint(entry.offering.endpoint)
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_MAPPING_BYTES:
            raise ValueError(f"Harbor import configuration exceeds {MAX_MAPPING_BYTES} bytes")
        if len(self.model_dump_json(indent=2).encode("utf-8")) + 1 > MAX_MAPPING_BYTES:
            raise ValueError(
                f"Harbor import configuration exceeds {MAX_MAPPING_BYTES} serialized bytes"
            )
        return self


# Compatibility name for the pre-release CLI/API.  The file now contains the
# generic QualityReconciliation rather than an adapter-specific mapping schema.
HarborTerminalBenchMapping = HarborTerminalBenchImportConfig


@dataclass(frozen=True, slots=True)
class HarborTerminalBenchImportResult:
    catalog: ObservationCatalog
    config: ProjectConfig
    source: SourceReference
    mapping: HarborTerminalBenchImportConfig
    mapping_sha256: str
    mapping_document: str
    evidence: QualityEvidenceSet
    report: QualityImportReport
    rows_seen: int
    allow_partial: bool

    @property
    def raw_sha256(self) -> str:
        return self.evidence.raw_audit.raw_sha256

    @property
    def source_identity_sha256(self) -> str:
        return self.evidence.source_identity_sha256

    @property
    def subject_identity_sha256(self) -> Mapping[str, str]:
        return {row.row_id: row.subject_identity_sha256 for row in self.evidence.rows}

    @property
    def result_sha256(self) -> Mapping[str, str]:
        return {row.row_id: row.result_sha256 for row in self.evidence.rows}

    @property
    def excluded(self) -> Mapping[str, str]:
        return {
            record.row_id: record.outcome.value
            for record in self.report.records
            if record.outcome.value != "mapped"
        }

    def manifest(self, *, output_sha256: Mapping[str, str]) -> dict[str, Any]:
        """Describe the private audit bundle and bind every non-manifest payload."""

        return {
            "schema_version": "model-skyline/harbor-terminal-bench-import/v1alpha1",
            "adapter": "harbor-terminal-bench",
            "parser_version": PARSER_VERSION,
            "source": self.source.model_dump(mode="json"),
            "raw_audit_sha256": self.evidence.raw_audit_sha256,
            "raw_sha256": self.evidence.raw_audit.raw_sha256,
            "source_identity_sha256": self.source_identity_sha256,
            "rights_sha256": self.evidence.rights_sha256,
            "mapping_sha256": self.mapping_sha256,
            "allow_partial": self.allow_partial,
            "reconciliation_sha256": self.mapping.reconciliation.content_sha256,
            "import_report_sha256": self.report.content_sha256,
            "leaderboard": {
                "id": self.evidence.source_identity.scope["leaderboard_id"],
                "package": self.evidence.source_identity.scope["package"],
                "name": self.evidence.source_identity.source_version,
                "dataset_version_ids": self.evidence.source_identity.dataset.configuration[
                    "dataset_version_ids"
                ],
            },
            "rows": {
                "seen": self.rows_seen,
                "mapped": len(self.catalog.offerings),
                "excluded": dict(sorted(self.excluded.items())),
                "subject_identity_sha256": dict(sorted(self.subject_identity_sha256.items())),
                "result_sha256": dict(sorted(self.result_sha256.items())),
            },
            "outputs": {
                "catalog": CATALOG_FILENAME,
                "config": CONFIG_FILENAME,
                "mapping": MAPPING_FILENAME,
                "evidence": EVIDENCE_FILENAME,
                "import_report": IMPORT_REPORT_FILENAME,
            },
            "output_sha256": dict(sorted(output_sha256.items())),
            "warnings": [
                "The source is an operator-captured Harbor CLI response; its raw digest is "
                "retained, but ModelSkyline does not independently attest the capture's "
                "authenticity.",
                "Reconciliation binds stable source and benchmark-subject identities. Score, cost, "
                "status, rank, timestamp, or other result-only changes produce a new result "
                "digest without silently changing the mapped subject.",
                "Every score remains a compound agent/model/harness system result and must "
                "not be presented as a bare-model benchmark score.",
                "Reviewed quality projections copy only quality-role measurements. Harbor "
                "cost, latency, token, and cache evidence is not attributed to a production route.",
                "Token/cache field relationships are board-version-specific and unasserted; "
                "the adapter does not sum those buckets or use them to estimate spend.",
                "Reported cost may omit infrastructure, tools, retries, or other charges not "
                "included by the upstream submission methodology.",
                "Leaderboard evidence is historical; availability and independent pricing "
                "evidence must be refreshed under their own policies.",
            ],
        }


def _read_bounded_regular_file(path: Path, max_bytes: int, *, label: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HarborAdapterError(f"cannot open {label} {path}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise HarborAdapterError(f"{label} is not a regular file: {path}")
            if before.st_size > max_bytes:
                raise HarborAdapterError(f"{label} exceeds the {max_bytes}-byte limit")
            raw = source.read(max_bytes + 1)
            after = os.fstat(source.fileno())
    except HarborAdapterError:
        raise
    except OSError as exc:
        raise HarborAdapterError(f"cannot read {label} {path}: {exc}") from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(raw) != before.st_size:
        raise HarborAdapterError(f"{label} changed while it was being read")
    if len(raw) > max_bytes:
        raise HarborAdapterError(f"{label} exceeds the {max_bytes}-byte limit")
    return raw


def _validate_json_depth(text: str, *, label: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    structural_tokens = 0
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            structural_tokens += 1
            if depth > MAX_JSON_DEPTH:
                raise HarborAdapterError(
                    f"{label} exceeds the maximum JSON depth of {MAX_JSON_DEPTH}"
                )
        elif character in "]}":
            depth -= 1
            structural_tokens += 1
            if depth < 0:
                raise HarborAdapterError(f"{label} has unbalanced JSON delimiters")
        elif character in ",:":
            structural_tokens += 1
        if structural_tokens > MAX_JSON_STRUCTURAL_TOKENS:
            raise HarborAdapterError(
                f"{label} exceeds {MAX_JSON_STRUCTURAL_TOKENS} structural tokens"
            )
    if in_string or depth != 0:
        raise HarborAdapterError(f"{label} has unterminated JSON syntax")


def _parse_decimal(value: str) -> Decimal:
    if len(value) > 1_024:
        raise HarborAdapterError("JSON decimal exceeds 1024 characters")
    try:
        return bounded_canonical_decimal(Decimal(value))
    except (InvalidOperation, ValueError) as exc:
        raise HarborAdapterError("JSON decimal is invalid or exceeds canonical bounds") from exc


def _parse_integer(value: str) -> int:
    if len(value) > 1_024:
        raise HarborAdapterError("JSON integer exceeds 1024 characters")
    return int(value)


def _reject_constant(value: str) -> None:
    raise HarborAdapterError(f"non-finite JSON number {value!r} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            raise HarborAdapterError(f"duplicate JSON object key length={len(key)} sha256={digest}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HarborAdapterError(f"{label} is not valid UTF-8") from exc
    _validate_json_depth(text, label=label)
    try:
        value = json.loads(
            text,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except HarborAdapterError:
        raise
    except (RecursionError, ValueError, json.JSONDecodeError) as exc:
        raise HarborAdapterError(f"cannot parse {label}: {exc}") from exc
    stack = [value]
    nodes = 0
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise HarborAdapterError(f"{label} exceeds {MAX_JSON_NODES} JSON nodes")
        if isinstance(current, str):
            if len(current) > MAX_JSON_STRING_LENGTH:
                raise HarborAdapterError(
                    f"{label} contains a string longer than {MAX_JSON_STRING_LENGTH} characters"
                )
        elif isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return value


def _typed_canonical_hash_value(value: Any) -> Any:
    """Injectively tag JSON types while normalizing equivalent JSON numbers."""

    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, (int, Decimal)):
        try:
            number = bounded_canonical_decimal(
                value if isinstance(value, Decimal) else Decimal(value)
            )
        except ValueError as exc:
            raise HarborAdapterError("numeric hash input exceeds canonical bounds") from exc
        return ["number", format(number, "f")]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, Mapping):
        return [
            "object",
            [[key, _typed_canonical_hash_value(child)] for key, child in sorted(value.items())],
        ]
    if isinstance(value, (list, tuple)):
        return ["array", [_typed_canonical_hash_value(child) for child in value]]
    raise HarborAdapterError("selected Harbor value is not JSON-compatible")


def harbor_value_sha256(value: Any) -> str:
    """Hash a normalized identity/result value with a language-neutral Decimal rule."""

    try:
        encoded = canonical_bytes(_typed_canonical_hash_value(value))
    except ValueError as exc:
        raise HarborAdapterError(f"selected Harbor value is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def load_harbor_terminal_bench_mapping(
    source: bytes | str | Path,
) -> tuple[HarborTerminalBenchImportConfig, str, str]:
    """Load a bounded import configuration and retain its exact review bytes."""

    if isinstance(source, bytes):
        raw = source
    else:
        raw = _read_bounded_regular_file(
            Path(source),
            MAX_MAPPING_BYTES,
            label="Harbor mapping",
        )
    if len(raw) > MAX_MAPPING_BYTES:
        raise HarborAdapterError(f"Harbor mapping exceeds the {MAX_MAPPING_BYTES}-byte limit")
    value = _decode_json(raw, label="Harbor import configuration JSON")
    try:
        mapping = HarborTerminalBenchImportConfig.model_validate(value)
    except (RecursionError, ValueError) as exc:
        # Pydantic errors may include the rejected input.  Keep credentials,
        # labels, and review prose out of CLI/error logs.
        raise HarborAdapterError("invalid Harbor import configuration") from exc
    return mapping, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")


def _required_object(container: Mapping[str, Any], field: str, *, scope: str) -> Mapping[str, Any]:
    value = container.get(field)
    if not isinstance(value, dict):
        raise HarborAdapterError(f"{scope}.{field} must be a JSON object")
    return value


def _required_text(
    container: Mapping[str, Any], field: str, *, scope: str, max_length: int = MAX_TEXT_LENGTH
) -> str:
    value = container.get(field)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_length
        or FORBIDDEN_TEXT_RE.search(value) is not None
    ):
        raise HarborAdapterError(
            f"{scope}.{field} must be safe non-empty text of at most {max_length} characters"
        )
    return value


def _validated_https_url(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2_083:
        raise ValueError(f"{field} must be a non-empty HTTPS URL")
    if FORBIDDEN_TEXT_RE.search(value) is not None or any(
        character.isspace() for character in value
    ):
        raise ValueError(f"{field} contains forbidden whitespace or control characters")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid HTTPS URL") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{field} must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} cannot contain user information")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field} cannot contain a query string or fragment")
    hostname = parsed.hostname
    assert hostname is not None
    lowered = hostname.rstrip(".").lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(
        (".localhost", ".local", ".internal", ".test")
    ):
        raise ValueError(f"{field} must name a public host")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        try:
            hostname.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field} hostname must use its ASCII IDNA form") from exc
    else:
        if not address.is_global:
            raise ValueError(f"{field} must not name a private or special-use address")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:  # pragma: no cover - urlsplit enforces
        raise ValueError(f"{field} contains an invalid port")
    return value


def _validated_offering_endpoint(value: str | None) -> None:
    """Prevent route configuration from becoming a credential-bearing artifact."""

    if value is None:
        return
    if any(character.isspace() for character in value):
        raise ValueError("offering endpoint contains forbidden whitespace")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("offering endpoint URL must be absolute HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("offering endpoint URL cannot contain user information")
        if parsed.query or parsed.fragment:
            raise ValueError("offering endpoint URL cannot contain query or fragment data")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("offering endpoint URL contains an invalid port") from exc
    elif any(character in value for character in "@?#"):
        raise ValueError("symbolic offering endpoint cannot contain @, ?, or #")


def _required_uuid(container: Mapping[str, Any], field: str, *, scope: str) -> str:
    value = _required_text(container, field, scope=scope, max_length=36)
    if not _UUID_RE.fullmatch(value):
        raise HarborAdapterError(f"{scope}.{field} must be a canonical lowercase UUID")
    return value


def _required_nonnegative_decimal(
    container: Mapping[str, Any],
    field: str,
    *,
    scope: str,
    maximum: Decimal | None = None,
) -> Decimal:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise HarborAdapterError(f"{scope}.{field} must be a JSON number")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite() or result < 0:
        raise HarborAdapterError(f"{scope}.{field} must be finite and non-negative")
    if maximum is not None and result > maximum:
        raise HarborAdapterError(f"{scope}.{field} must not exceed {maximum}")
    return result


def _required_nonnegative_integer(
    container: Mapping[str, Any], field: str, *, scope: str, positive: bool = False
) -> int:
    value = container.get(field)
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise HarborAdapterError(f"{scope}.{field} must be a {qualifier} integer")
    return value


def _required_datetime(container: Mapping[str, Any], field: str, *, scope: str) -> datetime:
    text = _required_text(container, field, scope=scope, max_length=64)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HarborAdapterError(f"{scope}.{field} must be an ISO 8601 timestamp") from exc
    if value.tzinfo is None:
        raise HarborAdapterError(f"{scope}.{field} must include a timezone")
    return value.astimezone(UTC)


def _required_date(container: Mapping[str, Any], field: str, *, scope: str) -> str:
    text = _required_text(container, field, scope=scope, max_length=10)
    if not _DATE_RE.fullmatch(text):
        raise HarborAdapterError(f"{scope}.{field} must be an ISO 8601 calendar date")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise HarborAdapterError(f"{scope}.{field} must be a valid calendar date") from exc
    return text


def _required_link(container: Mapping[str, Any], field: str, *, scope: str) -> tuple[str, str]:
    link = _required_object(container, field, scope=scope)
    if set(link) != {"url", "label"}:
        raise HarborAdapterError(f"{scope}.{field} must contain exactly url and label")
    url = _required_text(link, "url", scope=f"{scope}.{field}", max_length=2_083)
    label = _required_text(link, "label", scope=f"{scope}.{field}", max_length=512)
    try:
        _validated_https_url(url, field=f"{scope}.{field}.url")
    except ValueError as exc:
        raise HarborAdapterError(f"{scope}.{field}.url is not a safe public URL: {exc}") from exc
    return label, url


def _schema_contract(
    schema: Mapping[str, Any], *, required_fields: frozenset[str], scope: str
) -> None:
    _validate_supported_schema_node(schema, scope=scope)
    if schema.get("type") != "object":  # pragma: no cover - checked above
        raise HarborAdapterError(f"{scope} must be a closed JSON object schema")
    required = schema.get("required")
    properties = schema.get("properties")
    assert isinstance(required, list) and isinstance(properties, dict)
    missing_properties = sorted(required_fields - set(properties))
    missing_required = sorted(required_fields - set(required))
    if missing_properties or missing_required:
        details = []
        if missing_properties:
            details.append("properties=" + ",".join(missing_properties))
        if missing_required:
            details.append("required=" + ",".join(missing_required))
        raise HarborAdapterError(
            f"{scope} does not require the supported Terminal-Bench fields: " + "; ".join(details)
        )


def _schema_number(value: Any, *, scope: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise HarborAdapterError(f"{scope} must be a JSON number")
    try:
        return bounded_canonical_decimal(value if isinstance(value, Decimal) else Decimal(value))
    except ValueError as exc:
        raise HarborAdapterError(f"{scope} exceeds canonical numeric bounds") from exc


def _validate_supported_schema_node(
    schema: Mapping[str, Any],
    *,
    scope: str,
    depth: int = 0,
) -> None:
    """Meta-validate the small closed JSON-Schema subset Harbor 4.0 uses."""

    if depth > 4:
        raise HarborAdapterError(f"{scope} schema nesting is unsupported")
    if any(FORBIDDEN_TEXT_RE.search(key) is not None for key in schema):
        raise HarborAdapterError(f"{scope} contains forbidden schema text")
    schema_type = schema.get("type")
    if schema_type == "object":
        allowed = {"additionalProperties", "properties", "required", "type"}
        if set(schema) - allowed or schema.get("additionalProperties") is not False:
            raise HarborAdapterError(f"{scope} uses unsupported object-schema keywords")
        required = schema.get("required")
        properties = schema.get("properties")
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise HarborAdapterError(f"{scope}.required must be an array of strings")
        if any(FORBIDDEN_TEXT_RE.search(item) is not None for item in required):
            raise HarborAdapterError(f"{scope}.required contains forbidden schema text")
        if len(required) != len(set(required)):
            raise HarborAdapterError(f"{scope}.required must contain unique field names")
        if not isinstance(properties, dict) or not all(
            isinstance(key, str) and isinstance(value, dict) for key, value in properties.items()
        ):
            raise HarborAdapterError(f"{scope}.properties must contain schema objects")
        if any(FORBIDDEN_TEXT_RE.search(key) is not None for key in properties):
            raise HarborAdapterError(f"{scope}.properties contains forbidden schema text")
        if not set(required) <= set(properties):
            raise HarborAdapterError(f"{scope}.required must be a subset of properties")
        for index, child in enumerate(properties.values()):
            _validate_supported_schema_node(
                child,
                scope=f"{scope}.properties[{index}]",
                depth=depth + 1,
            )
        return
    if schema_type == "string":
        allowed = {"format", "maxLength", "minLength", "pattern", "type"}
        if set(schema) - allowed:
            raise HarborAdapterError(f"{scope} uses unsupported string-schema keywords")
        for field in ("minLength", "maxLength"):
            value = schema.get(field)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_JSON_STRING_LENGTH
            ):
                raise HarborAdapterError(f"{scope}.{field} must be a bounded integer")
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
            raise HarborAdapterError(f"{scope} has minLength greater than maxLength")
        pattern = schema.get("pattern")
        if pattern is not None and (
            not isinstance(pattern, str)
            or len(pattern) > 2_048
            or FORBIDDEN_TEXT_RE.search(pattern) is not None
        ):
            raise HarborAdapterError(f"{scope}.pattern must be bounded text")
        format_name = schema.get("format")
        if format_name is not None and format_name != "uri":
            raise HarborAdapterError(f"{scope}.format is unsupported")
        return
    if schema_type == "number":
        if set(schema) - {"maximum", "minimum", "type"}:
            raise HarborAdapterError(f"{scope} uses unsupported number-schema keywords")
        minimum_raw = schema.get("minimum")
        maximum_raw = schema.get("maximum")
        minimum = (
            _schema_number(minimum_raw, scope=f"{scope}.minimum")
            if minimum_raw is not None
            else None
        )
        maximum = (
            _schema_number(maximum_raw, scope=f"{scope}.maximum")
            if maximum_raw is not None
            else None
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise HarborAdapterError(f"{scope} has minimum greater than maximum")
        return
    raise HarborAdapterError(f"{scope} uses an unsupported schema type")


def _validate_closed_row_object(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    scope: str,
) -> None:
    required = schema.get("required")
    properties = schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):  # pragma: no cover
        raise HarborAdapterError(f"{scope} schema is internally inconsistent")
    if set(required) - set(value):
        raise HarborAdapterError(f"{scope} omits fields required by the embedded schema")
    if set(value) - set(properties):
        raise HarborAdapterError(f"{scope} contains fields outside the embedded closed schema")
    for index, field in enumerate(sorted(value)):
        child = value[field]
        child_schema = properties[field]
        assert isinstance(child_schema, dict)  # guaranteed by the schema meta-validator
        _validate_row_value_shape(
            child,
            child_schema,
            scope=f"{scope}.field[{index}]",
        )


def _validate_row_value_shape(value: Any, schema: Mapping[str, Any], *, scope: str) -> None:
    """Enforce the safe type/bound subset without executing source-supplied regexes."""

    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, dict):
            raise HarborAdapterError(f"{scope} must be a JSON object")
        _validate_closed_row_object(value, schema, scope=scope)
        return
    if schema_type == "string":
        if not isinstance(value, str) or FORBIDDEN_TEXT_RE.search(value) is not None:
            raise HarborAdapterError(f"{scope} must be safe JSON text")
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise HarborAdapterError(f"{scope} is shorter than its embedded schema permits")
        if isinstance(maximum, int) and len(value) > maximum:
            raise HarborAdapterError(f"{scope} is longer than its embedded schema permits")
        if len(value) > MAX_JSON_STRING_LENGTH:
            raise HarborAdapterError(f"{scope} exceeds the adapter string limit")
        return
    if schema_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise HarborAdapterError(f"{scope} must be a JSON number")
        number = _schema_number(value, scope=scope)
        minimum_raw = schema.get("minimum")
        maximum_raw = schema.get("maximum")
        if minimum_raw is not None and number < _schema_number(
            minimum_raw, scope=f"{scope}.schema.minimum"
        ):
            raise HarborAdapterError(f"{scope} is below its embedded schema minimum")
        if maximum_raw is not None and number > _schema_number(
            maximum_raw, scope=f"{scope}.schema.maximum"
        ):
            raise HarborAdapterError(f"{scope} exceeds its embedded schema maximum")
        return
    raise HarborAdapterError(f"{scope} has an unsupported embedded schema type")


def _release_date_column(columns: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    matches = [column for column in columns if column.get("accessor") == "metadata.date"]
    if len(matches) != 1:
        raise HarborAdapterError(
            "leaderboard.columns must contain exactly one metadata.date release-date column"
        )
    column = matches[0]
    expected = {
        "id": "date",
        "type": "date",
        "align": "right",
        "header": "Release Date",
        "accessor": "metadata.date",
        "display_type": "text",
        "display_accessor": "metadata.display_date",
    }
    if any(column.get(field) != value for field, value in expected.items()):
        raise HarborAdapterError(
            "leaderboard metadata.date column no longer has supported release-date semantics"
        )
    return column


def _schema_projection(
    schema: Mapping[str, Any],
    *,
    fields: frozenset[str],
    scope: str,
) -> Mapping[str, Any]:
    required = schema.get("required")
    properties = schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        raise HarborAdapterError(f"{scope} is not a supported object schema")
    missing = sorted(fields - set(properties))
    if missing:
        raise HarborAdapterError(
            f"{scope} is missing parsed field definitions: {', '.join(missing)}"
        )
    return {
        "type": schema.get("type"),
        "additionalProperties": schema.get("additionalProperties"),
        "fields": {
            field: {
                "required": field in required,
                "schema": properties[field],
            }
            for field in sorted(fields)
        },
    }


def _leaderboard_and_rows(
    raw: bytes,
) -> tuple[
    Mapping[str, Any],
    list[Mapping[str, Any]],
    Mapping[str, Any],
]:
    value = _decode_json(raw, label="Harbor leaderboard JSON")
    if not isinstance(value, dict) or set(value) != {"leaderboard", "rows"}:
        raise HarborAdapterError(
            "Harbor leaderboard root must contain exactly 'leaderboard' and 'rows'"
        )
    leaderboard = _required_object(value, "leaderboard", scope="root")
    rows_value = value.get("rows")
    if not isinstance(rows_value, list):
        raise HarborAdapterError("root.rows must be a JSON array")
    if len(rows_value) > MAX_ROWS:
        raise HarborAdapterError(f"Harbor leaderboard exceeds the {MAX_ROWS}-row limit")
    if not all(isinstance(row, dict) for row in rows_value):
        raise HarborAdapterError("every Harbor leaderboard row must be a JSON object")
    rows = list(rows_value)

    leaderboard_id = _required_uuid(leaderboard, "id", scope="leaderboard")
    package_id = _required_uuid(leaderboard, "package_id", scope="leaderboard")
    package = _required_text(leaderboard, "package", scope="leaderboard", max_length=256)
    name = _required_text(leaderboard, "name", scope="leaderboard", max_length=256)
    if package != "terminal-bench/terminal-bench":
        raise HarborAdapterError(
            "this adapter only accepts package='terminal-bench/terminal-bench'"
        )
    raw_dataset_ids = leaderboard.get("dataset_version_ids")
    if not isinstance(raw_dataset_ids, list) or not all(
        isinstance(item, str) and _UUID_RE.fullmatch(item) for item in raw_dataset_ids
    ):
        raise HarborAdapterError(
            "leaderboard.dataset_version_ids must be an array of canonical lowercase UUIDs"
        )
    if not raw_dataset_ids or len(set(raw_dataset_ids)) != len(raw_dataset_ids):
        raise HarborAdapterError("leaderboard.dataset_version_ids must be non-empty and unique")

    metadata_schema = _required_object(leaderboard, "metadata_schema", scope="leaderboard")
    metrics_schema = _required_object(leaderboard, "metrics_schema", scope="leaderboard")
    _schema_contract(
        metadata_schema,
        required_fields=_REQUIRED_METADATA_FIELDS,
        scope="leaderboard.metadata_schema",
    )
    _schema_contract(
        metrics_schema,
        required_fields=_REQUIRED_METRIC_FIELDS,
        scope="leaderboard.metrics_schema",
    )
    metadata_contract = _schema_projection(
        metadata_schema,
        fields=_REQUIRED_METADATA_FIELDS,
        scope="leaderboard.metadata_schema",
    )
    metrics_contract = _schema_projection(
        metrics_schema,
        fields=_PARSED_METRIC_FIELDS,
        scope="leaderboard.metrics_schema",
    )

    rank_by = leaderboard.get("rank_by")
    if rank_by != [{"accessor": "metrics.accuracy", "direction": "desc"}]:
        raise HarborAdapterError(
            "supported Terminal-Bench boards must rank descending by metrics.accuracy"
        )
    if leaderboard.get("visibility") != "public":
        raise HarborAdapterError("only public Terminal-Bench leaderboard snapshots are supported")
    _required_datetime(leaderboard, "updated_at", scope="leaderboard")
    columns = leaderboard.get("columns")
    if not isinstance(columns, list) or not all(isinstance(column, dict) for column in columns):
        raise HarborAdapterError("leaderboard.columns must be an array of JSON objects")
    release_date_column = _release_date_column(columns)
    source_identity: Mapping[str, Any] = {
        "parser_version": PARSER_VERSION,
        "leaderboard_id": leaderboard_id,
        "package_id": package_id,
        "package": package,
        "name": name,
        "dataset_version_ids": list(raw_dataset_ids),
        "metadata_schema_sha256": harbor_value_sha256(metadata_schema),
        "metrics_schema_sha256": harbor_value_sha256(metrics_schema),
        "metadata_contract_sha256": harbor_value_sha256(metadata_contract),
        "metrics_contract_sha256": harbor_value_sha256(metrics_contract),
        "release_date_column_sha256": harbor_value_sha256(release_date_column),
        "parsed_metadata_fields": sorted(_REQUIRED_METADATA_FIELDS),
        "parsed_metric_fields": sorted(_PARSED_METRIC_FIELDS),
        "rank_by": rank_by,
    }
    return leaderboard, rows, source_identity


@dataclass(frozen=True, slots=True)
class _ValidatedRow:
    row_id: str
    subject: QualitySubjectIdentity
    quality_result: QualityResult | None
    invalid_result: QualityInvalidResult | None
    agent_label: str
    agent_url: str
    agent_org_label: str
    agent_org_url: str
    model_label: str
    model_url: str
    model_org_label: str
    model_org_url: str
    reasoning_effort: str
    leaderboard_release_date: str
    display_release_date: str
    optional_release_date: str | None
    status: str
    rank: int | None
    n_trials: int
    successes: int
    accuracy_ratio: Decimal
    accuracy_lower: Decimal
    accuracy_upper: Decimal
    total_cost_usd: Decimal
    cost_per_trial_usd: Decimal
    total_tokens: int
    tokens_per_trial: Decimal
    token_fields: Mapping[str, int]
    avg_trial_duration_sec: Decimal | None
    pass_at_k: Mapping[str, Decimal]
    observed_at: datetime
    metadata: Mapping[str, Any]
    metrics: Mapping[str, Any]

    @property
    def evidence_row(self) -> QualityEvidenceRow:
        return QualityEvidenceRow(
            subject=self.subject,
            result=self.quality_result,
            invalid_result=self.invalid_result,
        )

    @property
    def subject_identity_sha256(self) -> str:
        return self.subject.content_sha256

    @property
    def result_sha256(self) -> str:
        return self.evidence_row.result_sha256


def _validate_row(
    row: Mapping[str, Any],
    *,
    leaderboard_id: str,
    metadata_schema: Mapping[str, Any],
    metrics_schema: Mapping[str, Any],
    retrieved_at: datetime,
) -> _ValidatedRow:
    row_id = _required_uuid(row, "id", scope="row")
    scope = f"row[{row_id}]"
    if _required_uuid(row, "leaderboard_id", scope=scope) != leaderboard_id:
        raise HarborAdapterError(f"{scope}.leaderboard_id does not match the board")
    metadata = _required_object(row, "metadata", scope=scope)
    metrics = _required_object(row, "metrics", scope=scope)
    _validate_closed_row_object(metadata, metadata_schema, scope=f"{scope}.metadata")
    _validate_closed_row_object(metrics, metrics_schema, scope=f"{scope}.metrics")
    agent_label, agent_url = _required_link(metadata, "agent_display", scope=scope)
    agent_org_label, agent_org_url = _required_link(metadata, "agent_org", scope=scope)
    model_label, model_url = _required_link(metadata, "model_display", scope=scope)
    model_org_label, model_org_url = _required_link(metadata, "model_org", scope=scope)
    reasoning_effort = _required_text(metadata, "reasoning_effort", scope=scope, max_length=128)
    leaderboard_release_date = _required_date(metadata, "date", scope=scope)
    display_release_date = _required_text(
        metadata,
        "display_date",
        scope=scope,
        max_length=64,
    )
    optional_release_date = (
        _required_date(metadata, "release_date", scope=scope)
        if metadata.get("release_date") is not None
        else None
    )
    status = _required_text(row, "status", scope=scope, max_length=32)
    if status not in {"display", "hide"}:
        raise HarborAdapterError(f"{scope}.status is not a supported complete/quarantined status")
    rank_raw = row.get("rank")
    if rank_raw is not None and (
        isinstance(rank_raw, bool) or not isinstance(rank_raw, int) or rank_raw <= 0
    ):
        raise HarborAdapterError(f"{scope}.rank must be null or a positive integer")
    rank = rank_raw

    n_trials = _required_nonnegative_integer(
        metrics, "n_trials", scope=f"{scope}.metrics", positive=True
    )
    row_n_trials = _required_nonnegative_integer(row, "n_trials", scope=scope, positive=True)
    if row_n_trials != n_trials:
        raise HarborAdapterError(f"{scope} has inconsistent row and metric n_trials")
    successes = _required_nonnegative_integer(metrics, "successes", scope=f"{scope}.metrics")
    if successes > n_trials:
        raise HarborAdapterError(f"{scope}.metrics.successes exceeds n_trials")
    accuracy_pct = _required_nonnegative_decimal(
        metrics,
        "accuracy",
        scope=f"{scope}.metrics",
        maximum=Decimal(100),
    )
    ci_half_width_pct = _required_nonnegative_decimal(
        metrics,
        "accuracy_ci95_half_width",
        scope=f"{scope}.metrics",
        maximum=Decimal(100),
    )
    with localcontext(POLICY_DECIMAL_CONTEXT):
        recovered_accuracy = Decimal(successes) * Decimal(100) / Decimal(n_trials)
        accuracy_difference = abs(accuracy_pct - recovered_accuracy)
        accuracy_ratio = accuracy_pct / Decimal(100)
        accuracy_lower = max(Decimal(0), accuracy_pct - ci_half_width_pct) / Decimal(100)
        accuracy_upper = min(Decimal(100), accuracy_pct + ci_half_width_pct) / Decimal(100)
    if accuracy_difference > Decimal("0.005"):
        raise HarborAdapterError(f"{scope}.metrics.accuracy is incoherent with successes/n_trials")

    total_cost_usd = _required_nonnegative_decimal(
        metrics, "total_cost_usd", scope=f"{scope}.metrics"
    )
    total_tokens = _required_nonnegative_integer(metrics, "total_tokens", scope=f"{scope}.metrics")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        cost_per_trial = total_cost_usd / Decimal(n_trials)
        tokens_per_trial = Decimal(total_tokens) / Decimal(n_trials)

    avg_duration: Decimal | None = None
    if metrics.get("avg_trial_duration_sec") is not None:
        avg_duration = _required_nonnegative_decimal(
            metrics, "avg_trial_duration_sec", scope=f"{scope}.metrics"
        )
    pass_at_k: dict[str, Decimal] = {}
    for k in range(2, 6):
        field = f"pass_at_{k}"
        if field in metrics:
            pass_at_k[field] = _required_nonnegative_decimal(
                metrics, field, scope=f"{scope}.metrics", maximum=Decimal(1)
            )
    previous_pass_rate = accuracy_ratio
    for field, pass_value in pass_at_k.items():
        if pass_value < previous_pass_rate:
            raise HarborAdapterError(f"{scope}.metrics.{field} is lower than pass-at-(k-1)")
        previous_pass_rate = pass_value

    observed_at = _required_datetime(row, "updated_at", scope=scope)
    created_at = _required_datetime(row, "created_at", scope=scope)
    if created_at > observed_at:
        raise HarborAdapterError(f"{scope}.created_at is later than updated_at")
    if observed_at > retrieved_at.astimezone(UTC):
        raise HarborAdapterError(f"{scope}.updated_at is later than captured retrieved_at")
    token_fields: dict[str, int] = {"total_tokens": total_tokens}
    for field in ("uncached_input_tokens", "cached_input_tokens", "output_tokens"):
        if field in metrics:
            token_fields[field] = _required_nonnegative_integer(
                metrics,
                field,
                scope=f"{scope}.metrics",
            )

    subject = QualitySubjectIdentity(
        row_id=row_id,
        kind=QualitySubjectKind.SINGLE_MODEL_SYSTEM,
        system_label=f"{agent_label} using {model_label}",
        model_claims=(
            QualityModelClaim(
                model_id=model_label,
                display_name=model_label,
                provider=model_org_label,
                revision=leaderboard_release_date,
                reasoning_effort=reasoning_effort,
                claims={
                    "documentation_url": model_url,
                    "organization": model_org_label,
                    "organization_url": model_org_url,
                    "leaderboard_release_date": leaderboard_release_date,
                    "upstream_optional_release_date": optional_release_date,
                },
            ),
        ),
        benchmark_agent=QualityComponentIdentity(
            id=agent_label,
            version="unreported",
            configuration={
                "organization": agent_org_label,
                "organization_url": agent_org_url,
                "product_url": agent_url,
            },
        ),
        route_disclosure=QualityRouteDisclosure.UNKNOWN,
        reasoning_claims={
            "reasoning_effort": reasoning_effort,
            # Metadata is subject-defining for a compound leaderboard system.
            # Hash every field so newly added optional identity claims cannot
            # drift while an old reconciliation continues to map.
            "source_metadata_sha256": harbor_value_sha256(metadata),
        },
    )

    measurements: list[QualityMeasurement] = [
        QualityMeasurement(
            id="terminal_bench_accuracy",
            role=QualityMeasurementRole.QUALITY,
            value=accuracy_ratio,
            unit="ratio",
            lower=accuracy_lower,
            upper=accuracy_upper,
            sample_count=n_trials,
        ),
        QualityMeasurement(
            id="harbor_reported_total_cost_usd",
            role=QualityMeasurementRole.COST,
            value=total_cost_usd,
            unit="USD",
            sample_count=n_trials,
        ),
        QualityMeasurement(
            id="harbor_reported_cost_per_trial_usd",
            role=QualityMeasurementRole.COST,
            value=cost_per_trial,
            unit="USD/trial",
            sample_count=n_trials,
        ),
        QualityMeasurement(
            id="harbor_reported_total_tokens",
            role=QualityMeasurementRole.TOKEN_USAGE,
            value=Decimal(total_tokens),
            unit="tokens",
            sample_count=n_trials,
        ),
        QualityMeasurement(
            id="harbor_reported_tokens_per_trial",
            role=QualityMeasurementRole.TOKEN_USAGE,
            value=tokens_per_trial,
            unit="tokens/trial",
            sample_count=n_trials,
        ),
    ]
    for field, value in token_fields.items():
        if field == "total_tokens":
            continue
        measurements.append(
            QualityMeasurement(
                id=f"harbor_reported_{field}",
                role=QualityMeasurementRole.TOKEN_USAGE,
                value=Decimal(value),
                unit="tokens",
                sample_count=n_trials,
            )
        )
    if avg_duration is not None:
        measurements.append(
            QualityMeasurement(
                id="harbor_avg_trial_duration_seconds",
                role=QualityMeasurementRole.LATENCY,
                value=avg_duration,
                unit="seconds/trial",
                sample_count=n_trials,
            )
        )
    for field, pass_value in pass_at_k.items():
        measurements.append(
            QualityMeasurement(
                id=field,
                role=QualityMeasurementRole.QUALITY,
                value=pass_value,
                unit="ratio",
                # Harbor does not expose the task denominator used for pass-at-k;
                # n_trials is a distinct attempt count and must not be substituted.
                sample_count=None,
            )
        )
    quality_result: QualityResult | None = QualityResult(
        primary_metric="terminal_bench_accuracy",
        measurements=tuple(measurements),
        counts=(
            QualityCount(
                id="n_trials",
                role=QualityMeasurementRole.QUALITY,
                value=n_trials,
            ),
            QualityCount(
                id="successes",
                role=QualityMeasurementRole.QUALITY,
                value=successes,
            ),
        ),
        observed_at=observed_at,
        metadata={
            "leaderboard_release_date": leaderboard_release_date,
            "display_release_date": display_release_date,
            "upstream_optional_release_date": optional_release_date,
            "harbor_rank": rank,
            "harbor_status": status,
            "source_metrics_sha256": harbor_value_sha256(metrics),
            "source_reported_token_fields": token_fields,
            "token_bucket_relationship": "unasserted_board_specific",
            "cost_basis": "source_reported_total_divided_by_n_trials",
        },
    )
    invalid_result: QualityInvalidResult | None = None
    if status != "display":
        invalid_result = QualityInvalidResult(
            code="harbor-row-not-displayed",
            detail="Harbor marked this row as hidden; no score is eligible for import.",
            selected_value_sha256=harbor_value_sha256(
                {
                    "metrics": metrics,
                    "rank": rank,
                    "status": status,
                    "updated_at": observed_at.isoformat(),
                }
            ),
        )
        quality_result = None
    return _ValidatedRow(
        row_id=row_id,
        subject=subject,
        quality_result=quality_result,
        invalid_result=invalid_result,
        agent_label=agent_label,
        agent_url=agent_url,
        agent_org_label=agent_org_label,
        agent_org_url=agent_org_url,
        model_label=model_label,
        model_url=model_url,
        model_org_label=model_org_label,
        model_org_url=model_org_url,
        reasoning_effort=reasoning_effort,
        leaderboard_release_date=leaderboard_release_date,
        display_release_date=display_release_date,
        optional_release_date=optional_release_date,
        status=status,
        rank=rank,
        n_trials=n_trials,
        successes=successes,
        accuracy_ratio=accuracy_ratio,
        accuracy_lower=accuracy_lower,
        accuracy_upper=accuracy_upper,
        total_cost_usd=total_cost_usd,
        cost_per_trial_usd=cost_per_trial,
        total_tokens=total_tokens,
        tokens_per_trial=tokens_per_trial,
        token_fields=token_fields,
        avg_trial_duration_sec=avg_duration,
        pass_at_k=pass_at_k,
        observed_at=observed_at,
        metadata=metadata,
        metrics=metrics,
    )


@dataclass(frozen=True, slots=True)
class HarborNormalizedEvidence:
    """Parsed evidence before any leaderboard subject is mapped to a route."""

    leaderboard: Mapping[str, Any]
    rows: tuple[_ValidatedRow, ...]
    evidence: QualityEvidenceSet

    @property
    def raw_sha256(self) -> str:
        return self.evidence.raw_audit.raw_sha256

    @property
    def source_identity(self) -> QualitySourceIdentity:
        return self.evidence.source_identity

    @property
    def source_identity_sha256(self) -> str:
        return self.evidence.source_identity_sha256

    @property
    def retrieved_at(self) -> datetime:
        return self.evidence.raw_audit.retrieved_at

    def document(self) -> Mapping[str, Any]:
        return self.evidence.model_dump(mode="json")


def _coerce_import_config(
    source: HarborTerminalBenchImportConfig | bytes | str | Path,
) -> HarborTerminalBenchImportConfig:
    if isinstance(source, HarborTerminalBenchImportConfig):
        try:
            return HarborTerminalBenchImportConfig.model_validate(source.model_dump(mode="json"))
        except ValidationError as exc:
            raise HarborAdapterError("invalid Harbor import configuration") from exc
    return load_harbor_terminal_bench_mapping(source)[0]


def _quality_source_identity(
    leaderboard: Mapping[str, Any],
    source_scope: Mapping[str, Any],
) -> QualitySourceIdentity:
    name = str(leaderboard["name"])
    leaderboard_id = str(leaderboard["id"])
    package_id = str(leaderboard["package_id"])
    dataset_ids = list(leaderboard["dataset_version_ids"])
    metadata_schema_sha256 = str(source_scope["metadata_schema_sha256"])
    metrics_schema_sha256 = str(source_scope["metrics_schema_sha256"])
    metadata_contract_sha256 = str(source_scope["metadata_contract_sha256"])
    metrics_contract_sha256 = str(source_scope["metrics_contract_sha256"])
    release_date_column_sha256 = str(source_scope["release_date_column_sha256"])
    return QualitySourceIdentity(
        source_id=f"harbor-leaderboard-{leaderboard_id}",
        source_version=name,
        benchmark=QualityComponentIdentity(
            id="terminal-bench",
            version=name,
            configuration={
                "leaderboard_id": leaderboard_id,
                "package": "terminal-bench/terminal-bench",
                "package_id": package_id,
            },
        ),
        dataset=QualityComponentIdentity(
            id="terminal-bench-dataset-set",
            version=f"sha256:{harbor_value_sha256({'dataset_version_ids': dataset_ids})}",
            configuration={"dataset_version_ids": dataset_ids},
        ),
        split="public-leaderboard",
        evaluator_harness=QualityComponentIdentity(
            id="terminal-bench-harbor-submission",
            version=name,
            configuration={"leaderboard_id": leaderboard_id},
        ),
        scorer=QualityComponentIdentity(
            id="terminal-bench-accuracy",
            version=f"sha256:{metrics_schema_sha256}",
            configuration={
                "metrics_schema_sha256": metrics_schema_sha256,
                "parsed_metrics_contract_sha256": metrics_contract_sha256,
                "parsed_metric_fields": source_scope["parsed_metric_fields"],
                "rank_by": source_scope["rank_by"],
            },
        ),
        protocol=QualityComponentIdentity(
            id="harbor-curated-public-leaderboard-read",
            version="v1",
            configuration={"visibility": "public"},
        ),
        projection=QualityComponentIdentity(
            id="harbor-terminal-bench",
            version=PARSER_VERSION,
            configuration={
                "metadata_schema_sha256": metadata_schema_sha256,
                "parsed_metadata_contract_sha256": metadata_contract_sha256,
                "parsed_metadata_fields": source_scope["parsed_metadata_fields"],
                "release_date_column_sha256": release_date_column_sha256,
                "metadata.date_semantics": "leaderboard Release Date column",
                "metric_roles": {
                    "accuracy_and_pass_at_k": "quality",
                    "avg_trial_duration_sec": "latency",
                    "token_fields": "token_usage",
                    "total_cost_usd": "cost",
                },
            },
        ),
        scope=dict(source_scope),
    )


def _normalize_harbor_terminal_bench_bytes(
    raw: bytes,
    import_config: HarborTerminalBenchImportConfig | bytes | str | Path,
    *,
    retrieved_at: datetime,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> HarborNormalizedEvidence:
    """Parse a bounded capture into identity-separated, route-neutral evidence."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise HarborAdapterError("max_bytes must be an integer")
    if not 1 <= max_bytes <= HARD_MAX_SOURCE_BYTES:
        raise HarborAdapterError(f"max_bytes must be between 1 and {HARD_MAX_SOURCE_BYTES}")
    if not isinstance(raw, bytes):
        raise HarborAdapterError("Harbor snapshot content must be bytes")
    if len(raw) > max_bytes:
        raise HarborAdapterError(f"Harbor snapshot exceeds the {max_bytes}-byte limit")
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
        raise HarborAdapterError("retrieved_at must include a timezone")
    config = _coerce_import_config(import_config)
    retrieved = retrieved_at.astimezone(UTC)
    leaderboard, raw_rows, source_scope = _leaderboard_and_rows(raw)
    board_updated_at = _required_datetime(leaderboard, "updated_at", scope="leaderboard")
    board_created_at = _required_datetime(leaderboard, "created_at", scope="leaderboard")
    if board_created_at > board_updated_at:
        raise HarborAdapterError("leaderboard.created_at is later than updated_at")
    if board_updated_at > retrieved:
        raise HarborAdapterError("leaderboard.updated_at is later than captured retrieved_at")
    leaderboard_id = _required_uuid(leaderboard, "id", scope="leaderboard")
    metadata_schema = _required_object(leaderboard, "metadata_schema", scope="leaderboard")
    metrics_schema = _required_object(leaderboard, "metrics_schema", scope="leaderboard")
    rows: list[_ValidatedRow] = []
    seen_ids: set[str] = set()
    for raw_row in raw_rows:
        row_id = _required_uuid(raw_row, "id", scope="row")
        if row_id in seen_ids:
            raise HarborAdapterError(f"duplicate Harbor row id {row_id!r}")
        seen_ids.add(row_id)
        rows.append(
            _validate_row(
                raw_row,
                leaderboard_id=leaderboard_id,
                metadata_schema=metadata_schema,
                metrics_schema=metrics_schema,
                retrieved_at=retrieved,
            )
        )
    rows.sort(key=lambda row: row.row_id)
    source_identity = _quality_source_identity(leaderboard, source_scope)
    evidence = QualityEvidenceSet(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        raw_audit=QualityRawAudit(
            source_locator=config.source_url,
            raw_sha256=quality_raw_sha256(raw),
            retrieved_at=retrieved,
            upstream_revision=str(leaderboard["updated_at"]),
            capture_method=(f"harbor/{config.capture_tool_version}:hub-leaderboard-show-json"),
            parser_implementation=QualityComponentIdentity(
                id="model-skyline-harbor-terminal-bench",
                version=PARSER_VERSION,
                configuration={
                    "package": "terminal-bench/terminal-bench",
                    "projection_id": source_identity.projection.id,
                },
            ),
            metadata={
                "leaderboard_id": leaderboard_id,
                "methodology_url": config.methodology_url,
            },
        ),
        source_identity=source_identity,
        rights=config.rights,
        rows=tuple(row.evidence_row for row in rows),
    )
    return HarborNormalizedEvidence(
        leaderboard=leaderboard,
        rows=tuple(rows),
        evidence=evidence,
    )


def normalize_harbor_terminal_bench_bytes(
    raw: bytes,
    import_config: HarborTerminalBenchImportConfig | bytes | str | Path,
    *,
    retrieved_at: datetime,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> HarborNormalizedEvidence:
    """Parse a capture while keeping untrusted Pydantic inputs out of errors."""

    try:
        return _normalize_harbor_terminal_bench_bytes(
            raw,
            import_config,
            retrieved_at=retrieved_at,
            max_bytes=max_bytes,
        )
    except ValidationError as exc:
        raise HarborAdapterError("Harbor data failed normalized evidence validation") from exc


def inspect_harbor_terminal_bench_bytes(
    raw: bytes,
    import_config: HarborTerminalBenchImportConfig | bytes | str | Path,
    *,
    retrieved_at: datetime,
) -> dict[str, Any]:
    """Return mapping-review locators without making route assertions."""

    config = _coerce_import_config(import_config)
    normalized = normalize_harbor_terminal_bench_bytes(
        raw,
        config,
        retrieved_at=retrieved_at,
    )
    evidence = normalized.evidence
    return {
        "schema_version": "model-skyline/harbor-terminal-bench-inventory/v1alpha1",
        "parser_version": PARSER_VERSION,
        "raw_sha256": evidence.raw_audit.raw_sha256,
        "raw_audit_sha256": evidence.raw_audit_sha256,
        "source_identity_sha256": evidence.source_identity_sha256,
        "rights_sha256": evidence.rights_sha256,
        "reconciliation_sha256": config.reconciliation.content_sha256,
        "leaderboard_id": str(normalized.leaderboard["id"]),
        "package": str(normalized.leaderboard["package"]),
        "name": str(normalized.leaderboard["name"]),
        "dataset_version_ids": list(normalized.leaderboard["dataset_version_ids"]),
        "rows": [
            {
                "row_id": row.row_id,
                "subject_identity_sha256": row.subject_identity_sha256,
                "result_sha256": row.result_sha256,
                "result_state": "valid" if row.quality_result is not None else "invalid",
                "route_disclosure": row.subject.route_disclosure.value,
                "agent_label": row.agent_label,
                "agent_url": row.agent_url,
                "agent_organization": row.agent_org_label,
                "agent_organization_url": row.agent_org_url,
                "model_label": row.model_label,
                "model_url": row.model_url,
                "model_organization": row.model_org_label,
                "model_organization_url": row.model_org_url,
                "reasoning_effort": row.reasoning_effort,
                "leaderboard_release_date": row.leaderboard_release_date,
                "display_release_date": row.display_release_date,
                "upstream_optional_release_date": row.optional_release_date,
                "status": row.status,
            }
            for row in normalized.rows
        ],
    }


def inspect_harbor_terminal_bench_snapshot(
    path: str | Path,
    import_config: HarborTerminalBenchImportConfig | bytes | str | Path,
    *,
    retrieved_at: datetime,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise HarborAdapterError("max_bytes must be an integer")
    if not 1 <= max_bytes <= HARD_MAX_SOURCE_BYTES:
        raise HarborAdapterError(f"max_bytes must be between 1 and {HARD_MAX_SOURCE_BYTES}")
    raw = _read_bounded_regular_file(Path(path), max_bytes, label="Harbor leaderboard snapshot")
    return inspect_harbor_terminal_bench_bytes(
        raw,
        import_config,
        retrieved_at=retrieved_at,
    )


def _dataset_version_ids(source_identity: QualitySourceIdentity) -> tuple[str, ...]:
    value = source_identity.dataset.configuration.get("dataset_version_ids")
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise HarborAdapterError("normalized dataset identity is internally inconsistent")
    return tuple(str(item) for item in value)


def _source_reference(
    config: HarborTerminalBenchImportConfig,
    *,
    evidence: QualityEvidenceSet,
) -> SourceReference:
    dataset_ids = ", ".join(_dataset_version_ids(evidence.source_identity))
    return SourceReference(
        id=evidence.source_identity.source_id,
        version=f"source-identity-sha256:{evidence.source_identity_sha256}",
        url=config.source_url,
        terms_url=config.rights.terms_locator,
        license=config.rights.license_expression,
        methodology=(
            "Operator-captured Harbor public leaderboard read response; "
            f"capture tool harbor {config.capture_tool_version}; parser {PARSER_VERSION}; "
            f"dataset-version UUIDs {dataset_ids}; methodology {config.methodology_url}; "
            f"source-identity sha256:{evidence.source_identity_sha256}. Exact raw bytes are "
            "content-addressed but not copied into the generated project, and ModelSkyline "
            "does not independently attest capture authenticity."
        ),
        raw_sha256=evidence.raw_audit.raw_sha256,
        retrieved_at=evidence.raw_audit.retrieved_at,
    )


def _offering_observation(
    mapped: MappedQualityRow,
    *,
    evidence_row: QualityEvidenceRow,
    source_identity: QualitySourceIdentity,
    source: SourceReference,
    leaderboard: Mapping[str, Any],
) -> OfferingObservation:
    signals = {
        measurement.id: Observation(
            value=measurement.value,
            lower=measurement.lower,
            upper=measurement.upper,
            unit=measurement.unit,
            sample_count=measurement.sample_count,
            observed_at=mapped.result.observed_at,
        )
        for measurement in mapped.result.measurements
    }
    counts = {item.id: item.value for item in mapped.result.counts}
    model_claim = evidence_row.subject.model_claims[0]
    benchmark_agent = evidence_row.subject.benchmark_agent
    return OfferingObservation(
        offering=mapped.offering,
        signals=signals,
        metadata={
            "benchmark": "terminal-bench",
            "compound_system_result": True,
            "leaderboard_id": str(leaderboard["id"]),
            "leaderboard_package": str(leaderboard["package"]),
            "leaderboard_name": str(leaderboard["name"]),
            "dataset_version_ids": list(leaderboard["dataset_version_ids"]),
            "harbor_row_id": mapped.row_id,
            "source_identity_sha256": mapped.source_identity_sha256,
            "subject_identity_sha256": mapped.subject_identity_sha256,
            "evidence_result_sha256": mapped.evidence_result_sha256,
            "mapped_result_sha256": mapped.result_sha256,
            "rights_sha256": mapped.rights_sha256,
            "benchmark_agent_label": benchmark_agent.id if benchmark_agent else None,
            "benchmark_agent_version": benchmark_agent.version if benchmark_agent else None,
            "benchmark_agent_claims": (
                dict(benchmark_agent.configuration) if benchmark_agent else {}
            ),
            "benchmark_harness_scope": source_identity.evaluator_harness.id,
            "source_model_label": model_claim.display_name or model_claim.model_id,
            "source_model_claims": dict(model_claim.claims),
            "source_reasoning_effort": model_claim.reasoning_effort,
            "source_quality_counts": counts,
            "route_mapping_relationship": mapped.relationship.value,
            "quality_only_projection": (mapped.relationship.value == "reviewed_quality_projection"),
            "route_specific_telemetry_projected": (
                mapped.relationship.value == "exact_subject_route"
            ),
        },
        default_source=source,
    )


def _metric_description(metric_id: str) -> str:
    descriptions = {
        "terminal_bench_accuracy": (
            "Harbor-reported Terminal-Bench trial accuracy with the source 95% interval. "
            "For reviewed_quality_projection mappings this is the only primary score "
            "attributed to the reviewed offering."
        ),
        "harbor_reported_total_cost_usd": (
            "Harbor source-reported aggregate benchmark cost; eligible for a route only "
            "when exact_subject_route reconciliation succeeds."
        ),
        "harbor_reported_cost_per_trial_usd": (
            "Harbor source-reported total cost divided by n_trials; no token/cache price "
            "reconstruction. Eligible only for an exact subject route."
        ),
        "harbor_reported_total_tokens": (
            "Harbor source-reported aggregate token count, without summing cache buckets."
        ),
        "harbor_reported_tokens_per_trial": (
            "Harbor source-reported total_tokens divided by n_trials."
        ),
        "harbor_avg_trial_duration_seconds": (
            "Harbor source-reported mean trial duration for the exact benchmark subject."
        ),
    }
    if metric_id.startswith("pass_at_"):
        return (
            "Harbor-reported Terminal-Bench pass-at-k quality measurement; the current "
            "leaderboard does not expose its exact task denominator."
        )
    if metric_id.startswith("harbor_reported_") and metric_id.endswith("_tokens"):
        return "Harbor source-reported token bucket retained under its upstream semantics."
    return descriptions.get(metric_id, "Normalized Harbor Terminal-Bench measurement.")


def _build_project_config(
    catalog: ObservationCatalog,
    *,
    source: SourceReference,
    source_identity: QualitySourceIdentity,
) -> ProjectConfig:
    observations: dict[str, Observation] = {}
    for offering in catalog.offerings:
        for metric_id, observation in offering.signals.items():
            observations.setdefault(metric_id, observation)
    metrics = {
        metric_id: SignalMetric(
            kind="signal",
            signal=metric_id,
            unit=observation.unit,
            description=_metric_description(metric_id),
            requirements=ObservationRequirements(
                minimum_samples=(1 if metric_id == "terminal_bench_accuracy" else None),
                require_bounds=metric_id == "terminal_bench_accuracy",
                require_source=True,
            ),
        )
        for metric_id, observation in sorted(observations.items())
    }
    workload_id = catalog.workload.id
    dataset_ids = _dataset_version_ids(source_identity)
    return ProjectConfig(
        schema_version="model-skyline/v1alpha1",
        workloads={
            workload_id: WorkloadProfile(
                unit="trial",
                version=catalog.workload.version,
                harness=(
                    f"{source_identity.evaluator_harness.id}/"
                    f"{source_identity.evaluator_harness.version}"
                ),
                cohort=(
                    f"{source_identity.benchmark.configuration['package']}/"
                    f"{source_identity.source_version}@"
                    + ",".join(str(item) for item in dataset_ids)
                ),
                benchmark=source_identity.benchmark.id,
                description=(
                    "Reviewed quality projections from exact Harbor Terminal-Bench row "
                    "subjects. The current leaderboard does not disclose execution routes."
                ),
                assumptions={
                    "result_identity": "compound_agent_model_harness_system",
                    "benchmark_harness_location": "workload_and_subject_evidence",
                    "offering_agent_harness": "production_route_only",
                    "route_identity": "operator_reviewed_relationship",
                    "accuracy_interval": "source_reported_95_percent_interval",
                    "reviewed_quality_projection": (
                        "quality_fields_only; route cost latency and tokens remain evidence"
                    ),
                    "cost_reconstruction_from_tokens": "forbidden",
                    "token_bucket_relationship": "unasserted_board_specific",
                    "capture_authenticity": "operator_asserted_not_independently_attested",
                },
                sources=[source],
            )
        },
        metrics=metrics,
        # One quality metric is not a Pareto frontier. Current Harbor rows cannot
        # safely contribute a route-specific competing axis.
        frontiers={},
    )


def import_harbor_terminal_bench_bytes(
    raw: bytes,
    mapping_source: bytes | str | Path,
    *,
    retrieved_at: datetime,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    allow_partial: bool = False,
) -> HarborTerminalBenchImportResult:
    """Normalize and reconcile one captured Harbor response without label matching."""

    if not isinstance(allow_partial, bool):
        raise HarborAdapterError("allow_partial must be a boolean")
    config, mapping_sha256, mapping_document = load_harbor_terminal_bench_mapping(mapping_source)
    if not config.reconciliation.entries and not allow_partial:
        raise HarborAdapterError(
            "Harbor import requires at least one reviewed reconciliation entry; "
            "use inspect first or explicitly allow a partial audit bundle"
        )
    normalized = normalize_harbor_terminal_bench_bytes(
        raw,
        config,
        retrieved_at=retrieved_at,
        max_bytes=max_bytes,
    )
    evidence = normalized.evidence
    report = reconcile_quality_evidence(
        evidence,
        config.reconciliation,
        publication_scope=config.publication_scope,
    )
    reviewed_row_ids = {entry.row_id for entry in config.reconciliation.entries}
    failed_reviewed_rows = [
        record
        for record in report.records
        if record.row_id in reviewed_row_ids and record.outcome is not QualityImportOutcome.MAPPED
    ]
    if failed_reviewed_rows and not allow_partial:
        first = failed_reviewed_rows[0]
        raise HarborAdapterError(
            f"reviewed Harbor row {first.row_id!r} did not map ({first.outcome.value}); "
            f"{len(failed_reviewed_rows)} reviewed row(s) failed"
        )
    source = _source_reference(config, evidence=evidence)
    evidence_rows = {row.row_id: row for row in evidence.rows}
    offerings = sorted(
        (
            _offering_observation(
                mapped,
                evidence_row=evidence_rows[mapped.row_id],
                source_identity=evidence.source_identity,
                source=source,
                leaderboard=normalized.leaderboard,
            )
            for mapped in report.mapped_rows
        ),
        key=lambda item: item.offering.offering_id,
    )
    workload_id = (
        f"harbor:{normalized.leaderboard['package']}/"
        f"{normalized.leaderboard['name']}:{normalized.leaderboard['id']}"
    )
    catalog = ObservationCatalog(
        schema_version="model-skyline/v1alpha1",
        workload=WorkloadReference(
            id=workload_id,
            version=f"source-identity-sha256:{evidence.source_identity_sha256}",
            unit="trial",
        ),
        offerings=offerings,
    )
    return HarborTerminalBenchImportResult(
        catalog=catalog,
        config=_build_project_config(
            catalog,
            source=source,
            source_identity=evidence.source_identity,
        ),
        source=source,
        mapping=config,
        mapping_sha256=mapping_sha256,
        mapping_document=mapping_document,
        evidence=evidence,
        report=report,
        rows_seen=len(evidence.rows),
        allow_partial=allow_partial,
    )


def import_harbor_terminal_bench(
    snapshot: str | Path,
    mapping: bytes | str | Path,
    *,
    retrieved_at: datetime,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    allow_partial: bool = False,
) -> HarborTerminalBenchImportResult:
    """Read a bounded local capture and reconcile only explicitly reviewed rows."""

    if not isinstance(allow_partial, bool):
        raise HarborAdapterError("allow_partial must be a boolean")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise HarborAdapterError("max_bytes must be an integer")
    if not 1 <= max_bytes <= HARD_MAX_SOURCE_BYTES:
        raise HarborAdapterError(f"max_bytes must be between 1 and {HARD_MAX_SOURCE_BYTES}")
    raw = _read_bounded_regular_file(
        Path(snapshot),
        max_bytes,
        label="Harbor leaderboard snapshot",
    )
    return import_harbor_terminal_bench_bytes(
        raw,
        mapping,
        retrieved_at=retrieved_at,
        max_bytes=max_bytes,
        allow_partial=allow_partial,
    )


def _render_project_config(config: ProjectConfig) -> str:
    return yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def write_harbor_terminal_bench_import(
    result: HarborTerminalBenchImportResult,
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Atomically publish evidence, reconciliation report, catalog, and policy."""

    rendered = {
        CATALOG_FILENAME: dump_json(result.catalog),
        CONFIG_FILENAME: _render_project_config(result.config),
        MAPPING_FILENAME: result.mapping_document,
        EVIDENCE_FILENAME: dump_json(result.evidence),
        IMPORT_REPORT_FILENAME: dump_json(result.report),
    }
    output_sha256 = {
        name: hashlib.sha256(payload.encode("utf-8")).hexdigest()
        for name, payload in rendered.items()
    }
    rendered[MANIFEST_FILENAME] = (
        json.dumps(
            result.manifest(output_sha256=output_sha256),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        return publish_text_bundle(
            output_directory,
            rendered,
            manifest_name=MANIFEST_FILENAME,
            overwrite=overwrite,
            directory_mode=0o700,
            file_mode=0o600,
        )
    except BundlePublicationError as exc:
        raise HarborAdapterError(
            f"cannot write Harbor Terminal-Bench import to {output_directory}: {exc}"
        ) from exc
