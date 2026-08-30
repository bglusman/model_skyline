from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time as time_module
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml
from yaml.constructor import ConstructorError
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from yaml.nodes import MappingNode

from model_skyline.adapters._publication import (
    BundlePublicationError,
    publish_text_bundle,
)
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT
from model_skyline.io import dump_json
from model_skyline.models import (
    FORBIDDEN_TEXT_RE,
    SCHEMA_VERSION,
    EligibilityPolicy,
    FrontierAxis,
    FrontierDefinition,
    Goal,
    Observation,
    ObservationCatalog,
    ObservationRequirements,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    SignalMetric,
    SourceReference,
    UncertaintyMode,
    WorkloadProfile,
    WorkloadReference,
)

DEFAULT_SOURCE_COMMIT = "cb6a152e5ee27fbc77ac499d5e628ccd74a5fa2a"
DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/Aider-AI/aider/"
    f"{DEFAULT_SOURCE_COMMIT}/aider/website/_data/polyglot_leaderboard.yml"
)
DEFAULT_SOURCE_SHA256 = "85a50b25953512d18ba4bb0c23c0b8e626fcf9a5b52d287644b8a0b44b9535de"
DEFAULT_TERMS_URL = f"https://github.com/Aider-AI/aider/blob/{DEFAULT_SOURCE_COMMIT}/LICENSE.txt"
DEFAULT_METHODOLOGY_URL = (
    f"https://github.com/Aider-AI/aider/blob/{DEFAULT_SOURCE_COMMIT}/benchmark/README.md"
)
DEFAULT_SOURCE_ID = "aider-polyglot-leaderboard"

HARNESS_ID = "aider-polyglot-mixed-historical-leaderboard@1"
WORKLOAD_ID = "aider-polyglot"
EXPECTED_CASES = 225
DEFAULT_MAX_SOURCE_BYTES = 1_000_000
HARD_MAX_SOURCE_BYTES = 10_000_000
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_ALLOWED_HOSTS = ("raw.githubusercontent.com",)
MAX_YAML_DEPTH = 32
MAX_YAML_EVENTS = 100_000
MAX_ROWS = 10_000
MAX_SCALAR_LENGTH = 16_384

CATALOG_FILENAME = "observations.json"
CONFIG_FILENAME = "frontier.yaml"
MANIFEST_FILENAME = "import.json"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_COUNT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_COMMIT_RE = re.compile(
    r"^(?P<commit>[0-9a-fA-F]{7,40})(?P<dirty>-dirty| ?\(dirty\))?$",
    re.IGNORECASE,
)
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_WILSON_Z_95 = Decimal("1.95996398454005423552")
_PERCENT_ROUNDING_TOLERANCE = Decimal("0.05")


class AiderAdapterError(ValueError):
    """Aider leaderboard input cannot be fetched, validated, or normalized."""


@dataclass(frozen=True, slots=True)
class LoadedAiderSource:
    raw: bytes
    raw_sha256: str
    retrieved_at: datetime
    url: str | None


@dataclass(frozen=True, slots=True)
class AiderRowRejection:
    row_number: int
    row_id: str
    reason: str

    def as_json(self) -> dict[str, int | str]:
        return {
            "row_number": self.row_number,
            "row_id": self.row_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AiderImportResult:
    catalog: ObservationCatalog
    config: ProjectConfig
    source: SourceReference
    rows_seen: int
    rejections: tuple[AiderRowRejection, ...]
    include_dirty: bool
    expected_cases: int

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "model-skyline/aider-import/v1alpha1",
            "adapter": "aider-polyglot",
            "source": self.source.model_dump(mode="json"),
            "policy": {
                "expected_completed_cases": self.expected_cases,
                "expected_total_cases": self.expected_cases,
                "include_dirty": self.include_dirty,
                "requires_positive_total_cost": True,
                "requires_positive_seconds_per_case": True,
                "requires_positive_solved_cases": True,
                "requires_coherent_pass_counts": True,
            },
            "rows": {
                "seen": self.rows_seen,
                "imported": len(self.catalog.offerings),
                "rejected": len(self.rejections),
            },
            "rejections": [item.as_json() for item in self.rejections],
            "outputs": {
                "catalog": CATALOG_FILENAME,
                "config": CONFIG_FILENAME,
            },
            "warnings": [
                "Aider total_cost is the historical benchmark-reported model cost; it is "
                "not a current-price estimate and may omit non-model infrastructure charges.",
                "Cache and provider-specific billing semantics are only represented to the "
                "extent they were included in Aider's reported total_cost.",
                "Cost per solved work unit embeds solve rate in its denominator; use cost per "
                "attempted work unit when comparing cost independently of measured quality.",
                "Rows are historical benchmark runs, not verified currently routable endpoints.",
                "This is a mixed historical leaderboard cohort: Aider version, run date, "
                "edit format, editor model, and provider conditions may differ between rows.",
                "Aider seconds_per_case times the agent edit/generation loop and excludes "
                "subsequent unit-test execution; it is not end-to-end task latency.",
                "Solve-rate bounds are descriptive Wilson binomial reference intervals under "
                "an IID task-sampling assumption. They do not measure run-to-run or serving "
                "variance, and frontier membership uses point estimates.",
                "Cost-derived values retain the source value while their bounds propagate "
                "half of the least displayed total_cost unit as rounding uncertainty.",
                "The upstream command field is hashed, not copied, to avoid propagating "
                "credentials from user-supplied leaderboard files.",
            ],
        }


class _UniqueKeyBaseLoader(yaml.BaseLoader):
    """String-only YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None, "expected a mapping node", node.start_mark)
        mapping: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "Aider row keys must be strings",
                    key_node.start_mark,
                )
            if key in mapping:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _RowError(ValueError):
    pass


def _normalized_retrieved_at(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise AiderAdapterError("retrieved_at must include a timezone")
    return timestamp.astimezone(UTC)


def _validated_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not _SHA256_RE.fullmatch(value):
        raise AiderAdapterError("expected_sha256 must contain exactly 64 hexadecimal characters")
    return value.lower()


def _canonical_allowed_host(value: str) -> str:
    candidate = value.rstrip(".").lower()
    if not candidate or "://" in candidate or "/" in candidate or "@" in candidate:
        raise AiderAdapterError("allowed hosts must be bare DNS hostnames or public IP addresses")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        try:
            candidate = candidate.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise AiderAdapterError(f"invalid allowed host {value!r}") from exc
        if candidate == "localhost" or candidate.endswith(".localhost"):
            raise AiderAdapterError("localhost cannot be an allowed remote source host") from None
        return candidate
    if not address.is_global:
        raise AiderAdapterError(
            "private, loopback, link-local, and reserved IP hosts are forbidden"
        )
    return address.compressed


def _validated_allowed_hosts(values: Iterable[str]) -> frozenset[str]:
    hosts = frozenset(_canonical_allowed_host(value) for value in values)
    if not hosts:
        raise AiderAdapterError("at least one allowed host is required for a remote source")
    return hosts


def _validated_https_url(value: str, *, allowed_hosts: frozenset[str]) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise AiderAdapterError("remote Aider sources must use an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise AiderAdapterError("remote Aider source URLs cannot contain credentials")
    if parsed.query:
        raise AiderAdapterError("remote Aider source URLs cannot contain query strings")
    if parsed.fragment:
        raise AiderAdapterError("remote Aider source URLs cannot contain fragments")
    host = _canonical_allowed_host(parsed.hostname)
    if host not in allowed_hosts:
        allowed = ", ".join(sorted(allowed_hosts))
        raise AiderAdapterError(
            f"remote Aider source host {host!r} is not allowed; allowed hosts: {allowed}"
        )
    return value


def _bounded_response_body(
    response: httpx.Response,
    max_bytes: int,
    *,
    deadline: float,
) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise AiderAdapterError("remote source returned an invalid Content-Length") from exc
        if declared_length < 0 or declared_length > max_bytes:
            raise AiderAdapterError(f"Aider source exceeds the {max_bytes}-byte limit")

    body = bytearray()
    for chunk in response.iter_bytes():
        if time_module.monotonic() > deadline:
            raise AiderAdapterError("remote Aider source exceeded the total retrieval deadline")
        body.extend(chunk)
        if len(body) > max_bytes:
            raise AiderAdapterError(f"Aider source exceeds the {max_bytes}-byte limit")
    return bytes(body)


def _download_https(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    allowed_hosts: Iterable[str],
    transport: httpx.BaseTransport | None,
) -> tuple[bytes, str]:
    validated_hosts = _validated_allowed_hosts(allowed_hosts)
    current = _validated_https_url(url, allowed_hosts=validated_hosts)
    deadline = time_module.monotonic() + timeout_seconds
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "model-skyline-aider-adapter/0.2"},
        ) as client:
            if time_module.monotonic() > deadline:
                raise AiderAdapterError("remote Aider source exceeded the total retrieval deadline")
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    raise AiderAdapterError(
                        "remote Aider source redirects are not followed; use the final HTTPS URL"
                    )
                response.raise_for_status()
                return (
                    _bounded_response_body(response, max_bytes, deadline=deadline),
                    current,
                )
    except AiderAdapterError:
        raise
    except httpx.HTTPError as exc:
        raise AiderAdapterError(f"cannot fetch remote Aider source: {exc}") from exc
    raise AssertionError("redirect loop must return or raise")


def _read_local(path: Path, max_bytes: int) -> bytes:
    try:
        if not path.is_file():
            raise AiderAdapterError(f"local Aider source is not a regular file: {path}")
        with path.open("rb") as source:
            raw = source.read(max_bytes + 1)
    except AiderAdapterError:
        raise
    except OSError as exc:
        raise AiderAdapterError(f"cannot read local Aider source {path}: {exc}") from exc
    if len(raw) > max_bytes:
        raise AiderAdapterError(f"Aider source exceeds the {max_bytes}-byte limit")
    return raw


def load_aider_source(
    source: str | Path = DEFAULT_SOURCE_URL,
    *,
    expected_sha256: str | None = None,
    retrieved_at: datetime | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS,
    transport: httpx.BaseTransport | None = None,
) -> LoadedAiderSource:
    """Read a bounded local or HTTPS source and optionally verify its exact bytes."""

    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise AiderAdapterError(
            f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    if not 0 < max_bytes <= HARD_MAX_SOURCE_BYTES:
        raise AiderAdapterError(
            f"max_bytes must be greater than zero and at most {HARD_MAX_SOURCE_BYTES}"
        )
    expected = _validated_sha256(expected_sha256)
    source_url: str | None = None
    if isinstance(source, Path):
        raw = _read_local(source, max_bytes)
    else:
        if _URI_RE.match(source):
            if retrieved_at is not None:
                raise AiderAdapterError(
                    "retrieved_at cannot be supplied for a remote source; retrieval time is "
                    "recorded internally"
                )
            raw, source_url = _download_https(
                source,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
                allowed_hosts=allowed_hosts,
                transport=transport,
            )
        else:
            raw = _read_local(Path(source), max_bytes)

    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None and actual != expected:
        raise AiderAdapterError(
            f"Aider source SHA-256 mismatch: expected {expected}, received {actual}"
        )
    return LoadedAiderSource(
        raw=raw,
        raw_sha256=actual,
        retrieved_at=_normalized_retrieved_at(retrieved_at),
        url=source_url,
    )


def _validate_yaml_events(text: str) -> None:
    depth = 0
    event_count = 0
    allowed_explicit_tags = {
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:timestamp",
    }
    try:
        for event in yaml.parse(text, Loader=yaml.BaseLoader):
            event_count += 1
            if event_count > MAX_YAML_EVENTS:
                raise AiderAdapterError(
                    f"Aider YAML exceeds the {MAX_YAML_EVENTS}-event complexity limit"
                )
            if isinstance(event, AliasEvent):
                raise AiderAdapterError("Aider YAML aliases are not allowed")
            tag = getattr(event, "tag", None)
            if tag is not None and tag not in allowed_explicit_tags:
                raise AiderAdapterError(f"Aider YAML tag {tag!r} is not allowed")
            if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise AiderAdapterError(
                        f"Aider YAML exceeds the nesting limit of {MAX_YAML_DEPTH}"
                    )
            elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                depth -= 1
    except AiderAdapterError:
        raise
    except (yaml.YAMLError, RecursionError) as exc:
        raise AiderAdapterError(f"cannot parse Aider YAML: {exc}") from exc


def _parse_rows(raw: bytes) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AiderAdapterError("Aider source must be UTF-8 YAML") from exc
    _validate_yaml_events(text)
    try:
        # BaseLoader constructs strings only; the custom subclass also rejects duplicate keys.
        loader = _UniqueKeyBaseLoader(text)
        try:
            loaded = loader.get_single_data()
        finally:
            loader.dispose()
    except (yaml.YAMLError, RecursionError) as exc:
        raise AiderAdapterError(f"cannot parse Aider YAML: {exc}") from exc
    if not isinstance(loaded, list):
        raise AiderAdapterError("Aider YAML must contain a top-level sequence")
    if len(loaded) > MAX_ROWS:
        raise AiderAdapterError(f"Aider YAML exceeds the {MAX_ROWS}-row limit")

    rows: list[dict[str, str]] = []
    for index, item in enumerate(loaded, start=1):
        if not isinstance(item, Mapping):
            raise AiderAdapterError(f"Aider YAML row {index} must be a mapping")
        row: dict[str, str] = {}
        for key, value in item.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise AiderAdapterError(
                    f"Aider YAML row {index} must contain only scalar string fields"
                )
            if len(key) > 128 or len(value) > MAX_SCALAR_LENGTH:
                raise AiderAdapterError(f"Aider YAML row {index} contains an oversized scalar")
            row[key] = value
        rows.append(row)
    return rows


def _text(row: Mapping[str, str], key: str, *, max_length: int = 512) -> str:
    value = row.get(key)
    if value is None:
        raise _RowError(f"missing {key}")
    value = value.strip()
    if not value:
        raise _RowError(f"{key} must not be empty")
    if len(value) > max_length:
        raise _RowError(f"{key} exceeds {max_length} characters")
    if FORBIDDEN_TEXT_RE.search(value):
        raise _RowError(f"{key} contains forbidden control characters")
    return value


def _optional_text(row: Mapping[str, str], key: str, *, max_length: int = 512) -> str | None:
    if key not in row:
        return None
    return _text(row, key, max_length=max_length)


def _count(row: Mapping[str, str], key: str) -> int:
    value = _text(row, key, max_length=32)
    if not _COUNT_RE.fullmatch(value):
        raise _RowError(f"{key} must be a canonical non-negative integer")
    result = int(value)
    if result > (1 << 53) - 1:
        raise _RowError(f"{key} exceeds the portable safe-integer limit")
    return result


def _optional_count(row: Mapping[str, str], key: str) -> int | None:
    if key not in row:
        return None
    return _count(row, key)


def _decimal(row: Mapping[str, str], key: str) -> Decimal:
    value = _text(row, key, max_length=128).replace("_", "")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise _RowError(f"{key} must be a decimal number") from exc
    if not result.is_finite():
        raise _RowError(f"{key} must be finite")
    return result


def _optional_decimal(row: Mapping[str, str], key: str) -> Decimal | None:
    if key not in row:
        return None
    return _decimal(row, key)


def _benchmark_datetime(row: Mapping[str, str]) -> tuple[str, datetime]:
    raw_date = _text(row, "date", max_length=32)
    try:
        benchmark_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise _RowError("date must use YYYY-MM-DD format") from exc
    return raw_date, datetime.combine(benchmark_date, time.min, tzinfo=UTC)


def _commit_is_dirty(value: str) -> bool:
    components = [component.strip() for component in value.split(",")]
    if not components or any(not component for component in components):
        raise _RowError("commit_hash must contain one or more comma-separated Git hashes")
    dirty = False
    for component in components:
        match = _COMMIT_RE.fullmatch(component)
        if match is None:
            raise _RowError("commit_hash components must be 7-40-character hexadecimal Git hashes")
        dirty = dirty or match.group("dirty") is not None
    return dirty


def _coherent_rate(label: str, reported_percent: Decimal, passed: int, attempted: int) -> None:
    if reported_percent < 0 or reported_percent > 100:
        raise _RowError(f"{label} must be between 0 and 100")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        exact_percent = Decimal(passed) * Decimal(100) / Decimal(attempted)
        difference = abs(reported_percent - exact_percent)
    if difference > _PERCENT_ROUNDING_TOLERANCE:
        raise _RowError(
            f"{label}={reported_percent} is incoherent with {passed}/{attempted} passed cases"
        )


def wilson_interval_95(passed: int, attempted: int) -> tuple[Decimal, Decimal]:
    """Return a two-sided 95% Wilson score interval without binary floats."""

    if attempted <= 0 or not 0 <= passed <= attempted:
        raise ValueError("Wilson interval requires 0 <= passed <= attempted and attempted > 0")
    with localcontext(POLICY_DECIMAL_CONTEXT):
        n = Decimal(attempted)
        probability = Decimal(passed) / n
        z_squared = _WILSON_Z_95 * _WILSON_Z_95
        denominator = Decimal(1) + z_squared / n
        center = (probability + z_squared / (Decimal(2) * n)) / denominator
        variance = (probability * (Decimal(1) - probability) + z_squared / (Decimal(4) * n)) / n
        margin = _WILSON_Z_95 * variance.sqrt() / denominator
        lower = max(Decimal(0), center - margin)
        upper = min(Decimal(1), center + margin)
        if passed == 0:
            lower = Decimal(0)
        if passed == attempted:
            upper = Decimal(1)
    return lower, upper


_INTEGER_METADATA_FIELDS = (
    "error_outputs",
    "exhausted_context_windows",
    "indentation_errors",
    "lazy_comments",
    "num_malformed_responses",
    "num_with_malformed_responses",
    "prompt_tokens",
    "completion_tokens",
    "thinking_tokens",
    "syntax_errors",
    "test_timeouts",
    "user_asks",
)


def _row_metadata(
    row: Mapping[str, str],
    *,
    dirname: str,
    model: str,
    edit_format: str,
    commit_hash: str,
    commit_dirty: bool,
    benchmark_date: str,
    aider_version: str,
    attempted: int,
    total_tests: int,
    pass_num_1: int,
    pass_num_2: int,
    pass_rate_1: Decimal,
    pass_rate_2: Decimal,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "dirname": dirname,
        "model": model,
        "edit_format": edit_format,
        "commit_hash": commit_hash,
        "commit_dirty": commit_dirty,
        "benchmark_date": benchmark_date,
        "aider_version": aider_version,
        "test_cases": attempted,
        "total_tests": total_tests,
        "pass_num_1": pass_num_1,
        "pass_num_2": pass_num_2,
        "reported_pass_rate_1_percent": pass_rate_1,
        "reported_pass_rate_2_percent": pass_rate_2,
        "provider_attribution": "not supplied by the Aider leaderboard",
    }
    for key in _INTEGER_METADATA_FIELDS:
        value = _optional_count(row, key)
        if value is not None:
            metadata[key] = value
    percent_well_formed = _optional_decimal(row, "percent_cases_well_formed")
    if percent_well_formed is not None:
        if percent_well_formed < 0 or percent_well_formed > 100:
            raise _RowError("percent_cases_well_formed must be between 0 and 100")
        metadata["percent_cases_well_formed"] = percent_well_formed
    for key in ("editor_model", "editor_edit_format", "reasoning_effort"):
        optional_text = _optional_text(row, key)
        if optional_text is not None:
            metadata[key] = optional_text
    command = row.get("command")
    if command is not None:
        metadata["command_sha256"] = hashlib.sha256(command.encode("utf-8")).hexdigest()
    return metadata


def _normalize_row(
    row: Mapping[str, str],
    *,
    source: SourceReference,
    include_dirty: bool,
    expected_cases: int,
) -> OfferingObservation:
    dirname = _text(row, "dirname")
    model = _text(row, "model")
    edit_format = _text(row, "edit_format", max_length=128)
    aider_version = _text(row, "versions", max_length=128)
    benchmark_date, observed_at = _benchmark_datetime(row)

    attempted = _count(row, "test_cases")
    total_tests = _count(row, "total_tests")
    if attempted != expected_cases or total_tests != expected_cases:
        raise _RowError(
            f"requires test_cases=total_tests={expected_cases}; received {attempted}/{total_tests}"
        )

    pass_num_1 = _count(row, "pass_num_1")
    pass_num_2 = _count(row, "pass_num_2")
    if not 0 <= pass_num_1 <= pass_num_2 <= attempted:
        raise _RowError("pass counts must satisfy 0 <= pass_num_1 <= pass_num_2 <= test_cases")
    if pass_num_2 == 0:
        raise _RowError("pass_num_2 must be positive to calculate cost per solved work unit")
    pass_rate_1 = _decimal(row, "pass_rate_1")
    pass_rate_2 = _decimal(row, "pass_rate_2")
    _coherent_rate("pass_rate_1", pass_rate_1, pass_num_1, attempted)
    _coherent_rate("pass_rate_2", pass_rate_2, pass_num_2, attempted)

    total_cost = _decimal(row, "total_cost")
    if total_cost <= 0:
        raise _RowError("total_cost must be positive")
    seconds_per_case = _decimal(row, "seconds_per_case")
    if seconds_per_case <= 0:
        raise _RowError("seconds_per_case must be positive")

    raw_commit = _text(row, "commit_hash", max_length=256)
    commit_dirty = _commit_is_dirty(raw_commit)
    if commit_dirty and not include_dirty:
        raise _RowError("commit_hash marks the benchmark checkout dirty")

    with localcontext(POLICY_DECIMAL_CONTEXT):
        solve_rate = Decimal(pass_num_2) / Decimal(attempted)
        usd_per_attempted = total_cost / Decimal(attempted)
        usd_per_solved = total_cost / Decimal(pass_num_2)
        total_cost_exponent = total_cost.as_tuple().exponent
        if not isinstance(total_cost_exponent, int):  # pragma: no cover - finite above
            raise AssertionError("finite total_cost must have an integer exponent")
        cost_rounding_unit = Decimal(1).scaleb(total_cost_exponent)
        cost_rounding_half_unit = cost_rounding_unit / Decimal(2)
        total_cost_lower = max(Decimal(0), total_cost - cost_rounding_half_unit)
        total_cost_upper = total_cost + cost_rounding_half_unit
        usd_per_attempted_lower = total_cost_lower / Decimal(attempted)
        usd_per_attempted_upper = total_cost_upper / Decimal(attempted)
        usd_per_solved_lower = total_cost_lower / Decimal(pass_num_2)
        usd_per_solved_upper = total_cost_upper / Decimal(pass_num_2)
    lower, upper = wilson_interval_95(pass_num_2, attempted)
    sample_count = attempted
    observation_args = {"sample_count": sample_count, "observed_at": observed_at}
    signals = {
        "solve_rate_2": Observation(
            value=solve_rate,
            unit="ratio",
            lower=lower,
            upper=upper,
            **observation_args,
        ),
        "total_cost_usd": Observation(
            value=total_cost,
            unit="USD/benchmark_run",
            lower=total_cost_lower,
            upper=total_cost_upper,
            **observation_args,
        ),
        "usd_per_attempted_workunit": Observation(
            value=usd_per_attempted,
            unit="USD/attempted_workunit",
            lower=usd_per_attempted_lower,
            upper=usd_per_attempted_upper,
            **observation_args,
        ),
        "usd_per_solved_workunit": Observation(
            value=usd_per_solved,
            unit="USD/solved_workunit",
            lower=usd_per_solved_lower,
            upper=usd_per_solved_upper,
            **observation_args,
        ),
        "agent_edit_seconds_per_case": Observation(
            value=seconds_per_case,
            unit="seconds/attempted_workunit",
            **observation_args,
        ),
    }
    reasoning_effort = _optional_text(row, "reasoning_effort")
    return OfferingObservation(
        offering=OfferingKey(
            offering_id=f"aider-polyglot/{dirname}",
            model_id=model,
            provider="unknown",
            endpoint=None,
            reasoning_effort=reasoning_effort,
            agent_harness=HARNESS_ID,
            capabilities=("coding", "text"),
        ),
        signals=signals,
        metadata=_row_metadata(
            row,
            dirname=dirname,
            model=model,
            edit_format=edit_format,
            commit_hash=raw_commit,
            commit_dirty=commit_dirty,
            benchmark_date=benchmark_date,
            aider_version=aider_version,
            attempted=attempted,
            total_tests=total_tests,
            pass_num_1=pass_num_1,
            pass_num_2=pass_num_2,
            pass_rate_1=pass_rate_1,
            pass_rate_2=pass_rate_2,
        ),
        default_source=source,
    )


def _source_reference(
    loaded: LoadedAiderSource,
    *,
    source_id: str,
    source_version: str | None,
    source_license: str | None,
    terms_url: str | None,
    methodology_url: str | None,
) -> SourceReference:
    version = source_version or f"sha256:{loaded.raw_sha256}"
    return SourceReference(
        id=source_id,
        version=version,
        url=loaded.url,
        terms_url=terms_url,
        license=source_license,
        methodology=(
            "Aider Polyglot benchmark leaderboard report. Exact integer pass counts are "
            "normalized by ModelSkyline; see " + methodology_url
            if methodology_url is not None
            else "Operator-supplied Aider-compatible leaderboard. Exact integer pass counts "
            "are normalized by ModelSkyline; upstream methodology and licensing are not asserted."
        ),
        raw_sha256=loaded.raw_sha256,
        retrieved_at=loaded.retrieved_at,
    )


def _project_config(
    source: SourceReference,
    *,
    expected_cases: int,
    include_dirty: bool,
) -> ProjectConfig:
    workload_version = source.version or f"sha256:{source.raw_sha256}"
    common_requirements = ObservationRequirements(
        minimum_samples=expected_cases,
        require_source=True,
    )
    metrics = {
        "solve_rate_2": SignalMetric(
            kind="signal",
            signal="solve_rate_2",
            unit="ratio",
            description=(
                "Cases solved after Aider's second attempt, computed from exact pass counts; "
                "observations include descriptive Wilson 95% binomial reference bounds under "
                "an IID task-sampling assumption, not run-to-run uncertainty."
            ),
            requirements=common_requirements,
        ),
        "total_cost_usd": SignalMetric(
            kind="signal",
            signal="total_cost_usd",
            unit="USD/benchmark_run",
            description=(
                "Historical total_cost reported for the complete Aider benchmark run; bounds "
                "propagate half of its least displayed decimal unit as rounding uncertainty."
            ),
            requirements=common_requirements,
        ),
        "usd_per_attempted_workunit": SignalMetric(
            kind="signal",
            signal="usd_per_attempted_workunit",
            unit="USD/attempted_workunit",
            description=(
                "Reported total benchmark cost divided by every attempted case, with source "
                "rounding uncertainty propagated into bounds."
            ),
            requirements=common_requirements,
        ),
        "usd_per_solved_workunit": SignalMetric(
            kind="signal",
            signal="usd_per_solved_workunit",
            unit="USD/solved_workunit",
            description=(
                "Reported total benchmark cost divided by solved cases; failed cases remain "
                "in the numerator. Source rounding uncertainty is propagated into bounds."
            ),
            requirements=common_requirements,
        ),
        "agent_edit_seconds_per_case": SignalMetric(
            kind="signal",
            signal="agent_edit_seconds_per_case",
            unit="seconds/attempted_workunit",
            description=(
                "Aider's reported average seconds in the agent edit/generation loop per case; "
                "subsequent unit-test execution is excluded."
            ),
            requirements=common_requirements,
        ),
    }
    metadata_fields = (
        "edit_format",
        "reasoning_effort",
        "aider_version",
        "benchmark_date",
        "pass_num_2",
        "commit_hash",
    )

    def frontier(cost_metric: str, *, order_by: str | None = None) -> FrontierDefinition:
        return FrontierDefinition(
            workload=WORKLOAD_ID,
            axes=[
                FrontierAxis(metric=cost_metric, goal=Goal.MINIMIZE),
                FrontierAxis(metric="solve_rate_2", goal=Goal.MAXIMIZE),
            ],
            order_by=order_by or cost_metric,
            uncertainty=UncertaintyMode.POINT,
            eligibility=EligibilityPolicy(allow_unknown_age=False),
            metadata_fields=metadata_fields,
        )

    return ProjectConfig(
        schema_version=SCHEMA_VERSION,
        workloads={
            WORKLOAD_ID: WorkloadProfile(
                unit="polyglot_case",
                version=workload_version,
                harness=HARNESS_ID,
                cohort=(
                    f"Aider Polyglot mixed historical leaderboard rows with {expected_cases} "
                    "completed cases and clean benchmark checkouts"
                ),
                benchmark="Aider Polyglot benchmark",
                description=(
                    "Mixed historical Aider coding-agent benchmark runs. Each offering is one "
                    "exact recorded model/edit-format/run configuration, not a controlled "
                    "same-date provider experiment or merely a model family."
                ),
                variables={"case_count": Decimal(expected_cases)},
                assumptions={
                    "cost_basis": "historical_upstream_reported_total_cost",
                    "cost_includes_failed_attempts": True,
                    "cache_accounting": "only_as_included_by_upstream_total_cost",
                    "non_model_infrastructure_cost": "not_reported",
                    "dirty_runs_included": include_dirty,
                    "pass_rate_basis": "pass_num_2/test_cases",
                    "reported_pass_rate_is_metadata_only": True,
                    "comparison_design": "mixed_historical_observational_leaderboard",
                    "aider_version_edit_format_and_provider_conditions_may_vary": True,
                    "timing_scope": "agent_edit_generation_loop_excluding_unit_test_execution",
                    "solve_rate_interval": (
                        "descriptive_wilson_95_binomial_reference_under_iid_task_sampling; "
                        "not_run_to_run_or_serving_variance"
                    ),
                    "frontier_uncertainty": "point_estimates",
                    "cost_rounding_bounds": (
                        "plus_or_minus_half_the_least_displayed_total_cost_decimal_unit"
                    ),
                    "date_precision": "day; observed_at normalized to 00:00:00Z",
                },
                sources=[source],
            )
        },
        metrics=metrics,
        frontiers={
            "cost-per-attempted-vs-solve-rate": frontier("usd_per_attempted_workunit"),
            "total-cost-vs-solve-rate": frontier("total_cost_usd"),
            "cost-per-solved-vs-solve-rate": frontier("usd_per_solved_workunit"),
            "agent-edit-seconds-vs-solve-rate": frontier("agent_edit_seconds_per_case"),
        },
        selections={},
    )


def normalize_aider_polyglot(
    loaded: LoadedAiderSource,
    *,
    include_dirty: bool = False,
    expected_cases: int = EXPECTED_CASES,
    source_id: str = DEFAULT_SOURCE_ID,
    source_version: str | None = None,
    source_license: str | None = None,
    terms_url: str | None = None,
    methodology_url: str | None = None,
) -> AiderImportResult:
    """Normalize strictly eligible mixed historical rows into catalog and policy files."""

    if expected_cases <= 0 or expected_cases > (1 << 53) - 1:
        raise AiderAdapterError("expected_cases must be a positive portable safe integer")
    source = _source_reference(
        loaded,
        source_id=source_id,
        source_version=source_version,
        source_license=source_license,
        terms_url=terms_url,
        methodology_url=methodology_url,
    )
    rows = _parse_rows(loaded.raw)
    offerings: list[OfferingObservation] = []
    rejections: list[AiderRowRejection] = []
    offering_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        raw_id = row.get("dirname", "<missing>")
        row_id = (
            raw_id.strip()
            if len(raw_id) <= 512 and not FORBIDDEN_TEXT_RE.search(raw_id)
            else "<unsafe>"
        )
        try:
            offering = _normalize_row(
                row,
                source=source,
                include_dirty=include_dirty,
                expected_cases=expected_cases,
            )
        except _RowError as exc:
            rejections.append(AiderRowRejection(row_number, row_id, str(exc)))
            continue
        offering_id = offering.offering.offering_id
        if offering_id in offering_ids:
            raise AiderAdapterError(f"duplicate eligible offering_id {offering_id!r}")
        offering_ids.add(offering_id)
        offerings.append(offering)
    if not offerings:
        raise AiderAdapterError("no Aider rows satisfied the import policy")

    workload_version = source.version or f"sha256:{loaded.raw_sha256}"
    catalog = ObservationCatalog(
        schema_version=SCHEMA_VERSION,
        workload=WorkloadReference(
            id=WORKLOAD_ID,
            version=workload_version,
            unit="polyglot_case",
        ),
        offerings=offerings,
    )
    return AiderImportResult(
        catalog=catalog,
        config=_project_config(
            source,
            expected_cases=expected_cases,
            include_dirty=include_dirty,
        ),
        source=source,
        rows_seen=len(rows),
        rejections=tuple(rejections),
        include_dirty=include_dirty,
        expected_cases=expected_cases,
    )


def import_aider_polyglot(
    source: str | Path = DEFAULT_SOURCE_URL,
    *,
    expected_sha256: str | None = None,
    retrieved_at: datetime | None = None,
    include_dirty: bool = False,
    expected_cases: int = EXPECTED_CASES,
    source_id: str = DEFAULT_SOURCE_ID,
    source_version: str | None = None,
    source_license: str | None = None,
    terms_url: str | None = None,
    methodology_url: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS,
    transport: httpx.BaseTransport | None = None,
) -> AiderImportResult:
    """Fetch/load and normalize an Aider Polyglot leaderboard YAML source."""

    is_default_source = isinstance(source, str) and source == DEFAULT_SOURCE_URL
    effective_sha256 = (
        DEFAULT_SOURCE_SHA256 if expected_sha256 is None and is_default_source else expected_sha256
    )
    loaded = load_aider_source(
        source,
        expected_sha256=effective_sha256,
        retrieved_at=retrieved_at,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        transport=transport,
    )
    is_pinned_payload = loaded.raw_sha256 == DEFAULT_SOURCE_SHA256
    effective_version = (
        DEFAULT_SOURCE_COMMIT if source_version is None and is_pinned_payload else source_version
    )
    effective_license = (
        "Apache-2.0" if source_license is None and is_pinned_payload else source_license
    )
    effective_terms_url = (
        DEFAULT_TERMS_URL if terms_url is None and is_pinned_payload else terms_url
    )
    effective_methodology_url = (
        DEFAULT_METHODOLOGY_URL
        if methodology_url is None and is_pinned_payload
        else methodology_url
    )
    return normalize_aider_polyglot(
        loaded,
        include_dirty=include_dirty,
        expected_cases=expected_cases,
        source_id=source_id,
        source_version=effective_version,
        source_license=effective_license,
        terms_url=effective_terms_url,
        methodology_url=effective_methodology_url,
    )


def render_project_config(config: ProjectConfig) -> str:
    """Render an adapter-generated policy without lossy Decimal conversion."""

    return yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def write_aider_import(
    result: AiderImportResult,
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path, Path]:
    """Write a self-contained catalog, project config, and import manifest."""

    directory = Path(output_directory)
    rendered = {
        CATALOG_FILENAME: dump_json(result.catalog),
        CONFIG_FILENAME: render_project_config(result.config),
        MANIFEST_FILENAME: json.dumps(result.manifest(), indent=2, ensure_ascii=False) + "\n",
    }
    try:
        targets = publish_text_bundle(
            directory,
            rendered,
            manifest_name=MANIFEST_FILENAME,
            overwrite=overwrite,
        )
    except BundlePublicationError as exc:
        raise AiderAdapterError(f"cannot write Aider import to {directory}: {exc}") from exc
    return targets[0], targets[1], targets[2]
