"""Fail-closed normalization for the official SWE-bench website feed.

The website JSON is an unversioned presentation artifact, not a route catalog.
This adapter therefore consumes one explicitly selected ``bash-only``
mini-SWE-agent cohort, validates its per-instance accounting, and emits only
route-free :class:`~model_skyline.quality_evidence.QualityEvidenceSet` rows.
Upstream model and organization labels are evidence claims.  They never become
``OfferingKey`` values without a separate, exact reviewed reconciliation.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from model_skyline.adapters._publication import BundlePublicationError, publish_text_bundle
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT, content_hash
from model_skyline.io import dump_json
from model_skyline.models import MAX_SAFE_INTEGER
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

SWE_BENCH_WEBSITE_COMMIT: Final = "ac7583972e21606e9dad4447a9c447685c03b57a"
SWE_BENCH_WEBSITE_SHA256: Final = "fa4b61d3167dfe99e1a834e007a38372c5bac07b7627f8e2c3904fb48cd4a006"
SWE_BENCH_WEBSITE_URL: Final = (
    "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/"
    f"{SWE_BENCH_WEBSITE_COMMIT}/data/leaderboards.json"
)
SWE_BENCH_WEBSITE_LICENSE_URL: Final = (
    f"https://github.com/SWE-bench/swe-bench.github.io/blob/{SWE_BENCH_WEBSITE_COMMIT}/LICENSE"
)
SWE_BENCH_BASH_ONLY_METHODOLOGY_URL: Final = "https://www.swebench.com/bash-only.html"
SWE_BENCH_DEFAULT_HARNESS_VERSION: Final = "2.0.0"
SWE_BENCH_ADAPTER_ID: Final = "model-skyline/swe-bench-website-bash-only"
SWE_BENCH_ADAPTER_VERSION: Final = "1"
SWE_BENCH_EXPECTED_INSTANCES: Final = 500

DEFAULT_MAX_SOURCE_BYTES: Final = 12_000_000
HARD_MAX_SOURCE_BYTES: Final = 32_000_000
DEFAULT_TIMEOUT_SECONDS: Final = 30.0
MAX_TIMEOUT_SECONDS: Final = 60.0
MAX_JSON_DEPTH: Final = 16
MAX_JSON_NODES: Final = 1_000_000
MAX_JSON_STRING_LENGTH: Final = 65_536
MAX_LEADERBOARD_ROWS: Final = 10_000

EVIDENCE_FILENAME: Final = "quality-evidence.json"
INVENTORY_FILENAME: Final = "inventory.json"
MANIFEST_FILENAME: Final = "capture.json"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_OFFICIAL_SOURCE_PATH_RE = re.compile(
    r"^/SWE-bench/swe-bench\.github\.io/(?P<revision>[0-9a-f]{40})/"
    r"data/leaderboards\.json$"
)
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_MODEL_TAG_PREFIX = "Model: "
_ATTEMPTS_TAG_RE = re.compile(r"^System: Attempts - (?P<value>1|2\+)$")
_ROW_REQUIRED_FIELDS = frozenset(
    {
        "agent",
        "agent_org",
        "checked",
        "date",
        "folder",
        "logo",
        "logs",
        "model_display",
        "model_org",
        "model_release_date",
        "name",
        "os_model",
        "os_system",
        "reasoning_effort",
        "resolved",
        "site",
        "tags",
        "trajs",
        "trajs_docent",
        "warning",
    }
)
_ROW_OPTIONAL_FIELDS = frozenset(
    {
        "cost",
        "instance_calls",
        "instance_cost",
        "mini-swe-agent_version",
        "per_instance_details",
    }
)
_DETAIL_REQUIRED_FIELDS = frozenset({"resolved"})
_DETAIL_OPTIONAL_FIELDS = frozenset({"api_calls", "cost"})
_NUMERIC_TOLERANCE = Decimal("0.000001")
_MAX_ACCOUNTING_VALUE = Decimal("1e24")
_RIGHTS_REVIEWED_AT = datetime(2026, 8, 31, tzinfo=UTC)


class SweBenchAdapterError(ValueError):
    """The SWE-bench source cannot be acquired or normalized safely."""


class SweBenchSourceIdentityMode(StrEnum):
    """Whether semantic source identity may outlive one exact raw capture."""

    RAW_BOUND_OPERATOR = "raw_bound_operator"
    OFFICIAL_SEMANTIC = "official_semantic"


@dataclass(frozen=True, slots=True)
class LoadedSweBenchSource:
    raw: bytes
    raw_sha256: str
    retrieved_at: datetime
    source_locator: str
    upstream_revision: str
    source_identity_mode: SweBenchSourceIdentityMode


@dataclass(frozen=True, slots=True)
class SweBenchCapture:
    evidence: QualityEvidenceSet
    rows_seen: int
    valid_rows: int
    invalid_rows: int
    harness_version: str

    def inventory(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for row in self.evidence.rows:
            result_state = (
                {"status": "valid", "result_sha256": row.result_sha256}
                if row.result is not None
                else {
                    "status": "invalid",
                    "code": row.invalid_result.code if row.invalid_result else "invalid",
                    "result_sha256": row.result_sha256,
                }
            )
            rows.append(
                {
                    "row_id": row.row_id,
                    "subject_identity_sha256": row.subject_identity_sha256,
                    "system_label": row.subject.system_label,
                    "benchmark_agent": (
                        row.subject.benchmark_agent.model_dump(mode="json")
                        if row.subject.benchmark_agent is not None
                        else None
                    ),
                    "model_claims": [
                        claim.model_dump(mode="json") for claim in row.subject.model_claims
                    ],
                    "route_disclosure": row.subject.route_disclosure.value,
                    "result": result_state,
                }
            )
        return {
            "schema_version": "model-skyline/swe-bench-inventory/v1alpha1",
            "adapter_id": SWE_BENCH_ADAPTER_ID,
            "adapter_version": SWE_BENCH_ADAPTER_VERSION,
            "raw_audit_sha256": self.evidence.raw_audit_sha256,
            "source_identity_sha256": self.evidence.source_identity_sha256,
            "rights_sha256": self.evidence.rights_sha256,
            "cohort": {
                "leaderboard": "bash-only",
                "mini_swe_agent_version": self.harness_version,
                "expected_instances": SWE_BENCH_EXPECTED_INSTANCES,
            },
            "counts": {
                "seen": self.rows_seen,
                "valid": self.valid_rows,
                "invalid": self.invalid_rows,
            },
            "rows": rows,
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "model-skyline/swe-bench-capture/v1alpha1",
            "adapter_id": SWE_BENCH_ADAPTER_ID,
            "adapter_version": SWE_BENCH_ADAPTER_VERSION,
            "source": {
                "locator": self.evidence.raw_audit.source_locator,
                "upstream_revision": self.evidence.raw_audit.upstream_revision,
                "raw_sha256": self.evidence.raw_audit.raw_sha256,
                "retrieved_at": self.evidence.raw_audit.retrieved_at.isoformat(),
            },
            "source_identity_sha256": self.evidence.source_identity_sha256,
            "rights": self.evidence.rights.model_dump(mode="json"),
            "cohort": {
                "leaderboard": "bash-only",
                "mini_swe_agent_version": self.harness_version,
                "expected_instances": SWE_BENCH_EXPECTED_INSTANCES,
            },
            "rows": {
                "seen": self.rows_seen,
                "valid": self.valid_rows,
                "invalid": self.invalid_rows,
            },
            "outputs": {
                "evidence": EVIDENCE_FILENAME,
                "inventory": INVENTORY_FILENAME,
            },
            "warnings": [
                "The website feed is an unversioned presentation contract; this adapter is "
                "pinned to exact source bytes and fails on unknown row fields.",
                "Only rows with exactly 500 valid per-instance records and a coherent aggregate "
                "score become valid quality results. Incoherent cost or API-call aggregates are "
                "dropped without discarding the recomputed quality score.",
                "Model and organization labels are route-free claims. They never select a "
                "provider offering without exact reviewed reconciliation.",
                "The upstream website is CC-BY-NC-4.0. Default rights prohibit publication "
                "until an operator completes an applicable rights review.",
                "Source-reported costs describe the benchmark subject and are removed by a "
                "reviewed_quality_projection reconciliation.",
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


def _preflight_json_structure(raw: bytes) -> None:
    """Bound depth and approximate value count before allocating a JSON tree."""

    depth = 0
    nodes = 1
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in {0x7B, 0x5B}:  # { [
            depth += 1
            nodes += 1
            if depth > MAX_JSON_DEPTH:
                raise SweBenchAdapterError("SWE-bench JSON exceeds the nesting limit")
        elif byte in {0x3A, 0x2C}:  # : ,
            nodes += 1
            if nodes > MAX_JSON_NODES:
                raise SweBenchAdapterError("SWE-bench JSON exceeds the structural token limit")
        elif byte in {0x7D, 0x5D}:  # } ]
            depth -= 1
            if depth < 0:
                break


def _validate_json_shape(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise SweBenchAdapterError("SWE-bench JSON exceeds the structural token limit")
        if depth > MAX_JSON_DEPTH:
            raise SweBenchAdapterError("SWE-bench JSON exceeds the nesting limit")
        if isinstance(current, Mapping):
            for key, child in current.items():
                if not isinstance(key, str) or len(key) > MAX_JSON_STRING_LENGTH:
                    raise SweBenchAdapterError("SWE-bench JSON contains an invalid object key")
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str) and len(current) > MAX_JSON_STRING_LENGTH:
            raise SweBenchAdapterError("SWE-bench JSON contains an oversized string")


def _decode_source(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise SweBenchAdapterError("SWE-bench source must be bytes")
    if len(raw) > HARD_MAX_SOURCE_BYTES:
        raise SweBenchAdapterError("SWE-bench source exceeds the hard byte limit")
    _preflight_json_structure(raw)
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
        raise SweBenchAdapterError("SWE-bench source is not valid bounded JSON") from None
    _validate_json_shape(value)
    if not isinstance(value, Mapping) or set(value) != {"leaderboards"}:
        raise SweBenchAdapterError("SWE-bench source has an unsupported top-level schema")
    return value


def _normalize_timestamp(value: datetime | None, *, required: bool = False) -> datetime:
    if value is None:
        if required:
            raise SweBenchAdapterError("retrieved_at is required for a local SWE-bench source")
        return datetime.now(UTC)
    if value.tzinfo is None:
        raise SweBenchAdapterError("retrieved_at must include a timezone")
    return value.astimezone(UTC)


def _normalize_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if _SHA256_RE.fullmatch(value) is None:
        raise SweBenchAdapterError("expected_sha256 must contain 64 hexadecimal characters")
    return value.lower()


def _validate_max_bytes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SweBenchAdapterError("max_bytes must be an integer")
    if not 1 <= value <= HARD_MAX_SOURCE_BYTES:
        raise SweBenchAdapterError(f"max_bytes must be between 1 and {HARD_MAX_SOURCE_BYTES}")
    return value


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweBenchAdapterError("timeout_seconds must be a number")
    if not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise SweBenchAdapterError(
            f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS}"
        )
    return float(value)


def _validate_remote_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SweBenchAdapterError("SWE-bench source URL is invalid") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise SweBenchAdapterError("remote SWE-bench sources must use absolute HTTPS URLs")
    if parsed.username is not None or parsed.password is not None:
        raise SweBenchAdapterError("SWE-bench source URL cannot contain user information")
    if parsed.query or parsed.fragment:
        raise SweBenchAdapterError("SWE-bench source URL cannot contain a query or fragment")
    if port not in {None, 443}:
        raise SweBenchAdapterError("SWE-bench source URL must use the default HTTPS port")
    if parsed.hostname.casefold() != "raw.githubusercontent.com":
        raise SweBenchAdapterError(
            "remote SWE-bench sources must use the official raw-content host"
        )
    path_match = _OFFICIAL_SOURCE_PATH_RE.fullmatch(parsed.path)
    if path_match is None:
        raise SweBenchAdapterError(
            "remote SWE-bench sources must name an exact official repository revision"
        )
    return value, path_match.group("revision")


def _read_regular_file(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SweBenchAdapterError("local SWE-bench source must be a regular file")
        if before.st_size > maximum:
            raise SweBenchAdapterError("local SWE-bench source exceeds the byte limit")
        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > maximum:
                raise SweBenchAdapterError("local SWE-bench source exceeds the byte limit")
        raw = b"".join(chunks)
        if len(raw) > maximum:
            raise SweBenchAdapterError("local SWE-bench source exceeds the byte limit")
        descriptor_after = os.fstat(descriptor)
        path_after = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(descriptor_after.st_mode)
            or not stat.S_ISREG(path_after.st_mode)
            or before.st_dev != descriptor_after.st_dev
            or before.st_ino != descriptor_after.st_ino
            or before.st_size != descriptor_after.st_size
            or before.st_mtime_ns != descriptor_after.st_mtime_ns
            or before.st_ctime_ns != descriptor_after.st_ctime_ns
            or descriptor_after.st_dev != path_after.st_dev
            or descriptor_after.st_ino != path_after.st_ino
            or descriptor_after.st_size != path_after.st_size
            or descriptor_after.st_mtime_ns != path_after.st_mtime_ns
            or descriptor_after.st_ctime_ns != path_after.st_ctime_ns
            or len(raw) != descriptor_after.st_size
        ):
            raise SweBenchAdapterError("local SWE-bench source changed while being read")
        return raw
    except SweBenchAdapterError:
        raise
    except OSError:
        raise SweBenchAdapterError("cannot read local SWE-bench source") from None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


def _fetch_remote(url: str, maximum: int, timeout_seconds: float) -> bytes:
    deadline = time.monotonic() + timeout_seconds
    try:
        with (
            httpx.Client(
                timeout=httpx.Timeout(timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code < 200 or response.status_code >= 300:
                raise SweBenchAdapterError(f"SWE-bench source returned HTTP {response.status_code}")
            if response.headers.get("content-encoding", "identity").casefold() not in {
                "",
                "identity",
            }:
                raise SweBenchAdapterError("SWE-bench source returned compressed content")
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError:
                    raise SweBenchAdapterError(
                        "SWE-bench source returned an invalid Content-Length"
                    ) from None
                if declared_size < 0 or declared_size > maximum:
                    raise SweBenchAdapterError("SWE-bench source exceeds the byte limit")
            content = bytearray()
            for chunk in response.iter_raw():
                if time.monotonic() > deadline:
                    raise SweBenchAdapterError("SWE-bench source read exceeded its deadline")
                content.extend(chunk)
                if len(content) > maximum:
                    raise SweBenchAdapterError("SWE-bench source exceeds the byte limit")
            return bytes(content)
    except SweBenchAdapterError:
        raise
    except httpx.HTTPError:
        raise SweBenchAdapterError("cannot fetch SWE-bench source") from None


def load_swe_bench_source(
    source: str | Path = SWE_BENCH_WEBSITE_URL,
    *,
    expected_sha256: str | None = None,
    source_revision: str | None = None,
    retrieved_at: datetime | None = None,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> LoadedSweBenchSource:
    """Load exact source bytes from a local capture or allowlisted HTTPS URL."""

    maximum = _validate_max_bytes(max_bytes)
    timeout = _validate_timeout(timeout_seconds)
    raw_source = str(source)
    is_pinned_default = raw_source == SWE_BENCH_WEBSITE_URL
    expected = _normalize_sha256(expected_sha256)
    if expected is None and is_pinned_default:
        expected = SWE_BENCH_WEBSITE_SHA256
    if expected is None:
        raise SweBenchAdapterError(
            "expected_sha256 is required for every non-default SWE-bench source"
        )
    is_remote = "://" in raw_source
    if is_remote:
        source_url, url_revision = _validate_remote_url(raw_source)
        raw = _fetch_remote(source_url, maximum, timeout)
        timestamp = _normalize_timestamp(retrieved_at)
        locator = source_url
    else:
        url_revision = None
        raw = _read_regular_file(Path(source), maximum)
        timestamp = _normalize_timestamp(retrieved_at, required=True)
        locator = f"operator-local-capture:sha256:{quality_raw_sha256(raw)}"
    digest = quality_raw_sha256(raw)
    if expected is not None and digest != expected:
        raise SweBenchAdapterError("SWE-bench source SHA-256 mismatch")
    revision = source_revision or (SWE_BENCH_WEBSITE_COMMIT if is_pinned_default else None)
    if not revision or len(revision) > 512:
        raise SweBenchAdapterError("source_revision is required for non-default SWE-bench sources")
    if url_revision is not None and revision != url_revision:
        raise SweBenchAdapterError("source_revision does not match the remote source URL")
    return LoadedSweBenchSource(
        raw=raw,
        raw_sha256=digest,
        retrieved_at=timestamp,
        source_locator=locator,
        upstream_revision=revision,
        source_identity_mode=(
            SweBenchSourceIdentityMode.OFFICIAL_SEMANTIC
            if is_pinned_default
            else SweBenchSourceIdentityMode.RAW_BOUND_OPERATOR
        ),
    )


def _required_string(value: Any, *, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SweBenchAdapterError(f"SWE-bench field {field!r} must be a bounded string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise SweBenchAdapterError(f"SWE-bench field {field!r} contains control characters")
    return value


def _optional_string(value: Any, *, field: str, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _required_string(value, field=field, maximum=maximum)


def _decimal(
    value: Any,
    *,
    field: str,
    nonnegative: bool = True,
    maximum: Decimal = _MAX_ACCOUNTING_VALUE,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f"{field} is not numeric")
    result = Decimal(value)
    if not result.is_finite() or (nonnegative and result < 0) or result.copy_abs() > maximum:
        raise ValueError(f"{field} is outside its numeric domain")
    return result


def _safe_count(value: Any, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_SAFE_INTEGER
    ):
        raise ValueError(f"{field} is not a nonnegative integer")
    return int(value)


def _row_subject(row: Mapping[str, Any], harness_version: str) -> QualitySubjectIdentity:
    folder = _required_string(row.get("folder"), field="folder", maximum=256)
    if _FOLDER_RE.fullmatch(folder) is None:
        raise SweBenchAdapterError("SWE-bench folder is not a stable opaque row identifier")
    label = _required_string(row.get("name"), field="name", maximum=2_048)
    agent = _required_string(row.get("agent"), field="agent", maximum=512)
    agent_org = _optional_string(row.get("agent_org"), field="agent_org", maximum=512)
    display = _optional_string(row.get("model_display"), field="model_display", maximum=2_048)
    model_org = _optional_string(row.get("model_org"), field="model_org", maximum=512)
    reasoning_effort = _optional_string(
        row.get("reasoning_effort"), field="reasoning_effort", maximum=128
    )
    site = _optional_string(row.get("site"), field="site", maximum=2_083)
    warning = _optional_string(row.get("warning"), field="warning", maximum=2_048)
    os_model = row.get("os_model")
    os_system = row.get("os_system")
    if not isinstance(os_model, bool) or not isinstance(os_system, bool):
        raise SweBenchAdapterError("SWE-bench open-source flags must be boolean")
    release_value = row.get("model_release_date")
    if release_value is not None:
        if (
            isinstance(release_value, bool)
            or not isinstance(release_value, int)
            or not 19_000_101 <= release_value <= 21_001_231
        ):
            raise SweBenchAdapterError("SWE-bench model release date must be YYYYMMDD or null")
        try:
            date.fromisoformat(
                f"{release_value // 10_000:04d}-"
                f"{release_value // 100 % 100:02d}-"
                f"{release_value % 100:02d}"
            )
        except ValueError:
            raise SweBenchAdapterError(
                "SWE-bench model release date must be a valid calendar date"
            ) from None
    tags = row.get("tags")
    if (
        not isinstance(tags, list)
        or len(tags) > 128
        or not all(isinstance(tag, str) and 0 < len(tag) <= 2_048 for tag in tags)
    ):
        raise SweBenchAdapterError("SWE-bench row tags are invalid")
    model_ids = tuple(
        _required_string(tag[len(_MODEL_TAG_PREFIX) :], field="model tag", maximum=512)
        for tag in tags
        if tag.startswith(_MODEL_TAG_PREFIX)
    )
    if len(model_ids) != len(set(model_ids)):
        raise SweBenchAdapterError("SWE-bench row repeats a model claim")
    model_claim_metadata: dict[str, Any] = {
        "open_source_model": os_model,
        "source_tags_sha256": content_hash(tags),
    }
    if model_org is not None:
        model_claim_metadata["model_organization"] = model_org
    if release_value is not None:
        model_claim_metadata["model_release_date_yyyymmdd"] = release_value
    if site is not None:
        model_claim_metadata["source_site_sha256"] = content_hash(site)
    model_claims = tuple(
        QualityModelClaim(
            model_id=model_id,
            display_name=display if len(model_ids) == 1 else None,
            provider=None,
            revision=None,
            reasoning_effort=reasoning_effort,
            claims=model_claim_metadata,
        )
        for model_id in model_ids
    )
    if len(model_claims) == 1:
        kind = QualitySubjectKind.SINGLE_MODEL_SYSTEM
    elif model_claims:
        kind = QualitySubjectKind.COMPOSITE_SYSTEM
    else:
        kind = QualitySubjectKind.UNDISCLOSED_SYSTEM
    attempts = tuple(
        match.group("value")
        for tag in tags
        if (match := _ATTEMPTS_TAG_RE.fullmatch(tag)) is not None
    )
    if len(attempts) > 1:
        raise SweBenchAdapterError("SWE-bench row has ambiguous attempt claims")
    return QualitySubjectIdentity(
        row_id=f"bash-only/{folder}",
        kind=kind,
        system_label=label,
        model_claims=model_claims,
        benchmark_agent=QualityComponentIdentity(
            id=agent,
            version=harness_version,
            configuration={
                **({"organization": agent_org} if agent_org is not None else {}),
                "open_source_system": os_system,
                **(
                    {"upstream_warning_sha256": content_hash(warning)}
                    if warning is not None
                    else {}
                ),
            },
        ),
        route_disclosure=QualityRouteDisclosure.UNKNOWN,
        reasoning_claims=(
            {"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}
        ),
        attempt_claims={"attempts": attempts[0]} if attempts else {},
    )


def _invalid(code: str, detail: str) -> QualityInvalidResult:
    return QualityInvalidResult(code=code, detail=detail)


def _validated_result(
    row: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> QualityResult | QualityInvalidResult:
    details = row.get("per_instance_details")
    if details is None:
        return _invalid(
            "missing_per_instance_details",
            "The selected cohort row does not expose per-instance validation records.",
        )
    if not isinstance(details, Mapping) or len(details) != SWE_BENCH_EXPECTED_INSTANCES:
        return _invalid(
            "invalid_per_instance_details",
            f"The row must contain exactly {SWE_BENCH_EXPECTED_INSTANCES} task records.",
        )
    resolved = 0
    detail_costs: list[Decimal] = []
    detail_calls: list[int] = []
    accounting_available = True
    outcomes: list[dict[str, str | bool]] = []
    for task_id, detail in details.items():
        if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
            return _invalid(
                "invalid_per_instance_details",
                "A per-instance record has an invalid task identifier.",
            )
        if (
            not isinstance(detail, Mapping)
            or not set(detail) >= _DETAIL_REQUIRED_FIELDS
            or set(detail) - (_DETAIL_REQUIRED_FIELDS | _DETAIL_OPTIONAL_FIELDS)
        ):
            return _invalid(
                "invalid_per_instance_details",
                "A per-instance record does not match the reviewed field contract.",
            )
        if not isinstance(detail.get("resolved"), bool):
            return _invalid(
                "invalid_per_instance_details",
                "A per-instance resolved value is not boolean.",
            )
        resolved += int(detail["resolved"])
        if accounting_available:
            try:
                detail_costs.append(_decimal(detail.get("cost"), field="per-instance cost"))
                detail_calls.append(
                    _safe_count(detail.get("api_calls"), field="per-instance API calls")
                )
            except ValueError:
                accounting_available = False
                detail_costs.clear()
                detail_calls.clear()
        outcomes.append({"task_id": task_id, "resolved": detail["resolved"]})
    try:
        source_percent = _decimal(
            row.get("resolved"),
            field="resolved percentage",
            maximum=Decimal(100),
        )
    except ValueError:
        return _invalid(
            "invalid_aggregate_score",
            "The row has a missing or invalid aggregate resolved percentage.",
        )
    with localcontext(POLICY_DECIMAL_CONTEXT):
        recomputed_percent = Decimal(resolved) * Decimal(100) / SWE_BENCH_EXPECTED_INSTANCES
        score_matches = (source_percent - recomputed_percent).copy_abs() <= _NUMERIC_TOLERANCE
    if not score_matches:
        return _invalid(
            "aggregate_detail_mismatch",
            "The source score disagrees with the recomputed per-instance outcomes.",
        )
    total_cost: Decimal | None = None
    cost_per_issue: Decimal | None = None
    calls_per_issue: Decimal | None = None
    total_calls: int | None = None
    accounting_coherent = False
    if accounting_available:
        total_calls = sum(detail_calls)
        if total_calls <= MAX_SAFE_INTEGER:
            try:
                source_total_cost = _decimal(row.get("cost"), field="total cost")
                source_cost_per_issue = _decimal(row.get("instance_cost"), field="instance cost")
                source_calls_per_issue = _decimal(row.get("instance_calls"), field="instance calls")
            except ValueError:
                accounting_available = False
            if accounting_available:
                with localcontext(POLICY_DECIMAL_CONTEXT):
                    computed_total_cost = sum(detail_costs, Decimal(0))
                    computed_cost_per_issue = computed_total_cost / SWE_BENCH_EXPECTED_INSTANCES
                    computed_calls_per_issue = Decimal(total_calls) / SWE_BENCH_EXPECTED_INSTANCES
                    accounting_coherent = (
                        computed_total_cost <= _MAX_ACCOUNTING_VALUE
                        and (source_total_cost - computed_total_cost).copy_abs()
                        <= _NUMERIC_TOLERANCE
                        and (source_cost_per_issue - computed_cost_per_issue).copy_abs()
                        <= _NUMERIC_TOLERANCE
                        and (source_calls_per_issue - computed_calls_per_issue).copy_abs()
                        <= _NUMERIC_TOLERANCE
                    )
                    if accounting_coherent:
                        total_cost = computed_total_cost
                        cost_per_issue = computed_cost_per_issue
                        calls_per_issue = computed_calls_per_issue
    try:
        raw_date = _required_string(row.get("date"), field="date", maximum=32)
        observed_date = date.fromisoformat(raw_date)
    except (SweBenchAdapterError, ValueError):
        return _invalid("invalid_observation_date", "The row date is not ISO 8601.")
    observed_at = datetime.combine(observed_date, datetime_time.min, tzinfo=UTC)
    if observed_at > retrieved_at:
        return _invalid(
            "future_observation_date",
            "The row observation date is later than the capture timestamp.",
        )
    checked = row.get("checked")
    if checked is not None and not isinstance(checked, bool):
        return _invalid("invalid_checked_flag", "The row checked flag is not boolean or null.")
    measurements = [
        QualityMeasurement(
            id="swe_bench_resolved_percent",
            role=QualityMeasurementRole.QUALITY,
            value=recomputed_percent,
            unit="percent",
            sample_count=SWE_BENCH_EXPECTED_INSTANCES,
        )
    ]
    counts = [
        QualityCount(
            id="attempted_issues",
            role=QualityMeasurementRole.QUALITY,
            value=SWE_BENCH_EXPECTED_INSTANCES,
        ),
        QualityCount(
            id="resolved_issues",
            role=QualityMeasurementRole.QUALITY,
            value=resolved,
        ),
    ]
    if accounting_coherent:
        assert total_cost is not None
        assert cost_per_issue is not None
        assert calls_per_issue is not None
        assert total_calls is not None
        measurements.extend(
            (
                QualityMeasurement(
                    id="swe_bench_reported_api_calls_per_issue",
                    role=QualityMeasurementRole.OTHER,
                    value=calls_per_issue,
                    unit="calls/issue",
                    sample_count=SWE_BENCH_EXPECTED_INSTANCES,
                ),
                QualityMeasurement(
                    id="swe_bench_reported_cost_per_issue_usd",
                    role=QualityMeasurementRole.COST,
                    value=cost_per_issue,
                    unit="USD/issue",
                    sample_count=SWE_BENCH_EXPECTED_INSTANCES,
                ),
                QualityMeasurement(
                    id="swe_bench_reported_total_cost_usd",
                    role=QualityMeasurementRole.COST,
                    value=total_cost,
                    unit="USD",
                    sample_count=SWE_BENCH_EXPECTED_INSTANCES,
                ),
            )
        )
        counts.append(
            QualityCount(
                id="total_api_calls",
                role=QualityMeasurementRole.OTHER,
                value=total_calls,
            )
        )
    return QualityResult(
        primary_metric="swe_bench_resolved_percent",
        measurements=tuple(measurements),
        counts=tuple(counts),
        observed_at=observed_at,
        metadata={
            "aggregate_validated_against_per_instance": True,
            "accounting_aggregate_coherent": accounting_coherent,
            "date_granularity": "day",
            "score_recomputed_from_per_instance": True,
            "task_outcome_sha256": content_hash(
                sorted(outcomes, key=lambda outcome: str(outcome["task_id"]))
            ),
            "upstream_checked": checked,
        },
    )


def _default_rights() -> QualityRights:
    return QualityRights(
        license_expression="CC-BY-NC-4.0",
        terms_locator=SWE_BENCH_WEBSITE_LICENSE_URL,
        publication_permission=QualityPublicationPermission.UNKNOWN,
        reviewed_at=_RIGHTS_REVIEWED_AT,
        review_evidence=(
            "Root LICENSE at the pinned official website revision declares CC-BY-NC-4.0. "
            "Publication remains disabled by default because applicability and attribution "
            "for a downstream deployment require operator review."
        ),
        metadata={
            "attribution_required": True,
            "noncommercial_restriction": True,
        },
    )


def _unreviewed_operator_rights() -> QualityRights:
    return QualityRights(
        license_expression="NOASSERTION",
        terms_locator=None,
        publication_permission=QualityPublicationPermission.UNKNOWN,
        reviewed_at=_RIGHTS_REVIEWED_AT,
        review_evidence=(
            "An operator-supplied capture has no adapter-reviewed provenance or rights. "
            "Publication remains disabled until an operator supplies an explicit assertion."
        ),
        metadata={"operator_review_required": True},
    )


def normalize_swe_bench_bytes(
    raw: bytes,
    *,
    retrieved_at: datetime,
    source_locator: str,
    upstream_revision: str,
    harness_version: str = SWE_BENCH_DEFAULT_HARNESS_VERSION,
    rights: QualityRights | None = None,
    source_identity_mode: SweBenchSourceIdentityMode = (
        SweBenchSourceIdentityMode.RAW_BOUND_OPERATOR
    ),
) -> SweBenchCapture:
    """Normalize one capture; unregistered inputs bind raw bytes into source identity.

    ``OFFICIAL_SEMANTIC`` is for this adapter's registered immutable source and
    its non-persisted semantic monitor. Acquisition trust and rights still live
    in raw-audit and rights identities; arbitrary capture entry points never set
    this mode automatically.
    """

    if retrieved_at.tzinfo is None:
        raise SweBenchAdapterError("retrieved_at must include a timezone")
    version = _required_string(harness_version, field="mini-SWE-agent version", maximum=128)
    locator = _required_string(source_locator, field="source locator", maximum=4_096)
    revision = _required_string(upstream_revision, field="upstream revision", maximum=512)
    if not isinstance(source_identity_mode, SweBenchSourceIdentityMode):
        raise SweBenchAdapterError("source_identity_mode must be a SweBenchSourceIdentityMode")
    document = _decode_source(raw)
    leaderboards = document["leaderboards"]
    if not isinstance(leaderboards, list) or len(leaderboards) > 128:
        raise SweBenchAdapterError("SWE-bench leaderboards must be a bounded array")
    selected: Mapping[str, Any] | None = None
    names: set[str] = set()
    for leaderboard in leaderboards:
        if not isinstance(leaderboard, Mapping) or set(leaderboard) != {"name", "results"}:
            raise SweBenchAdapterError("SWE-bench leaderboard entry schema drifted")
        name = _required_string(leaderboard["name"], field="leaderboard name", maximum=128)
        if name in names:
            raise SweBenchAdapterError("SWE-bench source repeats a leaderboard name")
        names.add(name)
        if name == "bash-only":
            selected = leaderboard
    if selected is None:
        raise SweBenchAdapterError("SWE-bench source omits the bash-only leaderboard")
    results = selected["results"]
    if not isinstance(results, list) or len(results) > MAX_LEADERBOARD_ROWS:
        raise SweBenchAdapterError("SWE-bench bash-only results must be a bounded array")
    selected_rows: list[Mapping[str, Any]] = []
    for raw_row in results:
        if not isinstance(raw_row, Mapping):
            raise SweBenchAdapterError("SWE-bench result row must be an object")
        row_version = raw_row.get("mini-swe-agent_version")
        if row_version is None:
            continue
        if not isinstance(row_version, str):
            continue
        if row_version != version:
            continue
        supplied_fields = set(raw_row)
        if not supplied_fields >= _ROW_REQUIRED_FIELDS:
            raise SweBenchAdapterError("SWE-bench result row omits a reviewed field")
        if supplied_fields - (_ROW_REQUIRED_FIELDS | _ROW_OPTIONAL_FIELDS):
            raise SweBenchAdapterError("SWE-bench result row contains an unreviewed field")
        selected_rows.append(raw_row)
    if not selected_rows:
        raise SweBenchAdapterError("selected SWE-bench cohort has no rows")

    task_sets: set[tuple[str, ...]] = set()
    for raw_row in selected_rows:
        details = raw_row.get("per_instance_details")
        if not isinstance(details, Mapping) or len(details) != SWE_BENCH_EXPECTED_INSTANCES:
            continue
        task_ids = tuple(sorted(details)) if all(isinstance(key, str) for key in details) else ()
        if task_ids and all(_TASK_ID_RE.fullmatch(task_id) is not None for task_id in task_ids):
            task_sets.add(task_ids)
    if not task_sets:
        raise SweBenchAdapterError("selected SWE-bench cohort has no complete task set")
    if len(task_sets) != 1:
        raise SweBenchAdapterError("selected SWE-bench cohort contains task-set drift")
    task_ids = next(iter(task_sets))
    task_set_sha256 = content_hash(list(task_ids))

    rows: list[QualityEvidenceRow] = []
    row_ids: set[str] = set()
    for raw_row in selected_rows:
        subject = _row_subject(raw_row, version)
        if subject.row_id in row_ids:
            raise SweBenchAdapterError("selected SWE-bench cohort repeats a folder identity")
        row_ids.add(subject.row_id)
        result_state = _validated_result(raw_row, retrieved_at=retrieved_at.astimezone(UTC))
        rows.append(
            QualityEvidenceRow(
                subject=subject,
                result=result_state if isinstance(result_state, QualityResult) else None,
                invalid_result=(
                    result_state if isinstance(result_state, QualityInvalidResult) else None
                ),
            )
        )
    raw_digest = quality_raw_sha256(raw)
    official_registered_capture = (
        raw_digest == SWE_BENCH_WEBSITE_SHA256
        and locator == SWE_BENCH_WEBSITE_URL
        and revision == SWE_BENCH_WEBSITE_COMMIT
    )
    source_identity = QualitySourceIdentity(
        source_id="swe-bench/bash-only",
        source_version=(
            f"mini-swe-agent/{version}"
            if source_identity_mode is SweBenchSourceIdentityMode.OFFICIAL_SEMANTIC
            else f"mini-swe-agent/{version}/raw-sha256:{raw_digest}"
        ),
        benchmark=QualityComponentIdentity(
            id="swe-bench",
            version="bash-only",
            configuration={"methodology": SWE_BENCH_BASH_ONLY_METHODOLOGY_URL},
        ),
        dataset=QualityComponentIdentity(
            id="princeton-nlp/SWE-bench_Verified",
            version=f"task-set-sha256:{task_set_sha256}",
            configuration={
                "expected_instances": SWE_BENCH_EXPECTED_INSTANCES,
                "historical_dataset_commit": "unreported-by-website-feed",
                "task_set_sha256": task_set_sha256,
            },
        ),
        split="bash-only",
        evaluator_harness=QualityComponentIdentity(
            id="swe-bench-official-evaluator",
            version="website-undisclosed",
            configuration={"official_website_projection": True},
        ),
        scorer=QualityComponentIdentity(
            id="resolved-instance-rate",
            version="1",
            configuration={"unit": "percent"},
        ),
        protocol=QualityComponentIdentity(
            id="mini-SWE-agent",
            version=version,
            configuration={"attempt_scope": "source-row-declared"},
        ),
        projection=QualityComponentIdentity(
            id=SWE_BENCH_ADAPTER_ID,
            version=SWE_BENCH_ADAPTER_VERSION,
            configuration={
                "aggregate_validation": "exact-per-instance-recomputation",
                "requires_complete_details": True,
            },
        ),
        scope={
            "leaderboard": "bash-only",
            "mini_swe_agent_version": version,
            "expected_instances": SWE_BENCH_EXPECTED_INSTANCES,
            "requires_per_instance_details": True,
            "source_identity_mode": source_identity_mode.value,
            "task_set_sha256": task_set_sha256,
        },
    )
    selected_rights = rights or (
        _default_rights() if official_registered_capture else _unreviewed_operator_rights()
    )
    if not isinstance(selected_rights, QualityRights):
        raise SweBenchAdapterError("rights must be a validated QualityRights assertion")
    evidence = QualityEvidenceSet(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        raw_audit=QualityRawAudit(
            source_locator=locator,
            raw_sha256=raw_digest,
            retrieved_at=retrieved_at.astimezone(UTC),
            upstream_revision=revision,
            capture_method=("https-get" if locator.startswith("https://") else "local-file"),
            parser_implementation=QualityComponentIdentity(
                id=SWE_BENCH_ADAPTER_ID,
                version=SWE_BENCH_ADAPTER_VERSION,
                configuration={"python_json_decimal": True},
            ),
            metadata={
                "selected_leaderboard": "bash-only",
                "selected_harness_version": version,
            },
        ),
        source_identity=source_identity,
        rights=QualityRights.model_validate(selected_rights.model_dump(mode="json")),
        rows=tuple(rows),
    )
    valid_rows = sum(row.result is not None for row in evidence.rows)
    return SweBenchCapture(
        evidence=evidence,
        rows_seen=len(evidence.rows),
        valid_rows=valid_rows,
        invalid_rows=len(evidence.rows) - valid_rows,
        harness_version=version,
    )


def capture_swe_bench(
    source: str | Path = SWE_BENCH_WEBSITE_URL,
    *,
    expected_sha256: str | None = None,
    source_revision: str | None = None,
    retrieved_at: datetime | None = None,
    harness_version: str = SWE_BENCH_DEFAULT_HARNESS_VERSION,
    rights: QualityRights | None = None,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> SweBenchCapture:
    loaded = load_swe_bench_source(
        source,
        expected_sha256=expected_sha256,
        source_revision=source_revision,
        retrieved_at=retrieved_at,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )
    return normalize_swe_bench_bytes(
        loaded.raw,
        retrieved_at=loaded.retrieved_at,
        source_locator=loaded.source_locator,
        upstream_revision=loaded.upstream_revision,
        harness_version=harness_version,
        rights=rights,
        source_identity_mode=loaded.source_identity_mode,
    )


def write_swe_bench_capture(
    capture: SweBenchCapture,
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Atomically write a private evidence/inventory bundle."""

    if not isinstance(capture, SweBenchCapture):
        raise SweBenchAdapterError("capture must be a SweBenchCapture")
    try:
        return publish_text_bundle(
            output_directory,
            {
                EVIDENCE_FILENAME: dump_json(capture.evidence),
                INVENTORY_FILENAME: json.dumps(capture.inventory(), indent=2, ensure_ascii=False)
                + "\n",
                MANIFEST_FILENAME: json.dumps(capture.manifest(), indent=2, ensure_ascii=False)
                + "\n",
            },
            manifest_name=MANIFEST_FILENAME,
            overwrite=overwrite,
            directory_mode=0o700,
            file_mode=0o600,
        )
    except BundlePublicationError as exc:
        raise SweBenchAdapterError(str(exc)) from exc
