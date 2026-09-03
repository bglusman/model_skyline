"""Strict, provenance-preserving discovery of public model offerings."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MAX_SOURCE_BYTES = 8_000_000
MAX_FEED_BYTES = 2_000_000
MAX_MODELS = 10_000
MAX_FEEDS = 32


class DiscoveryError(ValueError):
    """A source failed strict discovery validation."""


FrontierAdmissionPolicy = Literal[
    "require_quality", "allow_catalog_only", "allow_vendor_reported", "mark_unverified"
]
FRONTIER_ADMISSION_POLICIES = frozenset(
    {"require_quality", "allow_catalog_only", "allow_vendor_reported", "mark_unverified"}
)


class FrontierAdmission(BaseModel):
    """The independent admission decision for one frontier and offering."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    offering_id: str = Field(min_length=1, max_length=512)
    decision: Literal["admit", "exclude"]
    reason: str = Field(min_length=1, max_length=512)
    uncertainty_marker: bool
    admission: str = Field(pattern=r"^(review|catalog-only\*|vendor-reported\*|unverified\*)$")


def validate_frontier_policy(value: str) -> FrontierAdmissionPolicy:
    """Validate a policy name without executing operator-supplied code."""
    if value not in FRONTIER_ADMISSION_POLICIES:
        raise DiscoveryError(
            "invalid frontier admission policy; expected require_quality, "
            "allow_catalog_only, allow_vendor_reported, or mark_unverified"
        )
    return value  # type: ignore[return-value]


def load_frontier_policies(path: Path) -> dict[str, FrontierAdmissionPolicy]:
    """Load ``{"frontiers": {"id": "policy"}}`` from a JSON policy file."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey):
        raise DiscoveryError("frontier policy file is invalid JSON") from None
    if not isinstance(value, dict) or not isinstance(value.get("frontiers"), dict):
        raise DiscoveryError("frontier policy file must contain a frontiers object")
    result: dict[str, FrontierAdmissionPolicy] = {}
    for frontier_id, policy in value["frontiers"].items():
        if not isinstance(frontier_id, str) or not frontier_id or not isinstance(policy, str):
            raise DiscoveryError("frontier policy ids and values must be non-empty strings")
        result[frontier_id] = validate_frontier_policy(policy)
    return result


class DiscoverySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: AnyHttpUrl
    kind: str = Field(pattern=r"^(catalog|rss|atom)$")
    retrieved_at: datetime
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DiscoveredOffering(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    offering_id: str = Field(min_length=1, max_length=512)
    model_id: str = Field(min_length=1, max_length=512)
    provider: str = Field(min_length=1, max_length=256)
    name: str | None = Field(default=None, max_length=1024)
    source: DiscoverySource
    catalog_facts: dict[str, Any] = Field(default_factory=dict)
    vendor_quality: dict[str, Any] | None = None
    admission: str = Field(pattern=r"^(review|catalog-only\*|vendor-reported\*|unverified\*)$")


class DiscoveryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "model-skyline/discovery/v1alpha1"
    retrieved_at: datetime
    sources: list[DiscoverySource] = Field(max_length=MAX_FEEDS + 1)
    offerings: list[DiscoveredOffering] = Field(max_length=MAX_MODELS)
    review_queue: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_MODELS)
    admission_policy: str = Field(pattern=r"^(review|catalog-only|vendor-reported)$")
    frontier_admissions: dict[str, list[FrontierAdmission]] = Field(default_factory=dict)


class _DuplicateKey(ValueError):
    pass


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _fetch(client: httpx.Client, url: str, maximum: int) -> bytes:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise DiscoveryError(
            "discovery sources must be HTTPS URLs without credentials, query, or fragment"
        )
    try:
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code < 200 or response.status_code >= 300:
                raise DiscoveryError(f"source returned HTTP {response.status_code}")
            if response.headers.get("content-encoding", "identity") not in {"", "identity"}:
                raise DiscoveryError("compressed source responses are not accepted")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise DiscoveryError("source exceeds byte limit")
            raw = bytearray()
            iterator = (
                response.iter_raw() if not response.is_stream_consumed else (response.content,)
            )
            for chunk in iterator:
                raw.extend(chunk)
                if len(raw) > maximum:
                    raise DiscoveryError("source exceeds byte limit")
            return bytes(raw)
    except DiscoveryError:
        raise
    except httpx.HTTPError as exc:
        raise DiscoveryError("cannot fetch discovery source") from exc


def _source(url: str, kind: str, raw: bytes, when: datetime) -> DiscoverySource:
    return DiscoverySource(
        url=url, kind=kind, retrieved_at=when, raw_sha256=hashlib.sha256(raw).hexdigest()
    )


def _provider(model_id: str) -> str:
    return model_id.split("/", 1)[0] if "/" in model_id else "unknown"


def parse_openrouter(
    raw: bytes, source: DiscoverySource, *, model_pattern: str | None = None
) -> list[DiscoveredOffering]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError):
        raise DiscoveryError("OpenRouter response is invalid JSON") from None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("data"), list)
        or len(value["data"]) > MAX_MODELS
    ):
        raise DiscoveryError("OpenRouter response has an invalid shape")
    pattern = re.compile(model_pattern) if model_pattern else None
    result: list[DiscoveredOffering] = []
    for item in value["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise DiscoveryError("OpenRouter model has an invalid id")
        model_id = item["id"]
        if pattern and not pattern.search(model_id):
            continue
        facts = {
            key: item[key]
            for key in ("context_length", "architecture", "pricing", "supported_parameters")
            if key in item
        }
        result.append(
            DiscoveredOffering(
                offering_id=f"openrouter/{model_id}",
                model_id=model_id,
                provider=_provider(model_id),
                name=item.get("name"),
                source=source,
                catalog_facts=facts,
                admission="review",
            )
        )
    return result


def parse_feed(raw: bytes, source: DiscoverySource) -> list[DiscoveredOffering]:
    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError):
        raise DiscoveryError("RSS/Atom source is invalid XML") from None
    result: list[DiscoveredOffering] = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in {"item", "entry"}:
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in item}
        model_id = fields.get("model") or fields.get("id") or fields.get("title")
        if not model_id or len(model_id) > 512:
            continue
        result.append(
            DiscoveredOffering(
                offering_id=(
                    f"feed/{hashlib.sha256((str(source.url) + model_id).encode()).hexdigest()[:24]}"
                ),
                model_id=model_id,
                provider=_provider(model_id),
                name=fields.get("title"),
                source=source,
                catalog_facts={"link": fields.get("link")} if fields.get("link") else {},
                admission="review",
            )
        )
        if len(result) >= MAX_MODELS:
            break
    return result


def frontier_admission_decisions(
    offerings: list[DiscoveredOffering],
    frontier_policies: dict[str, FrontierAdmissionPolicy],
) -> dict[str, list[FrontierAdmission]]:
    """Apply per-frontier policy, independently of ranking or evaluation.

    Catalog evidence proves identity/provenance, not quality evaluation.
    Weaker-evidence admissions are explicitly marked and explained.
    """
    decisions: dict[str, list[FrontierAdmission]] = {}
    for frontier_id, policy in frontier_policies.items():
        validate_frontier_policy(policy)
        rows: list[FrontierAdmission] = []
        for item in offerings:
            if item.vendor_quality is not None and item.vendor_quality.get("reported") is not True:
                decision, reason, marked, admission = (
                    "admit",
                    "evaluation quality evidence present",
                    False,
                    "review",
                )
            elif policy == "require_quality":
                decision, reason, marked, admission = (
                    "exclude",
                    "excluded: evaluation quality evidence required",
                    False,
                    "review",
                )
            elif policy == "allow_catalog_only" and item.source.kind == "catalog":
                decision, reason, marked, admission = (
                    "admit",
                    "admitted: catalog verified; evaluation unverified",
                    True,
                    "catalog-only*",
                )
            elif policy in {"allow_vendor_reported", "mark_unverified"}:
                decision, reason, marked = (
                    "admit",
                    f"admitted: {policy}; evaluation unverified",
                    True,
                )
                admission = (
                    "unverified*"
                    if policy == "mark_unverified"
                    else ("catalog-only*" if item.source.kind == "catalog" else "vendor-reported*")
                )
            else:
                decision, reason, marked, admission = (
                    "exclude",
                    "excluded: policy does not allow this evidence",
                    False,
                    "review",
                )
            rows.append(
                FrontierAdmission(
                    offering_id=item.offering_id,
                    decision=decision,
                    reason=reason,
                    uncertainty_marker=marked,
                    admission=admission,
                )
            )
        decisions[frontier_id] = rows
    return decisions


def discover_offerings(
    *,
    feeds: list[str] | tuple[str, ...] = (),
    include_openrouter: bool = True,
    model_pattern: str | None = None,
    admission_policy: str = "review",
    frontier_policies: dict[str, FrontierAdmissionPolicy] | None = None,
    retrieved_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> DiscoveryArtifact:
    if len(feeds) > MAX_FEEDS or not include_openrouter and not feeds:
        raise DiscoveryError("at least one discovery source is required")
    if admission_policy not in {"review", "catalog-only", "vendor-reported"}:
        raise DiscoveryError("invalid admission policy")
    when = retrieved_at or datetime.now(UTC)
    if when.tzinfo is None:
        raise DiscoveryError("retrieved_at must include a timezone")
    owned = client is None
    active = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
    sources: list[DiscoverySource] = []
    offerings: dict[str, DiscoveredOffering] = {}
    try:
        for url, kind, limit in (
            [(OPENROUTER_MODELS_URL, "catalog", MAX_SOURCE_BYTES)] if include_openrouter else []
        ) + [(url, "rss", MAX_FEED_BYTES) for url in feeds]:
            raw = _fetch(active, url, limit)
            if kind == "catalog":
                source_kind = "catalog"
                found = parse_openrouter(
                    raw, _source(url, source_kind, raw, when), model_pattern=model_pattern
                )
            else:
                source_kind = (
                    "atom" if raw.lstrip().startswith(b"<feed") or b"<feed " in raw[:256] else "rss"
                )
                found = parse_feed(raw, _source(url, source_kind, raw, when))
            source = _source(url, source_kind, raw, when)
            sources.append(source)
            for item in found:
                if admission_policy == "catalog-only" and kind == "catalog":
                    item = item.model_copy(update={"admission": "catalog-only*"})
                elif admission_policy == "vendor-reported" and kind != "catalog":
                    item = item.model_copy(
                        update={
                            "vendor_quality": {"reported": True},
                            "admission": "vendor-reported*",
                        }
                    )
                offerings[item.offering_id] = item
    finally:
        if owned:
            active.close()
    ordered = [offerings[key] for key in sorted(offerings)]
    frontier_admissions = frontier_admission_decisions(ordered, frontier_policies or {})
    queue = [
        {
            "offering_id": item.offering_id,
            "reason": "unverified quality evidence; apply frontier_admissions before ranking",
            "source_url": str(item.source.url),
            "uncertainty_marker": item.admission.endswith("*"),
        }
        for item in ordered
    ]
    return DiscoveryArtifact(
        retrieved_at=when,
        sources=sources,
        offerings=ordered,
        review_queue=queue,
        admission_policy=admission_policy,
        frontier_admissions=frontier_admissions,
    )
