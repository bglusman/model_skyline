"""Experimental, provenance-preserving adapter for MCPMark-shaped summaries.

This module intentionally imports only benchmark quality and aggregate telemetry.
The pinned MCPMark verified summary does not identify a routable provider
offering, cache behavior, or current cost, so this adapter does not manufacture
those claims. Operator-supplied documents are integrity checked but are not
represented as the pinned verified artifact unless their digest matches it.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    Decimal,
    InvalidOperation,
    localcontext,
)
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from model_skyline.adapters._publication import (
    BundlePublicationError,
    publish_text_bundle,
)
from model_skyline.io import dump_json
from model_skyline.models import (
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
    WorkloadProfile,
    WorkloadReference,
)

MCPMARK_VERIFIED_COMMIT = "b8a62a98cc3b596c9d2e8a7879478df37a582c46"
MCPMARK_VERIFIED_SHA256 = "1854f62b24dac18370dcfb61f87c6f2ef0dbdfce31ffa20cb29170c2a01753d3"
MCPMARK_VERIFIED_URL = (
    "https://raw.githubusercontent.com/eval-sys/mcpmark-experiments/"
    f"{MCPMARK_VERIFIED_COMMIT}/verified/summary.json"
)
MCPMARK_VERIFIED_METHODOLOGY_URL = (
    "https://raw.githubusercontent.com/eval-sys/mcpmark-experiments/"
    f"{MCPMARK_VERIFIED_COMMIT}/verified/README.md"
)

MCPMARK_SECTIONS = (
    "overall",
    "filesystem",
    "github",
    "notion",
    "playwright",
    "postgres",
)

DEFAULT_MAX_BYTES = 1_000_000
MAX_MAX_BYTES = 16_000_000
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 60.0
MCPMARK_DEFAULT_ALLOWED_HOSTS = ("raw.githubusercontent.com",)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PASS_AT_1_DECIMAL_PLACES = 4
_WILSON_Z_95 = Decimal("1.959963984540054")
_WILSON_QUANTUM = Decimal("0.000000000001")
_REASONING_EFFORT_SUFFIXES = frozenset(
    {
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "enabled",
    }
)
_TELEMETRY_FIELDS = (
    "total_agent_execution_time",
    "total_input_tokens",
    "total_output_tokens",
    "total_tokens",
    "total_turns",
    "avg_agent_execution_time",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_total_tokens",
    "avg_turns",
    "per_run_input_tokens",
    "per_run_output_tokens",
)
_TOTAL_TO_AVERAGE_FIELDS = (
    ("total_agent_execution_time", "avg_agent_execution_time"),
    ("total_input_tokens", "avg_input_tokens"),
    ("total_output_tokens", "avg_output_tokens"),
    ("total_tokens", "avg_total_tokens"),
    ("total_turns", "avg_turns"),
)
_AGGREGATE_TOTAL_FIELDS = (
    "total_agent_execution_time",
    "total_input_tokens",
    "total_output_tokens",
    "total_tokens",
    "total_turns",
)
_IDENTITY_FIELDS = (
    "actual_model_name",
    "is_open_source_model",
    "is_reasoning_model",
    "scores_only",
)
_SERVICE_SECTIONS = MCPMARK_SECTIONS[1:]
_SUMMARY_QUANTUM = Decimal("0.0001")
_SUMMARY_HALF_QUANTUM = _SUMMARY_QUANTUM / 2
_AGGREGATE_DECIMAL_TOLERANCE = Decimal("0.000000001")
_WILSON_REFERENCE_ASSUMPTIONS = (
    "Descriptive 95% Wilson score reference interval under an IID Bernoulli "
    "task-sampling model for this fixed task set; it does not estimate repeated-run, "
    "task-cluster, harness, judge, or model-version uncertainty and is not a guarantee."
)


class MCPMarkAdapterError(ValueError):
    """An MCPMark document cannot be retrieved or interpreted safely."""


def _validate_max_bytes(max_bytes: int) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise MCPMarkAdapterError("max_bytes must be an integer")
    if not 1 <= max_bytes <= MAX_MAX_BYTES:
        raise MCPMarkAdapterError(f"max_bytes must be between 1 and {MAX_MAX_BYTES}")


def _validate_timeout(timeout_seconds: float) -> None:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise MCPMarkAdapterError("timeout_seconds must be a number")
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise MCPMarkAdapterError(
            f"timeout_seconds must be greater than 0 and at most {MAX_TIMEOUT_SECONDS}"
        )


def _normalize_required_sha256(required_sha256: str | None) -> str | None:
    if required_sha256 is None:
        return None
    if not _SHA256_RE.fullmatch(required_sha256):
        raise MCPMarkAdapterError("required_sha256 must be 64 hexadecimal characters")
    return required_sha256.lower()


def _validate_source_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError as exc:
        raise MCPMarkAdapterError(f"invalid source URL: {exc}") from exc
    if parts.scheme != "https" or not hostname:
        raise MCPMarkAdapterError("source URL must be an absolute HTTPS URL")
    if parts.username is not None or parts.password is not None:
        raise MCPMarkAdapterError("source URL cannot contain user information")
    if parts.query or parts.fragment:
        raise MCPMarkAdapterError("source URL cannot contain a query string or fragment")


def _canonical_allowed_host(value: str) -> str:
    candidate = value.rstrip(".").lower()
    if not candidate or "://" in candidate or "/" in candidate or "@" in candidate:
        raise MCPMarkAdapterError("allowed hosts must be bare DNS hostnames or public IP addresses")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        try:
            candidate = candidate.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise MCPMarkAdapterError(f"invalid allowed host {value!r}") from exc
        if candidate == "localhost" or candidate.endswith(".localhost"):
            raise MCPMarkAdapterError("localhost cannot be an allowed remote source host") from None
        return candidate
    if not address.is_global:
        raise MCPMarkAdapterError(
            "private, loopback, link-local, and reserved IP hosts are forbidden"
        )
    return address.compressed


def _validate_fetch_url(url: str, *, allowed_hosts: Iterable[str]) -> None:
    _validate_source_url(url)
    validated_hosts = frozenset(_canonical_allowed_host(host) for host in allowed_hosts)
    if not validated_hosts:
        raise MCPMarkAdapterError("at least one allowed host is required for a remote source")
    hostname = urlsplit(url).hostname
    if hostname is None:  # pragma: no cover - checked by _validate_source_url
        raise AssertionError("validated HTTPS URL must have a hostname")
    host = _canonical_allowed_host(hostname)
    if host not in validated_hosts:
        allowed = ", ".join(sorted(validated_hosts))
        raise MCPMarkAdapterError(
            f"remote MCPMark source host {host!r} is not allowed; allowed hosts: {allowed}"
        )


def _verify_digest(raw: bytes, required_sha256: str | None) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    required = _normalize_required_sha256(required_sha256)
    if required is not None and digest != required:
        raise MCPMarkAdapterError(f"SHA-256 mismatch: expected {required}, received {digest}")
    return digest


def _parse_decimal(value: str) -> Decimal:
    if len(value) > 1024:
        raise MCPMarkAdapterError("JSON decimal exceeds 1024 characters")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise MCPMarkAdapterError("invalid JSON decimal") from exc


def _parse_integer(value: str) -> int:
    if len(value) > 1024:
        raise MCPMarkAdapterError("JSON integer exceeds 1024 characters")
    return int(value)


def _reject_constant(value: str) -> None:
    raise MCPMarkAdapterError(f"non-finite JSON number {value!r} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MCPMarkAdapterError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _decode_summary(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MCPMarkAdapterError("MCPMark summary is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except MCPMarkAdapterError:
        raise
    except (RecursionError, ValueError, json.JSONDecodeError) as exc:
        raise MCPMarkAdapterError(f"cannot parse MCPMark summary JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MCPMarkAdapterError("MCPMark summary root must be a JSON object")
    return value


def _required_string(container: Mapping[str, Any], field: str) -> str:
    value = container.get(field)
    if not isinstance(value, str) or not value:
        raise MCPMarkAdapterError(f"{field!r} must be a non-empty string")
    return value


def _required_bool(container: Mapping[str, Any], field: str) -> bool:
    value = container.get(field)
    if not isinstance(value, bool):
        raise MCPMarkAdapterError(f"{field!r} must be a boolean")
    return value


def _required_count(container: Mapping[str, Any], field: str) -> int:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MCPMarkAdapterError(f"{field!r} must be a positive integer")
    return value


def _required_decimal(
    container: Mapping[str, Any],
    field: str,
    *,
    minimum: Decimal = Decimal(0),
    maximum: Decimal | None = None,
) -> Decimal:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise MCPMarkAdapterError(f"{field!r} must be a JSON number")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite() or result < minimum:
        raise MCPMarkAdapterError(f"{field!r} must be finite and at least {minimum}")
    if maximum is not None and result > maximum:
        raise MCPMarkAdapterError(f"{field!r} must not exceed {maximum}")
    return result


def _optional_decimal(container: Mapping[str, Any], field: str) -> Decimal | None:
    value = container.get(field)
    if value is None:
        return None
    return _required_decimal(container, field)


def _required_nonnegative_integer(container: Mapping[str, Any], field: str) -> int:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MCPMarkAdapterError(f"{field!r} must be a non-negative integer")
    return value


def _scores_only(row: Mapping[str, Any], *, context: str) -> bool:
    value = row.get("scores_only", False)
    if not isinstance(value, bool):
        raise MCPMarkAdapterError(f"MCPMark row {context} has a non-boolean scores_only flag")
    return value


def _pass_at_1(row: Mapping[str, Any], *, context: str) -> Decimal:
    container = row.get("pass@1")
    if not isinstance(container, dict):
        raise MCPMarkAdapterError(f"MCPMark row {context} has invalid pass@1 telemetry")
    value = _required_decimal(container, "avg", maximum=Decimal(1))
    exponent = value.normalize().as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -_PASS_AT_1_DECIMAL_PLACES:
        raise MCPMarkAdapterError(
            f"MCPMark row {context} pass@1.avg exceeds four-decimal source precision"
        )
    standard_deviation = container.get("std")
    if standard_deviation is not None:
        _required_decimal(container, "std")
    return value


def _validate_rounded_average(
    *,
    context: str,
    total_field: str,
    total: Decimal,
    average_field: str,
    average: Decimal,
    sample_count: int,
) -> None:
    tolerance = _SUMMARY_HALF_QUANTUM * Decimal(sample_count)
    if abs(average * Decimal(sample_count) - total) > tolerance:
        raise MCPMarkAdapterError(
            f"MCPMark row {context} has incoherent {total_field}/{average_field} telemetry"
        )


def _validate_row(section: str, alias: str, row: Mapping[str, Any]) -> None:
    """Validate one source row without silently discarding malformed telemetry."""

    context = f"{section}.{alias}"
    scores_only = _scores_only(row, context=context)
    total_tasks = _required_count(row, "total_tasks")
    _required_string(row, "actual_model_name")
    _required_bool(row, "is_open_source_model")
    _required_bool(row, "is_reasoning_model")
    _pass_at_1(row, context=context)

    if scores_only:
        populated = [field for field in _TELEMETRY_FIELDS if row.get(field) is not None]
        if populated:
            raise MCPMarkAdapterError(
                f"MCPMark scores-only row {context} unexpectedly contains telemetry: "
                f"{', '.join(populated)}"
            )
        return

    missing = [field for field in _TELEMETRY_FIELDS if row.get(field) is None]
    if missing:
        raise MCPMarkAdapterError(
            f"MCPMark non-scores-only row {context} has incomplete telemetry: {', '.join(missing)}"
        )

    totals: dict[str, Decimal] = {
        "total_agent_execution_time": _required_decimal(row, "total_agent_execution_time"),
        "total_input_tokens": Decimal(_required_nonnegative_integer(row, "total_input_tokens")),
        "total_output_tokens": Decimal(_required_nonnegative_integer(row, "total_output_tokens")),
        "total_tokens": Decimal(_required_nonnegative_integer(row, "total_tokens")),
        "total_turns": Decimal(_required_nonnegative_integer(row, "total_turns")),
    }
    averages = {
        field: _required_decimal(row, field)
        for field in (
            "avg_agent_execution_time",
            "avg_input_tokens",
            "avg_output_tokens",
            "avg_total_tokens",
            "avg_turns",
        )
    }
    if totals["total_tokens"] != totals["total_input_tokens"] + totals["total_output_tokens"]:
        raise MCPMarkAdapterError(
            f"MCPMark row {context} total_tokens does not equal input plus output tokens"
        )
    for total_field, average_field in _TOTAL_TO_AVERAGE_FIELDS:
        _validate_rounded_average(
            context=context,
            total_field=total_field,
            total=totals[total_field],
            average_field=average_field,
            average=averages[average_field],
            sample_count=total_tasks,
        )

    per_run_input = _required_decimal(row, "per_run_input_tokens")
    per_run_output = _required_decimal(row, "per_run_output_tokens")
    if per_run_input > totals["total_input_tokens"]:
        raise MCPMarkAdapterError(
            f"MCPMark row {context} per_run_input_tokens exceeds total_input_tokens"
        )
    if per_run_output > totals["total_output_tokens"]:
        raise MCPMarkAdapterError(
            f"MCPMark row {context} per_run_output_tokens exceeds total_output_tokens"
        )


def _row_identity(row: Mapping[str, Any], *, context: str) -> tuple[str, bool, bool, bool]:
    return (
        _required_string(row, "actual_model_name"),
        _required_bool(row, "is_open_source_model"),
        _required_bool(row, "is_reasoning_model"),
        _scores_only(row, context=context),
    )


def _validate_summary_cohort(summary: Mapping[str, Any]) -> None:
    """Require the six sections to describe one internally coherent cohort."""

    sections: dict[str, Mapping[str, Any]] = {}
    for section in MCPMARK_SECTIONS:
        raw_section = summary.get(section)
        if not isinstance(raw_section, dict) or not raw_section:
            raise MCPMarkAdapterError(f"MCPMark section {section!r} must be a non-empty object")
        for alias, raw_row in raw_section.items():
            if not isinstance(alias, str) or not alias:
                raise MCPMarkAdapterError(f"MCPMark section {section!r} has an invalid alias")
            if not isinstance(raw_row, dict):
                raise MCPMarkAdapterError(f"MCPMark row {section}.{alias} must be an object")
            _validate_row(section, alias, raw_row)
        sections[section] = raw_section

    aliases = set(sections["overall"])
    for section in _SERVICE_SECTIONS:
        section_aliases = set(sections[section])
        if section_aliases != aliases:
            missing = sorted(aliases - section_aliases)
            extra = sorted(section_aliases - aliases)
            raise MCPMarkAdapterError(
                f"MCPMark section {section!r} model roster differs from overall "
                f"(missing={missing}, extra={extra})"
            )

    section_task_counts: dict[str, int] = {}
    for section, rows in sections.items():
        task_counts = {_required_count(row, "total_tasks") for row in rows.values()}
        if len(task_counts) != 1:
            raise MCPMarkAdapterError(
                f"MCPMark section {section!r} rows do not share one task count"
            )
        section_task_counts[section] = next(iter(task_counts))
    service_task_count = sum(section_task_counts[name] for name in _SERVICE_SECTIONS)
    if section_task_counts["overall"] != service_task_count:
        raise MCPMarkAdapterError(
            "MCPMark overall task count does not equal the sum of service-section task counts"
        )

    for alias in sorted(aliases):
        overall_row = sections["overall"][alias]
        expected_identity = _row_identity(overall_row, context=f"overall.{alias}")
        for section in _SERVICE_SECTIONS:
            row = sections[section][alias]
            if _row_identity(row, context=f"{section}.{alias}") != expected_identity:
                raise MCPMarkAdapterError(
                    f"MCPMark model identity for {alias!r} differs in section {section!r}"
                )

        overall_tasks = _required_count(overall_row, "total_tasks")
        component_tasks = sum(
            _required_count(sections[section][alias], "total_tasks")
            for section in _SERVICE_SECTIONS
        )
        if overall_tasks != component_tasks:
            raise MCPMarkAdapterError(
                f"MCPMark overall task count for {alias!r} does not equal service sections"
            )

        overall_score = _pass_at_1(overall_row, context=f"overall.{alias}")
        component_success_estimate = sum(
            (
                _pass_at_1(
                    sections[section][alias],
                    context=f"{section}.{alias}",
                )
                * Decimal(_required_count(sections[section][alias], "total_tasks"))
                for section in _SERVICE_SECTIONS
            ),
            start=Decimal(0),
        )
        score_tolerance = _SUMMARY_HALF_QUANTUM * Decimal(overall_tasks + component_tasks)
        if (
            abs(overall_score * Decimal(overall_tasks) - component_success_estimate)
            > score_tolerance
        ):
            raise MCPMarkAdapterError(
                f"MCPMark overall pass@1 for {alias!r} is incoherent with service sections"
            )

        if expected_identity[-1]:
            continue
        for field in _AGGREGATE_TOTAL_FIELDS:
            overall_total = _required_decimal(overall_row, field)
            component_total = sum(
                (
                    _required_decimal(sections[section][alias], field)
                    for section in _SERVICE_SECTIONS
                ),
                start=Decimal(0),
            )
            tolerance = (
                _AGGREGATE_DECIMAL_TOLERANCE
                if field == "total_agent_execution_time"
                else Decimal(0)
            )
            if abs(overall_total - component_total) > tolerance:
                raise MCPMarkAdapterError(
                    f"MCPMark overall {field} for {alias!r} is incoherent with service sections"
                )


def _is_single_run(row: Mapping[str, Any]) -> bool:
    """Infer a single run only when both token aggregates provide exact evidence."""

    total_input = _optional_decimal(row, "total_input_tokens")
    total_output = _optional_decimal(row, "total_output_tokens")
    per_run_input = _optional_decimal(row, "per_run_input_tokens")
    per_run_output = _optional_decimal(row, "per_run_output_tokens")
    return (
        total_input is not None
        and total_output is not None
        and per_run_input is not None
        and per_run_output is not None
        and total_input == per_run_input
        and total_output == per_run_output
    )


def _recover_successes(reported: Decimal, sample_count: int) -> int | None:
    """Recover a count only when four-decimal rounding has one possible integer."""

    quantum = Decimal(1).scaleb(-_PASS_AT_1_DECIMAL_PLACES)
    half_quantum = quantum / 2
    count = Decimal(sample_count)
    low = max(Decimal(0), reported - half_quantum) * count
    high = min(Decimal(1), reported + half_quantum) * count
    low_integer = int(low.to_integral_value(rounding=ROUND_CEILING))
    high_integer = int(high.to_integral_value(rounding=ROUND_FLOOR))
    if low_integer != high_integer or not 0 <= low_integer <= sample_count:
        return None
    return low_integer


def _wilson_95(successes: int, sample_count: int) -> tuple[Decimal, Decimal]:
    with localcontext() as context:
        context.prec = 50
        n = Decimal(sample_count)
        proportion = Decimal(successes) / n
        z_squared = _WILSON_Z_95 * _WILSON_Z_95
        denominator = Decimal(1) + z_squared / n
        center = (proportion + z_squared / (Decimal(2) * n)) / denominator
        radius = (
            _WILSON_Z_95
            * (proportion * (Decimal(1) - proportion) / n + z_squared / (Decimal(4) * n * n)).sqrt()
            / denominator
        )
        lower = max(Decimal(0), center - radius).quantize(
            _WILSON_QUANTUM,
            rounding=ROUND_FLOOR,
        )
        upper = min(Decimal(1), center + radius).quantize(
            _WILSON_QUANTUM,
            rounding=ROUND_CEILING,
        )
    return lower, upper


def _reasoning_effort(alias: str, is_reasoning_model: bool) -> str | None:
    if not is_reasoning_model:
        return None
    suffix = alias.rsplit("-", 1)[-1].lower()
    if suffix in _REASONING_EFFORT_SUFFIXES:
        return suffix
    return "benchmark-default"


def _methodology(summary: Mapping[str, Any], *, is_pinned_verified: bool) -> str:
    task_set = _required_string(summary, "task_set")
    task_version = _required_string(summary, "task_version")
    artifact_description = (
        "MCPMark pinned verified aggregate summary"
        if is_pinned_verified
        else (
            "Operator-supplied MCPMark-shaped aggregate summary whose contents self-report "
            "experiment_name='verified'; ModelSkyline does not assert its repository origin, "
            "authenticity, verification status, or methodology"
        )
    )
    license_description = (
        "The experiments repository had no LICENSE file at the pinned revision, so reuse "
        "rights are unknown (NOASSERTION)."
        if is_pinned_verified
        else (
            "The supplied summary carries no license metadata; ModelSkyline makes no license "
            "or reuse-rights assertion (NOASSERTION)."
        )
    )
    methodology_link = (
        f" Methodology notes: {MCPMARK_VERIFIED_METHODOLOGY_URL}" if is_pinned_verified else ""
    )
    return (
        f"{artifact_description} (task_set={task_set}, task_version={task_version}). Values "
        "are source-reported aggregates; pass@1 is retained at its reported four-decimal "
        "precision. A descriptive 95% Wilson score reference interval is added only when "
        "token aggregates prove there was one summarized run and the rounded score maps to "
        f"exactly one integer success count. {_WILSON_REFERENCE_ASSUMPTIONS} "
        f"{license_description} Provider route, region, service tier, cache telemetry, tool "
        "charges, and cost are not reported and are not inferred."
        f"{methodology_link}"
    )


def _source_reference(
    summary: Mapping[str, Any],
    *,
    digest: str,
    source_url: str | None,
    source_version: str | None,
    retrieved_at: datetime,
) -> SourceReference:
    is_pinned_verified = digest == MCPMARK_VERIFIED_SHA256
    version = (
        MCPMARK_VERIFIED_COMMIT if is_pinned_verified else (source_version or f"sha256:{digest}")
    )
    source_id_prefix = "mcpmark-experiments-verified" if is_pinned_verified else "mcpmark-summary"
    return SourceReference(
        id=f"{source_id_prefix}-{digest[:16]}",
        version=version,
        url=source_url,
        license="NOASSERTION",
        methodology=_methodology(summary, is_pinned_verified=is_pinned_verified),
        raw_sha256=digest,
        retrieved_at=retrieved_at,
    )


def _source_cohort_label(source: SourceReference) -> str:
    return "mcpmark-verified" if source.raw_sha256 == MCPMARK_VERIFIED_SHA256 else "mcpmark-summary"


def _offering_observation(
    *,
    alias: str,
    row: Mapping[str, Any],
    source: SourceReference,
    harness: str,
    section: str,
    summary: Mapping[str, Any],
) -> OfferingObservation | None:
    context = f"{section}.{alias}"
    if _scores_only(row, context=context):
        return None

    total_tasks = _required_count(row, "total_tasks")
    pass_at_1 = _pass_at_1(row, context=context)
    actual_model_name = _required_string(row, "actual_model_name")
    is_open_source = _required_bool(row, "is_open_source_model")
    is_reasoning = _required_bool(row, "is_reasoning_model")
    reasoning_effort = _reasoning_effort(alias, is_reasoning)

    successes: int | None = None
    lower: Decimal | None = None
    upper: Decimal | None = None
    if _is_single_run(row):
        successes = _recover_successes(pass_at_1, total_tasks)
        if successes is not None:
            lower, upper = _wilson_95(successes, total_tasks)

    signals = {
        "pass_at_1": Observation(
            value=pass_at_1,
            unit="ratio",
            lower=lower,
            upper=upper,
            sample_count=total_tasks,
        ),
        "avg_agent_seconds": Observation(
            value=_required_decimal(row, "avg_agent_execution_time"),
            unit="seconds/task",
            sample_count=total_tasks,
        ),
        "avg_input_tokens": Observation(
            value=_required_decimal(row, "avg_input_tokens"),
            unit="tokens/task",
            sample_count=total_tasks,
        ),
        "avg_output_tokens": Observation(
            value=_required_decimal(row, "avg_output_tokens"),
            unit="tokens/task",
            sample_count=total_tasks,
        ),
        "avg_turns": Observation(
            value=_required_decimal(row, "avg_turns"),
            unit="turns/task",
            sample_count=total_tasks,
        ),
    }
    metadata: dict[str, Any] = {
        "benchmark": "mcpmark",
        "experiment_name": _required_string(summary, "experiment_name"),
        "task_set": _required_string(summary, "task_set"),
        "task_version": _required_string(summary, "task_version"),
        "section": section,
        "mcpmark_model_alias": alias,
        "actual_model_name": actual_model_name,
        "is_open_source_model": is_open_source,
        "is_reasoning_model": is_reasoning,
        "reasoning_configuration": reasoning_effort,
        "source_generated_at": _required_string(summary, "generated_at"),
        "source_generated_at_timezone": "unspecified",
    }
    if successes is not None:
        metadata["recovered_pass_at_1_successes"] = successes
        metadata["recovery_basis"] = "unique integer after four-decimal rounding; single run"
        metadata["pass_at_1_interval"] = "wilson-score-95-reference"
        metadata["pass_at_1_interval_assumptions"] = _WILSON_REFERENCE_ASSUMPTIONS

    return OfferingObservation(
        offering=OfferingKey(
            offering_id=f"mcpmark/{alias}@{harness}",
            model_id=actual_model_name,
            provider="unknown",
            reasoning_effort=reasoning_effort,
            agent_harness=harness,
            capabilities=("tools",),
        ),
        signals=signals,
        metadata=metadata,
        default_source=source,
    )


def _catalogs_from_summary(
    summary: Mapping[str, Any],
    *,
    source: SourceReference,
) -> dict[str, ObservationCatalog]:
    experiment_name = _required_string(summary, "experiment_name")
    if experiment_name != "verified":
        raise MCPMarkAdapterError(
            "this adapter accepts only MCPMark experiment_name='verified' summaries"
        )
    task_set = _required_string(summary, "task_set")
    task_version = _required_string(summary, "task_version")
    harness = f"mcpmark:{task_set}@{task_version}"
    source_hash = source.raw_sha256
    if source_hash is None:
        raise MCPMarkAdapterError("MCPMark source provenance must include a SHA-256 digest")
    workload_version = f"{task_version}+{source_hash[:16]}"
    cohort_label = _source_cohort_label(source)

    _validate_summary_cohort(summary)

    catalogs: dict[str, ObservationCatalog] = {}
    for section in MCPMARK_SECTIONS:
        raw_section = summary.get(section)
        if not isinstance(raw_section, dict):
            raise MCPMarkAdapterError(f"MCPMark section {section!r} must be an object")
        offerings: list[OfferingObservation] = []
        for alias, raw_row in raw_section.items():
            if not isinstance(alias, str) or not alias:
                raise MCPMarkAdapterError(f"MCPMark section {section!r} has an invalid alias")
            if not isinstance(raw_row, dict):
                raise MCPMarkAdapterError(f"MCPMark row {section}.{alias} must be an object")
            offering = _offering_observation(
                alias=alias,
                row=raw_row,
                source=source,
                harness=harness,
                section=section,
                summary=summary,
            )
            if offering is not None:
                offerings.append(offering)
        catalogs[section] = ObservationCatalog(
            schema_version="model-skyline/v1alpha1",
            workload=WorkloadReference(
                id=f"{cohort_label}-{section}",
                version=workload_version,
                unit="task",
            ),
            offerings=offerings,
        )
    return catalogs


def catalogs_from_mcpmark_bytes(
    raw: bytes,
    *,
    source_url: str | None = None,
    source_version: str | None = None,
    required_sha256: str | None = None,
    retrieved_at: datetime | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, ObservationCatalog]:
    """Validate and adapt an in-memory MCPMark-shaped verified summary.

    ``required_sha256=None`` permits unpinned operator-supplied data. The actual
    digest is always retained in provenance regardless. Only an exact digest
    match to :data:`MCPMARK_VERIFIED_SHA256` receives pinned-verification labels.
    """

    _validate_max_bytes(max_bytes)
    if not isinstance(raw, bytes):
        raise MCPMarkAdapterError("raw MCPMark content must be bytes")
    if len(raw) > max_bytes:
        raise MCPMarkAdapterError(f"MCPMark summary exceeds configured {max_bytes}-byte limit")
    if source_url is not None:
        _validate_source_url(source_url)
    timestamp = retrieved_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise MCPMarkAdapterError("retrieved_at must include a timezone")
    digest = _verify_digest(raw, required_sha256)
    summary = _decode_summary(raw)
    source = _source_reference(
        summary,
        digest=digest,
        source_url=source_url,
        source_version=source_version,
        retrieved_at=timestamp,
    )
    return _catalogs_from_summary(summary, source=source)


def load_mcpmark_catalogs(
    path: str | Path,
    *,
    source_url: str | None = None,
    source_version: str | None = None,
    required_sha256: str | None = None,
    retrieved_at: datetime | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, ObservationCatalog]:
    """Load an operator-supplied MCPMark summary from a bounded local file."""

    _validate_max_bytes(max_bytes)
    source_path = Path(path)
    try:
        with source_path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise MCPMarkAdapterError(f"cannot read {source_path}: {exc}") from exc
    return catalogs_from_mcpmark_bytes(
        raw,
        source_url=source_url,
        source_version=source_version,
        required_sha256=required_sha256,
        retrieved_at=retrieved_at,
        max_bytes=max_bytes,
    )


def _stream_response(
    client: httpx.Client,
    *,
    url: str,
    max_bytes: int,
    timeout_seconds: float,
    clock: Callable[[], float],
) -> bytes:
    started = clock()
    timeout = httpx.Timeout(timeout_seconds)
    try:
        with client.stream(
            "GET",
            url,
            timeout=timeout,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        ) as response:
            if not 200 <= response.status_code < 300:
                raise MCPMarkAdapterError(
                    f"MCPMark source returned HTTP {response.status_code}; "
                    "redirects are not followed"
                )
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise MCPMarkAdapterError("invalid Content-Length from MCPMark source") from exc
                if declared_length > max_bytes:
                    raise MCPMarkAdapterError(
                        f"MCPMark summary exceeds configured {max_bytes}-byte limit"
                    )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                if clock() - started > timeout_seconds:
                    raise MCPMarkAdapterError(
                        f"MCPMark retrieval exceeded {timeout_seconds}-second limit"
                    )
                size += len(chunk)
                if size > max_bytes:
                    raise MCPMarkAdapterError(
                        f"MCPMark summary exceeds configured {max_bytes}-byte limit"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except MCPMarkAdapterError:
        raise
    except httpx.HTTPError as exc:
        raise MCPMarkAdapterError(f"cannot retrieve MCPMark summary: {exc}") from exc


def fetch_mcpmark_catalogs(
    *,
    url: str = MCPMARK_VERIFIED_URL,
    source_version: str | None = None,
    required_sha256: str | None = MCPMARK_VERIFIED_SHA256,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allowed_hosts: Iterable[str] = MCPMARK_DEFAULT_ALLOWED_HOSTS,
    client: httpx.Client | None = None,
) -> dict[str, ObservationCatalog]:
    """Fetch MCPMark only when explicitly called, with bounded bytes and time.

    The defaults are the immutable verified commit and its required digest. For a
    user-supplied URL, pass its digest or explicitly pass ``required_sha256=None``.
    Redirects are deliberately not followed.
    """

    _validate_fetch_url(url, allowed_hosts=allowed_hosts)
    _validate_max_bytes(max_bytes)
    _validate_timeout(timeout_seconds)
    _normalize_required_sha256(required_sha256)

    if client is None:
        with httpx.Client() as owned_client:
            raw = _stream_response(
                owned_client,
                url=url,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
                clock=monotonic,
            )
    else:
        raw = _stream_response(
            client,
            url=url,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            clock=monotonic,
        )
    return catalogs_from_mcpmark_bytes(
        raw,
        source_url=url,
        source_version=source_version,
        required_sha256=required_sha256,
        retrieved_at=datetime.now(UTC),
        max_bytes=max_bytes,
    )


def build_mcpmark_project_config(
    catalogs: Mapping[str, ObservationCatalog],
) -> ProjectConfig:
    """Build quality-vs-time and quality-vs-input-token policies per section."""

    metrics = {
        "pass_at_1": SignalMetric(
            kind="signal",
            signal="pass_at_1",
            unit="ratio",
            description=(
                "MCPMark source-reported pass@1 for this task section. When present, bounds "
                "are descriptive Wilson score reference intervals under the assumptions "
                "recorded on the workload and observation."
            ),
            requirements=ObservationRequirements(
                minimum_samples=1,
                require_source=True,
            ),
        ),
        "avg_agent_seconds": SignalMetric(
            kind="signal",
            signal="avg_agent_seconds",
            unit="seconds/task",
            description="MCPMark mean agent execution time per task.",
            requirements=ObservationRequirements(
                minimum_samples=1,
                require_source=True,
            ),
        ),
        "avg_input_tokens": SignalMetric(
            kind="signal",
            signal="avg_input_tokens",
            unit="tokens/task",
            description="MCPMark mean input tokens per task; not a cost claim.",
            requirements=ObservationRequirements(
                minimum_samples=1,
                require_source=True,
            ),
        ),
    }
    workloads: dict[str, WorkloadProfile] = {}
    frontiers: dict[str, FrontierDefinition] = {}
    for section in MCPMARK_SECTIONS:
        catalog = catalogs.get(section)
        if catalog is None:
            raise MCPMarkAdapterError(f"catalog mapping is missing section {section!r}")
        if not catalog.offerings:
            raise MCPMarkAdapterError(f"catalog section {section!r} has no telemetry offerings")
        harnesses = {item.offering.agent_harness for item in catalog.offerings}
        if len(harnesses) != 1 or None in harnesses:
            raise MCPMarkAdapterError(
                f"catalog section {section!r} does not have one explicit harness"
            )
        sources = {item.default_source for item in catalog.offerings}
        if len(sources) != 1 or None in sources:
            raise MCPMarkAdapterError(
                f"catalog section {section!r} does not have one explicit source"
            )
        harness = next(iter(harnesses))
        source = next(iter(sources))
        if harness is None or source is None:  # defensive type narrowing after set checks
            raise MCPMarkAdapterError(
                f"catalog section {section!r} has incomplete harness/source provenance"
            )
        source_hash = source.raw_sha256
        if source_hash is None:
            raise MCPMarkAdapterError(f"catalog section {section!r} source has no SHA-256 digest")
        cohort_label = _source_cohort_label(source)
        expected_id = f"{cohort_label}-{section}"
        if catalog.workload.id != expected_id:
            raise MCPMarkAdapterError(
                f"catalog section {section!r} has workload {catalog.workload.id!r}, "
                f"expected {expected_id!r}"
            )
        is_pinned_verified = cohort_label == "mcpmark-verified"
        workloads[catalog.workload.id] = WorkloadProfile(
            unit=catalog.workload.unit,
            version=catalog.workload.version,
            harness=harness,
            cohort=f"{cohort_label}-{source_hash[:16]}",
            benchmark="mcpmark",
            description=(
                f"MCPMark pinned verified {section} task section."
                if is_pinned_verified
                else f"Operator-supplied MCPMark-shaped {section} task section."
            ),
            assumptions={
                "verification_status": (
                    "pinned verified artifact"
                    if is_pinned_verified
                    else "self-reported; authenticity and verification not asserted"
                ),
                "license_status": "unknown",
                "provider_route": "not reported",
                "cache_telemetry": "not reported",
                "cost": "not reported",
                "source_generated_at_timezone": "unspecified",
                "pass_at_1_interval": _WILSON_REFERENCE_ASSUMPTIONS,
                "frontier_uncertainty_policy": "point estimates",
            },
            sources=[source],
        )
        metadata_fields = (
            "mcpmark_model_alias",
            "is_open_source_model",
            "is_reasoning_model",
            "reasoning_configuration",
        )
        frontiers[f"{section}-quality-time"] = FrontierDefinition(
            workload=catalog.workload.id,
            axes=[
                FrontierAxis(metric="pass_at_1", goal=Goal.MAXIMIZE),
                FrontierAxis(metric="avg_agent_seconds", goal=Goal.MINIMIZE),
            ],
            order_by="avg_agent_seconds",
            uncertainty="point",
            eligibility=EligibilityPolicy(
                required_capabilities=("tools",),
                allow_unknown_age=True,
            ),
            metadata_fields=metadata_fields,
        )
        frontiers[f"{section}-quality-input-tokens"] = FrontierDefinition(
            workload=catalog.workload.id,
            axes=[
                FrontierAxis(metric="pass_at_1", goal=Goal.MAXIMIZE),
                FrontierAxis(metric="avg_input_tokens", goal=Goal.MINIMIZE),
            ],
            order_by="avg_input_tokens",
            uncertainty="point",
            eligibility=EligibilityPolicy(
                required_capabilities=("tools",),
                allow_unknown_age=True,
            ),
            metadata_fields=metadata_fields,
        )
    return ProjectConfig(
        schema_version="model-skyline/v1alpha1",
        workloads=workloads,
        metrics=metrics,
        frontiers=frontiers,
    )


def _render_project_config(config: ProjectConfig) -> str:
    return yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def write_mcpmark_import(
    catalogs: Mapping[str, ObservationCatalog],
    config: ProjectConfig,
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Write explicit, reviewable MCPMark catalogs, policy, and warning manifest."""

    missing = [section for section in MCPMARK_SECTIONS if section not in catalogs]
    if missing:
        raise MCPMarkAdapterError(f"catalog mapping is missing sections: {', '.join(missing)}")
    directory = Path(output_directory)
    config_path = directory / "frontier.yaml"
    catalog_paths = {
        section: directory / f"observations-{section}.json" for section in MCPMARK_SECTIONS
    }
    manifest_path = directory / "import.json"

    sources = {
        offering.default_source
        for section in MCPMARK_SECTIONS
        for offering in catalogs[section].offerings
    }
    if len(sources) != 1 or None in sources:
        raise MCPMarkAdapterError(
            "MCPMark catalogs must contain exactly one explicit shared source"
        )
    source = next(iter(sources))
    if source is None:  # defensive type narrowing after the set check above
        raise MCPMarkAdapterError("MCPMark catalogs have incomplete source provenance")
    is_pinned_verified = source.raw_sha256 == MCPMARK_VERIFIED_SHA256
    provenance_warnings = (
        [
            "The mcpmark-experiments repository had no LICENSE file at the pinned revision.",
            "Do not combine this pinned verified cohort with the methodologically different "
            "2025 cohort.",
        ]
        if is_pinned_verified
        else [
            "This operator-supplied digest does not match ModelSkyline's pinned verified "
            "artifact; repository origin, authenticity, verification status, methodology, "
            "and reuse rights are not asserted."
        ]
    )
    manifest = {
        "schema_version": "model-skyline/mcpmark-import/v1alpha1",
        "adapter": "mcpmark-verified" if is_pinned_verified else "mcpmark-summary",
        "verification_status": (
            "pinned verified artifact"
            if is_pinned_verified
            else "self-reported; authenticity and verification not asserted"
        ),
        "source": source.model_dump(mode="json"),
        "license_status": "unknown",
        "warnings": [
            *provenance_warnings,
            "Provider route, region, tier, cache telemetry, tool charges, and cost "
            "are not reported.",
            "Source generated_at has no timezone; observations intentionally have no observed_at.",
            _WILSON_REFERENCE_ASSUMPTIONS,
        ],
        "sections": {
            section: {
                "workload_id": catalogs[section].workload.id,
                "offerings": len(catalogs[section].offerings),
                "output": catalog_paths[section].name,
            }
            for section in MCPMARK_SECTIONS
        },
        "outputs": {
            "config": config_path.name,
            "manifest": manifest_path.name,
        },
    }
    rendered = {
        config_path.name: _render_project_config(config),
        **{
            catalog_paths[section].name: dump_json(catalogs[section])
            for section in MCPMARK_SECTIONS
        },
        manifest_path.name: json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    }
    try:
        return publish_text_bundle(
            directory,
            rendered,
            manifest_name=manifest_path.name,
            overwrite=overwrite,
        )
    except BundlePublicationError as exc:
        raise MCPMarkAdapterError(f"cannot write MCPMark import to {directory}: {exc}") from exc
