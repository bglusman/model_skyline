"""Exact, provenance-preserving models.dev list-price projections.

The adapter deliberately does not match model names.  An operator-provided mapping
binds one historical Aider offering to one exact models.dev provider/model entry.
The initial supported scenario is cache-disabled accounting: every Aider prompt
token is billed at the selected entry's ordinary input rate and every completion
token at its ordinary output rate. Tiered prices are rejected until a tier-selection
policy can be represented without guessing.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import time as time_module
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator

from model_skyline.adapters._publication import BundlePublicationError, publish_text_bundle
from model_skyline.adapters.aider import AiderImportResult, render_project_config
from model_skyline.canonical import canonical_bytes
from model_skyline.io import dump_json
from model_skyline.models import (
    CostFormulaBasis,
    EligibilityPolicy,
    FormulaMetric,
    FrontierAxis,
    FrontierDefinition,
    Goal,
    Observation,
    ObservationCatalog,
    ObservationRequirements,
    OfferingKey,
    OfferingObservation,
    ProjectConfig,
    SourceReference,
    StrictModel,
    UncertaintyMode,
    WorkloadProfile,
    WorkloadReference,
)

MODELS_DEV_API_URL = "https://models.dev/api.json"
MODELS_DEV_SCHEMA_COMMIT = "4a3a072b45d6d79611b6d1ccddf23f22a7b4cfc2"
MODELS_DEV_REPOSITORY_URL = "https://github.com/anomalyco/models.dev"
MODELS_DEV_LICENSE_URL = (
    f"https://github.com/anomalyco/models.dev/blob/{MODELS_DEV_SCHEMA_COMMIT}/LICENSE"
)
MODELS_DEV_README_URL = (
    f"https://github.com/anomalyco/models.dev/blob/{MODELS_DEV_SCHEMA_COMMIT}/README.md"
)
MODELS_DEV_SCHEMA_URL = (
    "https://github.com/anomalyco/models.dev/blob/"
    f"{MODELS_DEV_SCHEMA_COMMIT}/packages/core/src/schema.ts"
)
MODELS_DEV_SOURCE_ID = "models-dev-api"
DEFAULT_MAX_SOURCE_BYTES = 16_000_000
HARD_MAX_SOURCE_BYTES = 64_000_000
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 90.0
MAX_JSON_DEPTH = 48
MAX_PROVIDERS = 2_000
MAX_MODELS_PER_PROVIDER = 20_000
MAX_TOTAL_MODELS = 100_000
MAX_JSON_NODES = 2_000_000
MAX_JSON_STRUCTURAL_TOKENS = 4_000_000
MAX_JSON_STRING_LENGTH = 65_536
MAX_MAPPING_BYTES = 1_000_000
MAX_MAPPINGS = 10_000
MAX_TEXT_LENGTH = 2_048

CATALOG_FILENAME = "observations.json"
CONFIG_FILENAME = "frontier.yaml"
MANIFEST_FILENAME = "projection.json"
MAPPING_FILENAME = "mapping.json"
SELECTED_PRICES_FILENAME = "selected-prices.json"

_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ALLOWED_COST_FIELDS = frozenset(
    {
        "input",
        "output",
        "reasoning",
        "cache_read",
        "cache_write",
        "input_audio",
        "output_audio",
        "tiers",
        "context_over_200k",
    }
)
_OPTIONAL_RATE_FIELDS = (
    "cache_read",
    "cache_write",
    "input_audio",
    "output_audio",
)


class ModelsDevAdapterError(ValueError):
    """A models.dev source or explicit projection mapping is unsafe or ambiguous."""


@dataclass(frozen=True, slots=True)
class LoadedModelsDevSource:
    raw: bytes
    raw_sha256: str
    retrieved_at: datetime
    url: str | None
    official: bool


class AiderModelsDevMappingEntry(StrictModel):
    """One explicit benchmark-offering to exact provider/model assertion."""

    source_offering_id: str = Field(min_length=1, max_length=512)
    expected_source_model_id: str = Field(min_length=1, max_length=512)
    provider_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=512)
    pricing_mode: Literal["default"] = "default"
    relationship: Literal["same_provider_model_route"]
    expected_reasoning_effort: str | None = Field(default=None, max_length=128)
    expected_command_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence: str = Field(min_length=1, max_length=2_048)
    reviewed_at: datetime
    allow_deprecated: bool = False

    @model_validator(mode="after")
    def review_assertions_are_coherent(self) -> AiderModelsDevMappingEntry:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must include a timezone")
        if (
            self.relationship == "same_provider_model_route"
            and self.expected_command_sha256 is None
        ):
            raise ValueError(
                "same_provider_model_route mappings require expected_command_sha256 evidence"
            )
        return self


class AiderModelsDevMapping(StrictModel):
    schema_version: Literal["model-skyline/aider-models-dev-mapping/v1alpha1"]
    scenario: Literal["cache_disabled"]
    pricing_max_age_hours: Decimal = Field(default=Decimal("48"), gt=0, le=8_760)
    mappings: tuple[AiderModelsDevMappingEntry, ...] = Field(
        min_length=1,
        max_length=MAX_MAPPINGS,
    )

    @model_validator(mode="after")
    def mappings_are_unique(self) -> AiderModelsDevMapping:
        duplicates = sorted(
            source_offering_id
            for source_offering_id, count in Counter(
                item.source_offering_id for item in self.mappings
            ).items()
            if count > 1
        )
        if duplicates:
            raise ValueError(
                f"duplicate source offering mapping {duplicates[0]!r}; one historical row "
                "cannot be projected onto multiple price routes"
            )
        route_counts = Counter(
            (
                item.provider_id,
                item.model_id,
                item.pricing_mode,
                item.expected_reasoning_effort,
            )
            for item in self.mappings
        )
        duplicate_routes = sorted(route for route, count in route_counts.items() if count > 1)
        if duplicate_routes:
            provider_id, model_id, pricing_mode, reasoning_effort = duplicate_routes[0]
            raise ValueError(
                "duplicate target route mapping "
                f"{provider_id}/{model_id} mode={pricing_mode} "
                f"reasoning_effort={reasoning_effort!r}; aggregate or choose one benchmark "
                "row for each routable candidate"
            )
        return self


@dataclass(frozen=True, slots=True)
class ModelsDevPrice:
    provider_id: str
    provider_name: str
    model_id: str
    model_name: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    optional_rates: Mapping[str, Decimal]
    experimental_modes: tuple[str, ...]
    reasoning_efforts: tuple[str, ...]
    status: str | None


@dataclass(frozen=True, slots=True)
class AiderModelsDevProjectionResult:
    catalog: ObservationCatalog
    config: ProjectConfig
    aider_source: SourceReference
    catalog_source: SourceReference
    pricing_source: SourceReference
    mapping_sha256: str
    mapping_document: str
    selected_prices_sha256: str
    selected_prices_document: str
    mapping_count: int

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "model-skyline/models-dev-projection/v1alpha1",
            "adapter": "aider-models-dev",
            "scenario": "cache_disabled",
            "pricing_max_age_hours": str(
                self.config.frontiers[
                    "price-snapshot-cost-per-attempted-vs-solve-rate"
                ].eligibility.max_source_age_hours[self.pricing_source.id]
            ),
            "mapping_sha256": self.mapping_sha256,
            "selected_prices_sha256": self.selected_prices_sha256,
            "mapping_count": self.mapping_count,
            "sources": {
                "benchmark": self.aider_source.model_dump(mode="json"),
                "pricing_catalog": self.catalog_source.model_dump(mode="json"),
                "selected_prices": self.pricing_source.model_dump(mode="json"),
            },
            "outputs": {
                "catalog": CATALOG_FILENAME,
                "config": CONFIG_FILENAME,
                "mapping": MAPPING_FILENAME,
                "selected_prices": SELECTED_PRICES_FILENAME,
            },
            "warnings": [
                "models.dev values are community catalog assertions, not provider invoices or "
                "availability guarantees.",
                "The cache_disabled scenario charges every Aider prompt token as ordinary "
                "uncached input and every completion token as output; no cache read or write "
                "is assumed.",
                "Aider quality, token counts, and latency are historical. Their temporal "
                "portability to the operator-asserted provider/model binding is an explicit "
                "assumption.",
                "The reconstructed token marginal cost excludes non-model infrastructure, "
                "tool, tax, credit, batch, "
                "priority-tier, and other unreported charges or discounts.",
                "Aider thinking_tokens is not used because the leaderboard field can represent "
                "a configured reasoning budget rather than measured billed usage.",
                "Tiered or context-dependent models.dev entries are rejected rather than priced "
                "using an implicit context-length assumption.",
                "Mappings are exact identifiers; this adapter performs no fuzzy name matching.",
                "Mapping evidence is copied into public offering metadata; it must contain no "
                "secrets, personal data, private paths, or confidential review notes.",
            ],
        }


def _normalized_retrieved_at(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ModelsDevAdapterError("retrieved_at must include a timezone")
    return timestamp.astimezone(UTC)


def _validated_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not _SHA256_RE.fullmatch(value):
        raise ModelsDevAdapterError(
            "expected_sha256 must contain exactly 64 hexadecimal characters"
        )
    return value.lower()


def _canonical_allowed_host(value: str) -> str:
    candidate = value.rstrip(".").lower()
    if not candidate or "://" in candidate or "/" in candidate or "@" in candidate:
        raise ModelsDevAdapterError(
            "allowed hosts must be bare DNS hostnames or public IP addresses"
        )
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        try:
            candidate = candidate.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ModelsDevAdapterError(f"invalid allowed host {value!r}") from exc
        if candidate == "localhost" or candidate.endswith(".localhost"):
            raise ModelsDevAdapterError(
                "localhost cannot be an allowed remote source host"
            ) from None
        return candidate
    if not address.is_global:
        raise ModelsDevAdapterError(
            "private, loopback, link-local, and reserved IP hosts are forbidden"
        )
    return address.compressed


def _validated_https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ModelsDevAdapterError(f"invalid models.dev source URL: {exc}") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ModelsDevAdapterError("remote models.dev sources must use an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ModelsDevAdapterError("remote models.dev source URLs cannot contain credentials")
    if parsed.query:
        raise ModelsDevAdapterError("remote models.dev source URLs cannot contain query strings")
    if parsed.fragment:
        raise ModelsDevAdapterError("remote models.dev source URLs cannot contain fragments")
    _canonical_allowed_host(parsed.hostname)
    if value != MODELS_DEV_API_URL:
        raise ModelsDevAdapterError(
            "remote pricing retrieval is limited to the exact official "
            "https://models.dev/api.json URL; use a local file for mirrors or custom data"
        )
    return value


def _read_bounded_regular_file(path: Path, max_bytes: int, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModelsDevAdapterError(f"cannot open {label} {path}: {exc}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ModelsDevAdapterError(f"{label} is not a regular file: {path}")
            if before.st_size > max_bytes:
                raise ModelsDevAdapterError(f"{label} exceeds the {max_bytes}-byte limit")
            raw = source.read(max_bytes + 1)
            after = os.fstat(source.fileno())
    except ModelsDevAdapterError:
        raise
    except OSError as exc:
        raise ModelsDevAdapterError(f"cannot read {label} {path}: {exc}") from exc
    metadata_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    metadata_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if metadata_before != metadata_after or len(raw) != before.st_size:
        raise ModelsDevAdapterError(f"{label} changed while it was being read")
    if len(raw) > max_bytes:
        raise ModelsDevAdapterError(f"{label} exceeds the {max_bytes}-byte limit")
    return raw


def _read_local(path: Path, max_bytes: int) -> bytes:
    return _read_bounded_regular_file(
        path,
        max_bytes,
        label="local models.dev source",
    )


def _download_https(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    transport: httpx.BaseTransport | None,
) -> bytes:
    validated_url = _validated_https_url(url)
    deadline = time_module.monotonic() + timeout_seconds
    try:
        with (
            httpx.Client(
                timeout=httpx.Timeout(timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                transport=transport,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "model-skyline-models-dev-adapter/0.6",
                },
            ) as client,
            client.stream("GET", validated_url) as response,
        ):
            if response.is_redirect:
                raise ModelsDevAdapterError(
                    "remote models.dev source redirects are not followed; use the final URL"
                )
            if response.status_code != 200:
                raise ModelsDevAdapterError(
                    f"remote models.dev source returned HTTP {response.status_code}; "
                    "only a complete 200 response is accepted"
                )
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise ModelsDevAdapterError(
                    "remote models.dev source must use Content-Type application/json"
                )
            content_encoding = response.headers.get("content-encoding", "identity").lower()
            if content_encoding != "identity":
                raise ModelsDevAdapterError(
                    "remote models.dev source must use identity content encoding"
                )
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as exc:
                    raise ModelsDevAdapterError(
                        "remote source returned an invalid Content-Length"
                    ) from exc
                if declared < 0 or declared > max_bytes:
                    raise ModelsDevAdapterError(
                        f"models.dev source exceeds the {max_bytes}-byte limit"
                    )
            body = bytearray()
            for chunk in response.iter_raw():
                if time_module.monotonic() > deadline:
                    raise ModelsDevAdapterError(
                        "remote models.dev source exceeded the total retrieval deadline"
                    )
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ModelsDevAdapterError(
                        f"models.dev source exceeds the {max_bytes}-byte limit"
                    )
            return bytes(body)
    except ModelsDevAdapterError:
        raise
    except httpx.HTTPError as exc:
        raise ModelsDevAdapterError(f"cannot fetch remote models.dev source: {exc}") from exc


def load_models_dev_source(
    source: str | Path = MODELS_DEV_API_URL,
    *,
    expected_sha256: str | None = None,
    retrieved_at: datetime | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    assert_official_source: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> LoadedModelsDevSource:
    """Read a bounded local or HTTPS models.dev catalog and preserve exact bytes.

    A custom HTTP transport is an embedding/test seam and receives unasserted operator
    provenance unless ``assert_official_source`` is also supplied explicitly.
    """

    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ModelsDevAdapterError(
            f"timeout_seconds must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}"
        )
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise ModelsDevAdapterError("max_bytes must be an integer")
    if not 0 < max_bytes <= HARD_MAX_SOURCE_BYTES:
        raise ModelsDevAdapterError(
            f"max_bytes must be greater than zero and at most {HARD_MAX_SOURCE_BYTES}"
        )
    expected = _validated_sha256(expected_sha256)
    source_url: str | None = None
    official = False
    if isinstance(source, Path):
        raw = _read_local(source, max_bytes)
    elif _URI_RE.match(source):
        if retrieved_at is not None:
            raise ModelsDevAdapterError(
                "retrieved_at cannot be supplied for a remote source; retrieval time is "
                "recorded internally"
            )
        raw = _download_https(
            source,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            transport=transport,
        )
        source_url = source
        # Injected transports are a trusted-host test/embedding seam, not evidence that
        # the bytes traversed models.dev's TLS endpoint. The CLI never supplies one.
        official = source == MODELS_DEV_API_URL and transport is None
    else:
        raw = _read_local(Path(source), max_bytes)
    if assert_official_source:
        if source_url is not None and source_url != MODELS_DEV_API_URL:
            raise ModelsDevAdapterError(
                "only the exact https://models.dev/api.json URL can be asserted as an "
                "official remote models.dev source"
            )
        if source_url is None and expected is None:
            raise ModelsDevAdapterError(
                "asserting a local file as an official models.dev snapshot requires expected_sha256"
            )
        official = True
    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None and actual != expected:
        raise ModelsDevAdapterError(f"SHA-256 mismatch: expected {expected}, received {actual}")
    if source_url is None and retrieved_at is None:
        raise ModelsDevAdapterError(
            "retrieved_at is required for a local models.dev source so cached bytes are not "
            "misrepresented as freshly retrieved"
        )
    return LoadedModelsDevSource(
        raw=raw,
        raw_sha256=actual,
        retrieved_at=_normalized_retrieved_at(retrieved_at),
        url=source_url,
        official=official,
    )


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
            structural_tokens += 1
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ModelsDevAdapterError(
                    f"{label} exceeds the maximum JSON depth of {MAX_JSON_DEPTH}"
                )
        elif character in "]}":
            structural_tokens += 1
            depth -= 1
            if depth < 0:
                raise ModelsDevAdapterError(f"{label} has unbalanced JSON delimiters")
        elif character in ",:":
            structural_tokens += 1
        if structural_tokens > MAX_JSON_STRUCTURAL_TOKENS:
            raise ModelsDevAdapterError(
                f"{label} exceeds {MAX_JSON_STRUCTURAL_TOKENS} structural tokens"
            )
    if in_string or depth != 0:
        raise ModelsDevAdapterError(f"{label} has unterminated JSON syntax")


def _parse_decimal(value: str) -> Decimal:
    if len(value) > 1_024:
        raise ModelsDevAdapterError("JSON decimal exceeds 1024 characters")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ModelsDevAdapterError("invalid JSON decimal") from exc


def _parse_integer(value: str) -> int:
    if len(value) > 1_024:
        raise ModelsDevAdapterError("JSON integer exceeds 1024 characters")
    return int(value)


def _reject_constant(value: str) -> None:
    raise ModelsDevAdapterError(f"non-finite JSON number {value!r} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelsDevAdapterError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelsDevAdapterError(f"{label} is not valid UTF-8") from exc
    _validate_json_depth(text, label=label)
    try:
        value = json.loads(
            text,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except ModelsDevAdapterError:
        raise
    except (RecursionError, ValueError, json.JSONDecodeError) as exc:
        raise ModelsDevAdapterError(f"cannot parse {label}: {exc}") from exc
    stack = [value]
    nodes = 0
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ModelsDevAdapterError(f"{label} exceeds {MAX_JSON_NODES} JSON nodes")
        if isinstance(current, str):
            if len(current) > MAX_JSON_STRING_LENGTH:
                raise ModelsDevAdapterError(
                    f"{label} contains a string longer than {MAX_JSON_STRING_LENGTH} characters"
                )
        elif isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return value


def load_aider_models_dev_mapping(
    source: bytes | str | Path,
) -> tuple[AiderModelsDevMapping, str, str]:
    """Load a bounded JSON mapping and return its exact SHA-256 provenance."""

    if isinstance(source, bytes):
        raw = source
    else:
        path = Path(source)
        raw = _read_bounded_regular_file(
            path,
            MAX_MAPPING_BYTES,
            label="projection mapping",
        )
    if len(raw) > MAX_MAPPING_BYTES:
        raise ModelsDevAdapterError(
            f"projection mapping exceeds the {MAX_MAPPING_BYTES}-byte limit"
        )
    value = _decode_json(raw, label="projection mapping JSON")
    try:
        mapping = AiderModelsDevMapping.model_validate(value)
    except ValueError as exc:
        raise ModelsDevAdapterError(f"invalid projection mapping: {exc}") from exc
    return mapping, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")


def _required_object(container: Mapping[str, Any], field: str, *, scope: str) -> Mapping[str, Any]:
    value = container.get(field)
    if not isinstance(value, dict):
        raise ModelsDevAdapterError(f"{scope}.{field} must be a JSON object")
    return value


def _required_text(container: Mapping[str, Any], field: str, *, scope: str) -> str:
    value = container.get(field)
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_LENGTH:
        raise ModelsDevAdapterError(
            f"{scope}.{field} must be a non-empty string of at most {MAX_TEXT_LENGTH} characters"
        )
    return value


def _rate(cost: Mapping[str, Any], field: str, *, scope: str) -> Decimal:
    value = cost.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ModelsDevAdapterError(f"{scope}.cost.{field} must be a JSON number")
    result = Decimal(value)
    if not result.is_finite() or result < 0 or result > Decimal("1000000000"):
        raise ModelsDevAdapterError(
            f"{scope}.cost.{field} must be a finite non-negative bounded rate"
        )
    return result


def _optional_rate(cost: Mapping[str, Any], field: str, *, scope: str) -> Decimal | None:
    if field not in cost:
        return None
    return _rate(cost, field, scope=scope)


def _canonical_decimal_text(value: Decimal) -> str:
    """Serialize a derived record with the public canonical Decimal contract."""

    normalized = Observation(value=value, unit="canonical_decimal").value
    return format(normalized, "f")


def _models_dev_root(raw: bytes) -> Mapping[str, Any]:
    value = _decode_json(raw, label="models.dev catalog JSON")
    if not isinstance(value, dict):
        raise ModelsDevAdapterError("models.dev catalog root must be a JSON object")
    if not 1 <= len(value) <= MAX_PROVIDERS:
        raise ModelsDevAdapterError(
            f"models.dev catalog must contain between 1 and {MAX_PROVIDERS} providers"
        )
    total_models = 0
    for provider_id, provider in value.items():
        if not isinstance(provider_id, str) or not isinstance(provider, dict):
            raise ModelsDevAdapterError("models.dev providers must be string/object pairs")
        if not provider_id or len(provider_id) > 256:
            raise ModelsDevAdapterError("models.dev provider identifiers must be 1-256 characters")
        if provider.get("id") != provider_id:
            raise ModelsDevAdapterError(
                f"models.dev provider key {provider_id!r} disagrees with its record id"
            )
        models = provider.get("models")
        if not isinstance(models, dict):
            raise ModelsDevAdapterError(f"models.dev provider {provider_id!r} has no models object")
        if len(models) > MAX_MODELS_PER_PROVIDER:
            raise ModelsDevAdapterError(
                f"models.dev provider {provider_id!r} exceeds {MAX_MODELS_PER_PROVIDER} models"
            )
        if any(
            not isinstance(model_id, str) or not model_id or len(model_id) > 512
            for model_id in models
        ):
            raise ModelsDevAdapterError(
                f"models.dev provider {provider_id!r} has an invalid model identifier"
            )
        for model_id, model in models.items():
            if not isinstance(model, dict):
                raise ModelsDevAdapterError(
                    f"models.dev model {provider_id}/{model_id} must be a JSON object"
                )
            if model.get("id") != model_id:
                raise ModelsDevAdapterError(
                    f"models.dev model key {provider_id}/{model_id} disagrees with its record id"
                )
        total_models += len(models)
        if total_models > MAX_TOTAL_MODELS:
            raise ModelsDevAdapterError(
                f"models.dev catalog exceeds {MAX_TOTAL_MODELS} total models"
            )
    return value


def _select_price(
    root: Mapping[str, Any],
    entry: AiderModelsDevMappingEntry,
) -> ModelsDevPrice:
    provider = root.get(entry.provider_id)
    if not isinstance(provider, dict):
        raise ModelsDevAdapterError(f"models.dev provider {entry.provider_id!r} does not exist")
    scope = f"providers[{entry.provider_id!r}]"
    provider_record_id = _required_text(provider, "id", scope=scope)
    if provider_record_id != entry.provider_id:
        raise ModelsDevAdapterError(
            f"models.dev provider key {entry.provider_id!r} disagrees with record id "
            f"{provider_record_id!r}"
        )
    provider_name = _required_text(provider, "name", scope=scope)
    models = _required_object(provider, "models", scope=scope)
    model = models.get(entry.model_id)
    if not isinstance(model, dict):
        raise ModelsDevAdapterError(
            f"models.dev model {entry.provider_id}/{entry.model_id} does not exist"
        )
    model_scope = f"{scope}.models[{entry.model_id!r}]"
    model_record_id = _required_text(model, "id", scope=model_scope)
    if model_record_id != entry.model_id:
        raise ModelsDevAdapterError(
            f"models.dev model key {entry.model_id!r} disagrees with record id {model_record_id!r}"
        )
    model_name = _required_text(model, "name", scope=model_scope)
    if not isinstance(model.get("reasoning"), bool):
        raise ModelsDevAdapterError(f"{model_scope}.reasoning must be a boolean")
    modalities = model.get("modalities")
    if not isinstance(modalities, dict):
        raise ModelsDevAdapterError(f"{model_scope}.modalities must be a JSON object")
    for direction in ("input", "output"):
        values = modalities.get(direction)
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) for value in values)
            or "text" not in values
        ):
            raise ModelsDevAdapterError(
                f"{model_scope}.modalities.{direction} must attest text support"
            )
    reasoning_efforts: set[str] = set()
    reasoning_options = model.get("reasoning_options")
    if reasoning_options is not None:
        if not isinstance(reasoning_options, list) or len(reasoning_options) > 32:
            raise ModelsDevAdapterError(f"{model_scope}.reasoning_options must be a bounded array")
        for option in reasoning_options:
            if not isinstance(option, dict):
                raise ModelsDevAdapterError(
                    f"{model_scope}.reasoning_options entries must be objects"
                )
            if option.get("type") != "effort":
                continue
            values = option.get("values")
            if (
                not isinstance(values, list)
                or len(values) > 32
                or any(
                    not isinstance(value, str) or not value or len(value) > 128 for value in values
                )
            ):
                raise ModelsDevAdapterError(f"{model_scope} has invalid reasoning effort values")
            reasoning_efforts.update(values)
    if entry.expected_reasoning_effort is not None and (
        model.get("reasoning") is not True
        or entry.expected_reasoning_effort not in reasoning_efforts
    ):
        raise ModelsDevAdapterError(
            f"models.dev model {entry.provider_id}/{entry.model_id} does not attest reasoning "
            f"effort {entry.expected_reasoning_effort!r}"
        )
    status_value = model.get("status")
    if status_value is not None and status_value not in {"alpha", "beta", "deprecated"}:
        raise ModelsDevAdapterError(f"{model_scope}.status has an unknown value")
    status = status_value if isinstance(status_value, str) else None
    if status == "deprecated" and not entry.allow_deprecated:
        raise ModelsDevAdapterError(
            f"models.dev model {entry.provider_id}/{entry.model_id} is deprecated; "
            "set allow_deprecated only for an intentional historical projection"
        )
    cost = _required_object(model, "cost", scope=model_scope)
    unknown_fields = sorted(set(cost) - _ALLOWED_COST_FIELDS)
    if unknown_fields:
        raise ModelsDevAdapterError(
            f"{model_scope}.cost has unsupported fields: {', '.join(unknown_fields)}"
        )
    if cost.get("tiers") not in (None, []):
        raise ModelsDevAdapterError(
            f"models.dev model {entry.provider_id}/{entry.model_id} has tiered pricing; "
            "an explicit tier policy is required but not yet supported"
        )
    if cost.get("context_over_200k") is not None:
        raise ModelsDevAdapterError(
            f"models.dev model {entry.provider_id}/{entry.model_id} has context-dependent "
            "pricing; an explicit tier policy is required but not yet supported"
        )
    if "reasoning" in cost:
        raise ModelsDevAdapterError(
            f"models.dev model {entry.provider_id}/{entry.model_id} has a distinct reasoning "
            "meter, but Aider does not provide a trustworthy disjoint billed reasoning count"
        )
    optional_rates = {
        field: value
        for field in _OPTIONAL_RATE_FIELDS
        if (value := _optional_rate(cost, field, scope=model_scope)) is not None
    }
    experimental = model.get("experimental")
    experimental_modes: tuple[str, ...] = ()
    if experimental is not None:
        if not isinstance(experimental, dict):
            raise ModelsDevAdapterError(f"{model_scope}.experimental must be a JSON object")
        modes = experimental.get("modes")
        if not isinstance(modes, dict) or len(modes) > 128:
            raise ModelsDevAdapterError(
                f"{model_scope}.experimental.modes must be a bounded JSON object"
            )
        if any(not isinstance(mode, str) or not mode or len(mode) > 128 for mode in modes):
            raise ModelsDevAdapterError(f"{model_scope} has an invalid experimental mode id")
        experimental_modes = tuple(sorted(modes))
    return ModelsDevPrice(
        provider_id=entry.provider_id,
        provider_name=provider_name,
        model_id=entry.model_id,
        model_name=model_name,
        input_usd_per_million=_rate(cost, "input", scope=model_scope),
        output_usd_per_million=_rate(cost, "output", scope=model_scope),
        optional_rates=optional_rates,
        experimental_modes=experimental_modes,
        reasoning_efforts=tuple(sorted(reasoning_efforts)),
        status=status,
    )


def _catalog_source_reference(loaded: LoadedModelsDevSource) -> SourceReference:
    if not loaded.official:
        return SourceReference(
            id="operator-models-dev-compatible",
            version=f"sha256:{loaded.raw_sha256}",
            url=loaded.url,
            license="NOASSERTION",
            methodology=(
                "Operator-supplied models.dev-compatible JSON. ModelSkyline validates the "
                "supported shape and exact bytes but does not assert that this payload came "
                "from models.dev, inherits its license, or matches its repository."
            ),
            raw_sha256=loaded.raw_sha256,
            retrieved_at=loaded.retrieved_at,
        )
    return SourceReference(
        id=MODELS_DEV_SOURCE_ID,
        version=f"api-sha256:{loaded.raw_sha256}",
        url=loaded.url,
        terms_url=MODELS_DEV_LICENSE_URL,
        license="MIT",
        methodology=(
            "Community-maintained provider/model catalog; prices are documented as USD per "
            f"one million tokens. See {MODELS_DEV_README_URL}. Exact API bytes, rather than a "
            "repository commit, identify this snapshot. Catalog values are assertions, not "
            "provider invoices or availability guarantees. The adapter validates the subset "
            f"described by the schema at {MODELS_DEV_SCHEMA_URL}; that schema commit is "
            "methodology provenance and does not claim to version the live API bytes."
        ),
        raw_sha256=loaded.raw_sha256,
        retrieved_at=loaded.retrieved_at,
    )


def _selected_price_source_reference(
    loaded: LoadedModelsDevSource,
    *,
    selected_prices_sha256: str,
) -> SourceReference:
    """Identify only the reviewed fields that can affect this projection.

    The upstream catalog digest remains acquisition provenance in ``projection.json``
    and offering metadata.  Keeping it out of this semantic source identity means an
    unrelated provider or unused cache meter can advance immutable history without
    pretending that the cache-disabled input/output price evidence changed.
    """

    if loaded.official:
        return SourceReference(
            id="models-dev-selected-cache-disabled-prices",
            version=f"selected-prices-sha256:{selected_prices_sha256}",
            url=loaded.url,
            terms_url=MODELS_DEV_LICENSE_URL,
            license="MIT",
            methodology=(
                "Canonical reviewed models.dev provider/model/default-mode records selected "
                "for the cache-disabled projection. Identity covers provider and model IDs, "
                "ordinary input/output USD-per-million rates, status, and supported reasoning "
                "efforts. It intentionally excludes unused catalog records and optional cache, "
                "audio, and experimental-mode rates. The complete acquisition digest is "
                "preserved separately in projection.json and offering metadata; the exact "
                "canonical selected records are copied to selected-prices.json."
            ),
            retrieved_at=loaded.retrieved_at,
        )
    return SourceReference(
        id="operator-selected-cache-disabled-prices",
        version=f"selected-prices-sha256:{selected_prices_sha256}",
        license="NOASSERTION",
        methodology=(
            "Canonical reviewed fields selected from an operator-supplied "
            "models.dev-compatible catalog for the cache-disabled projection. Identity covers "
            "provider and model IDs, ordinary input/output rates, status, and supported "
            "reasoning efforts; unused records and optional meters are excluded. The complete "
            "operator catalog digest is preserved separately in projection.json and offering "
            "metadata; the exact canonical selected records are copied to selected-prices.json."
        ),
        retrieved_at=loaded.retrieved_at,
    )


def _semantic_source_digest(source: SourceReference) -> str:
    """Hash stable source semantics without volatile acquisition time."""

    payload = source.model_dump(mode="json", exclude={"retrieved_at"})
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _metadata_count(
    offering: OfferingObservation,
    field: str,
    *,
    allow_zero: bool = False,
) -> int:
    value = offering.metadata.get(field)
    requirement = "non-negative" if allow_zero else "positive"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelsDevAdapterError(
            f"Aider offering {offering.offering.offering_id!r} requires {requirement} integer "
            f"metadata.{field} for cache-disabled projection"
        )
    if value < 0 or (value == 0 and not allow_zero):
        raise ModelsDevAdapterError(
            f"Aider offering {offering.offering.offering_id!r} requires {requirement} integer "
            f"metadata.{field} for cache-disabled projection"
        )
    return value


def _projection_offering_id(
    source_offering_id: str,
    entry: AiderModelsDevMappingEntry,
) -> str:
    route_identity = json.dumps(
        {
            "provider_id": entry.provider_id,
            "model_id": entry.model_id,
            "pricing_mode": entry.pricing_mode,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    route_digest = hashlib.sha256(route_identity).hexdigest()
    return f"{source_offering_id}@models.dev/{route_digest}"


def _projection_config(
    aider: AiderImportResult,
    *,
    pricing_source: SourceReference,
    pricing_max_age_hours: Decimal,
    workload_version: str,
) -> ProjectConfig:
    original_workload = aider.config.workloads[aider.catalog.workload.id]
    pricing_source_digest = _semantic_source_digest(pricing_source)
    workload = WorkloadProfile.model_validate(
        {
            **original_workload.model_dump(mode="python"),
            "version": workload_version,
            "description": (
                f"{original_workload.description or ''} Current list-price signals are a "
                "models.dev cache-disabled projection over historical Aider prompt/completion "
                "token counts and exact operator mappings."
            ).strip(),
            "assumptions": {
                **original_workload.assumptions,
                "projected_cost_basis": "models_dev_public_list_price_snapshot",
                "pricing_scenario": "cache_disabled",
                "prompt_tokens_billing_bucket": "ordinary_uncached_input",
                "completion_tokens_billing_bucket": (
                    "output_including_any_source-accounted_reasoning"
                ),
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "non_model_costs_included": False,
                "performance_and_token_portability": (
                    "operator_asserted_same_provider_model_route_across_time"
                ),
                "tiered_pricing": "rejected_without_explicit_policy",
                "cost_axis_epsilon_relative": "0.000000001",
                "pricing_max_age_hours": pricing_max_age_hours,
                "pricing_source_semantics_sha256": pricing_source_digest,
            },
            "sources": [aider.source, pricing_source],
        }
    )
    formula_requirements = ObservationRequirements(require_source=True)
    projected_total_expression = (
        "(signals.aider_prompt_tokens_total * "
        "signals.models_dev_input_usd_per_million_tokens + "
        "signals.aider_completion_tokens_total * "
        "signals.models_dev_output_usd_per_million_tokens) / 1000000"
    )
    metrics = {
        "solve_rate_2": aider.config.metrics["solve_rate_2"],
        "price_snapshot_reconstructed_token_cost_usd": FormulaMetric(
            kind="formula",
            expression=projected_total_expression,
            unit="USD/benchmark_run",
            cost_basis=CostFormulaBasis.RECONSTRUCTED_COMPONENTS,
            description=(
                "Cache-disabled reconstructed token marginal cost: Aider prompt/output token "
                "totals multiplied by the exact mapped models.dev input/output rates."
            ),
            requirements=formula_requirements,
        ),
        "price_snapshot_usd_per_attempted_workunit": FormulaMetric(
            kind="formula",
            expression=(f"({projected_total_expression}) / signals.aider_attempted_workunits"),
            unit="USD/attempted_workunit",
            cost_basis=CostFormulaBasis.RECONSTRUCTED_COMPONENTS,
            description=(
                "Projected price-snapshot cache-disabled token marginal cost divided by all "
                "attempted Aider cases."
            ),
            requirements=formula_requirements,
        ),
        "price_snapshot_usd_per_solved_workunit": FormulaMetric(
            kind="formula",
            expression=f"({projected_total_expression}) / signals.aider_solved_workunits",
            unit="USD/solved_workunit",
            cost_basis=CostFormulaBasis.RECONSTRUCTED_COMPONENTS,
            description=(
                "Projected price-snapshot cache-disabled token marginal cost divided by "
                "solved Aider cases; failed cases remain in the numerator."
            ),
            requirements=formula_requirements,
        ),
    }
    metadata_fields = (
        "models_dev_route",
        "models_dev_model_name",
        "reasoning_effort",
        "edit_format",
        "benchmark_date",
        "aider_version",
    )

    def frontier(cost_metric: str) -> FrontierDefinition:
        return FrontierDefinition(
            workload=aider.catalog.workload.id,
            axes=[
                FrontierAxis(
                    metric=cost_metric,
                    goal=Goal.MINIMIZE,
                    epsilon_relative=Decimal("0.000000001"),
                ),
                FrontierAxis(metric="solve_rate_2", goal=Goal.MAXIMIZE),
            ],
            order_by=cost_metric,
            uncertainty=UncertaintyMode.POINT,
            eligibility=EligibilityPolicy(
                allow_unknown_age=False,
                max_source_age_hours={pricing_source.id: pricing_max_age_hours},
            ),
            metadata_fields=metadata_fields,
        )

    frontiers = {
        "price-snapshot-cost-per-attempted-vs-solve-rate": frontier(
            "price_snapshot_usd_per_attempted_workunit"
        ),
        "price-snapshot-reconstructed-token-cost-vs-solve-rate": frontier(
            "price_snapshot_reconstructed_token_cost_usd"
        ),
        "price-snapshot-cost-per-solved-vs-solve-rate": frontier(
            "price_snapshot_usd_per_solved_workunit"
        ),
    }
    return ProjectConfig(
        schema_version=aider.config.schema_version,
        workloads={aider.catalog.workload.id: workload},
        metrics=metrics,
        frontiers=frontiers,
        selections={},
    )


def project_aider_with_models_dev(
    aider: AiderImportResult,
    loaded_pricing: LoadedModelsDevSource,
    mapping_source: bytes | str | Path,
) -> AiderModelsDevProjectionResult:
    """Project exact mapped Aider runs using one immutable models.dev price snapshot."""

    mapping, mapping_sha256, mapping_document = load_aider_models_dev_mapping(mapping_source)
    configured_workload = aider.config.workloads.get(aider.catalog.workload.id)
    if (
        configured_workload is None
        or configured_workload.version != aider.catalog.workload.version
        or configured_workload.unit != aider.catalog.workload.unit
    ):
        raise ModelsDevAdapterError("Aider catalog does not match its project configuration")
    if any(offering.default_source != aider.source for offering in aider.catalog.offerings):
        raise ModelsDevAdapterError("Aider result contains an unexpected offering source")
    root = _models_dev_root(loaded_pricing.raw)
    catalog_source = _catalog_source_reference(loaded_pricing)
    source_by_id = {offering.offering.offering_id: offering for offering in aider.catalog.offerings}
    projected: list[OfferingObservation] = []
    selected_price_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    output_ids: set[str] = set()
    for entry in mapping.mappings:
        source_offering = source_by_id.get(entry.source_offering_id)
        if source_offering is None:
            raise ModelsDevAdapterError(
                f"mapped Aider offering {entry.source_offering_id!r} does not exist"
            )
        if source_offering.offering.model_id != entry.expected_source_model_id:
            raise ModelsDevAdapterError(
                f"mapped Aider offering {entry.source_offering_id!r} model changed: expected "
                f"{entry.expected_source_model_id!r}, received "
                f"{source_offering.offering.model_id!r}"
            )
        if source_offering.offering.reasoning_effort != entry.expected_reasoning_effort:
            raise ModelsDevAdapterError(
                f"mapped Aider offering {entry.source_offering_id!r} reasoning effort changed: "
                f"expected {entry.expected_reasoning_effort!r}, received "
                f"{source_offering.offering.reasoning_effort!r}"
            )
        if "editor_model" in source_offering.metadata:
            raise ModelsDevAdapterError(
                f"mapped Aider offering {entry.source_offering_id!r} is a compound multi-model "
                "run and cannot be priced as one route"
            )
        if entry.expected_command_sha256 is not None:
            actual_command_sha256 = source_offering.metadata.get("command_sha256")
            if actual_command_sha256 != entry.expected_command_sha256:
                raise ModelsDevAdapterError(
                    f"mapped Aider offering {entry.source_offering_id!r} command digest changed"
                )
        prompt_tokens = _metadata_count(source_offering, "prompt_tokens", allow_zero=True)
        completion_tokens = _metadata_count(source_offering, "completion_tokens", allow_zero=True)
        attempted = _metadata_count(source_offering, "test_cases")
        solved = _metadata_count(source_offering, "pass_num_2", allow_zero=True)
        price = _select_price(root, entry)
        selected_price_records[(price.provider_id, price.model_id, entry.pricing_mode)] = {
            "provider_id": price.provider_id,
            "model_id": price.model_id,
            "pricing_mode": entry.pricing_mode,
            "input_usd_per_million": _canonical_decimal_text(price.input_usd_per_million),
            "output_usd_per_million": _canonical_decimal_text(price.output_usd_per_million),
            "status": price.status,
            "reasoning_efforts": list(price.reasoning_efforts),
        }
        output_id = _projection_offering_id(entry.source_offering_id, entry)
        if output_id in output_ids:
            raise ModelsDevAdapterError(f"projection produces duplicate offering_id {output_id!r}")
        output_ids.add(output_id)
        solve_rate = source_offering.signals.get("solve_rate_2")
        if solve_rate is None:
            raise ModelsDevAdapterError(
                f"mapped Aider offering {entry.source_offering_id!r} has no solve_rate_2 signal"
            )
        benchmark_observed_at = solve_rate.observed_at
        sample_count = solve_rate.sample_count
        signals = {
            "solve_rate_2": solve_rate,
            "aider_prompt_tokens_total": Observation(
                value=Decimal(prompt_tokens),
                unit="tokens/benchmark_run",
                sample_count=sample_count,
                observed_at=benchmark_observed_at,
            ),
            "aider_completion_tokens_total": Observation(
                value=Decimal(completion_tokens),
                unit="tokens/benchmark_run",
                sample_count=sample_count,
                observed_at=benchmark_observed_at,
            ),
            "aider_attempted_workunits": Observation(
                value=Decimal(attempted),
                unit="attempted_workunits/benchmark_run",
                sample_count=sample_count,
                observed_at=benchmark_observed_at,
            ),
            "aider_solved_workunits": Observation(
                value=Decimal(solved),
                unit="solved_workunits/benchmark_run",
                sample_count=sample_count,
                observed_at=benchmark_observed_at,
            ),
            "models_dev_input_usd_per_million_tokens": Observation(
                value=price.input_usd_per_million,
                unit="USD/million_input_tokens",
                observed_at=loaded_pricing.retrieved_at,
                source=catalog_source,
            ),
            "models_dev_output_usd_per_million_tokens": Observation(
                value=price.output_usd_per_million,
                unit="USD/million_output_tokens",
                observed_at=loaded_pricing.retrieved_at,
                source=catalog_source,
            ),
        }
        metadata = {
            **source_offering.metadata,
            "source_offering_id": entry.source_offering_id,
            "source_model_id": source_offering.offering.model_id,
            "models_dev_provider_id": price.provider_id,
            "models_dev_provider_name": price.provider_name,
            "models_dev_model_id": price.model_id,
            "models_dev_model_name": price.model_name,
            "models_dev_route": f"{price.provider_id}/{price.model_id}",
            "models_dev_status": price.status or "unspecified",
            "models_dev_optional_rates_usd_per_million": dict(price.optional_rates),
            "models_dev_experimental_modes_excluded": list(price.experimental_modes),
            "models_dev_reasoning_efforts": list(price.reasoning_efforts),
            "models_dev_catalog_source_id": catalog_source.id,
            "models_dev_catalog_version": catalog_source.version,
            "models_dev_catalog_raw_sha256": loaded_pricing.raw_sha256,
            "models_dev_catalog_retrieved_at": loaded_pricing.retrieved_at.isoformat(),
            "pricing_scenario": "cache_disabled",
            "performance_and_token_portability": (
                "operator_asserted_same_provider_model_route_across_time"
            ),
            "mapping_sha256": mapping_sha256,
            "mapping_relationship": entry.relationship,
            "mapping_evidence": entry.evidence,
            "mapping_reviewed_at": entry.reviewed_at.astimezone(UTC).isoformat(),
        }
        projected.append(
            OfferingObservation(
                offering=OfferingKey(
                    offering_id=output_id,
                    model_id=entry.model_id,
                    provider=entry.provider_id,
                    endpoint=None,
                    billing_mode=None,
                    reasoning_effort=source_offering.offering.reasoning_effort,
                    agent_harness=source_offering.offering.agent_harness,
                    capabilities=source_offering.offering.capabilities,
                ),
                signals=signals,
                metadata=metadata,
                default_source=aider.source,
            )
        )
    if not projected:  # pragma: no cover - mapping model requires at least one
        raise ModelsDevAdapterError("projection mapping selected no offerings")
    selected_prices_bytes = canonical_bytes(
        [selected_price_records[key] for key in sorted(selected_price_records)]
    )
    selected_prices_sha256 = hashlib.sha256(selected_prices_bytes).hexdigest()
    selected_prices_document = selected_prices_bytes.decode("utf-8")
    pricing_source = _selected_price_source_reference(
        loaded_pricing,
        selected_prices_sha256=selected_prices_sha256,
    )
    pricing_source_digest = _semantic_source_digest(pricing_source)
    repriced: list[OfferingObservation] = []
    for offering in projected:
        signals = dict(offering.signals)
        for signal_id in (
            "models_dev_input_usd_per_million_tokens",
            "models_dev_output_usd_per_million_tokens",
        ):
            signals[signal_id] = signals[signal_id].model_copy(update={"source": pricing_source})
        repriced.append(offering.model_copy(update={"signals": signals}))
    projected = repriced
    workload_digest = hashlib.sha256(
        "\n".join(
            (
                aider.catalog.workload.version,
                selected_prices_sha256,
                pricing_source_digest,
                mapping_sha256,
                mapping.scenario,
            )
        ).encode("utf-8")
    ).hexdigest()
    workload_version = f"sha256:{workload_digest}"
    catalog = ObservationCatalog(
        schema_version=aider.catalog.schema_version,
        workload=WorkloadReference(
            id=aider.catalog.workload.id,
            version=workload_version,
            unit=aider.catalog.workload.unit,
        ),
        offerings=projected,
    )
    return AiderModelsDevProjectionResult(
        catalog=catalog,
        config=_projection_config(
            aider,
            pricing_source=pricing_source,
            pricing_max_age_hours=mapping.pricing_max_age_hours,
            workload_version=workload_version,
        ),
        aider_source=aider.source,
        catalog_source=catalog_source,
        pricing_source=pricing_source,
        mapping_sha256=mapping_sha256,
        mapping_document=mapping_document,
        selected_prices_sha256=selected_prices_sha256,
        selected_prices_document=selected_prices_document,
        mapping_count=len(mapping.mappings),
    )


def write_models_dev_projection(
    result: AiderModelsDevProjectionResult,
    output_directory: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, ...]:
    """Atomically publish catalog, policy, reviewed mapping, and manifest."""

    directory = Path(output_directory)
    rendered = {
        CATALOG_FILENAME: dump_json(result.catalog),
        CONFIG_FILENAME: render_project_config(result.config),
        MAPPING_FILENAME: result.mapping_document,
        SELECTED_PRICES_FILENAME: result.selected_prices_document,
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
        raise ModelsDevAdapterError(
            f"cannot write models.dev projection to {directory}: {exc}"
        ) from exc
    return targets
