from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from model_skyline.discovery import (
    DiscoveryError,
    DiscoverySource,
    discover_offerings,
    frontier_admission_decisions,
    load_frontier_policies,
    parse_feed,
    parse_openrouter,
)


def response(url: str, body: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-length": str(len(body))},
        content=body,
        request=httpx.Request("GET", url),
    )


def test_openrouter_catalog_preserves_source_and_filters_models() -> None:
    raw = json.dumps(
        {
            "data": [
                {"id": "openai/gpt-x", "name": "GPT X", "context_length": 1000},
                {"id": "anthropic/claude"},
            ]
        }
    ).encode()
    source = "https://openrouter.ai/api/v1/models"
    client = httpx.MockTransport(lambda request: response(str(request.url), raw))
    with httpx.Client(transport=client) as http:
        artifact = discover_offerings(
            client=http, model_pattern="gpt", retrieved_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    assert [item.model_id for item in artifact.offerings] == ["openai/gpt-x"]
    assert str(artifact.offerings[0].source.url) == source
    assert artifact.offerings[0].catalog_facts["context_length"] == 1000


def test_feed_is_reviewable_and_permissive_admission_is_marked() -> None:
    raw = b"<rss><channel><item><title>vendor/model</title><link>https://vendor.example/model</link></item></channel></rss>"
    source = "https://vendor.example/feed.xml"
    transport = httpx.MockTransport(lambda request: response(str(request.url), raw))
    with httpx.Client(transport=transport) as client:
        artifact = discover_offerings(
            client=client,
            include_openrouter=False,
            feeds=[source],
            admission_policy="vendor-reported",
        )
    assert artifact.offerings[0].admission == "vendor-reported*"
    assert artifact.offerings[0].vendor_quality == {"reported": True}
    assert artifact.review_queue[0]["offering_id"] == artifact.offerings[0].offering_id


def test_rejects_non_https_and_duplicate_json_keys() -> None:
    with pytest.raises(DiscoveryError):
        discover_offerings(
            client=httpx.Client(transport=httpx.MockTransport(lambda _: response("", b"{}"))),
            include_openrouter=False,
            feeds=["http://example.com/feed"],
        )
    source = DiscoverySource(
        url="https://openrouter.ai/api/v1/models",
        kind="catalog",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw_sha256="0" * 64,
    )
    with pytest.raises(DiscoveryError):
        parse_openrouter(b'{"data":[],"data":[]}', source)


def test_atom_entry_parser() -> None:
    raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>google/gemini</id><title>Gemini</title></entry></feed>'
    source = DiscoverySource(
        url="https://example.com/feed",
        kind="atom",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw_sha256="0" * 64,
    )
    assert parse_feed(raw, source)[0].model_id == "google/gemini"


def test_frontier_policies_are_independent_and_strict() -> None:
    catalog_source = DiscoverySource(
        url="https://catalog.example/models",
        kind="catalog",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        raw_sha256="0" * 64,
    )
    vendor_source = catalog_source.model_copy(
        update={"url": "https://vendor.example/feed", "kind": "rss"}
    )
    catalog = parse_openrouter(b'{"data":[{"id":"acme/catalog-model"}]}', catalog_source)[0]
    vendor = parse_feed(
        b"<rss><channel><item><title>vendor/model</title></item></channel></rss>", vendor_source
    )[0]
    decisions = frontier_admission_decisions(
        [catalog, vendor],
        {"strict": "require_quality", "catalog": "allow_catalog_only"},
    )
    assert [row.decision for row in decisions["strict"]] == ["exclude", "exclude"]
    assert decisions["strict"][0].reason == "excluded: evaluation quality evidence required"
    assert decisions["catalog"][0].decision == "admit"
    assert decisions["catalog"][0].uncertainty_marker is True
    assert decisions["catalog"][1].decision == "exclude"


def test_frontier_policy_file_is_strict_json(tmp_path) -> None:
    path = tmp_path / "policies.json"
    path.write_text('{"frontiers":{"fast":"mark_unverified"}}', encoding="utf-8")
    assert load_frontier_policies(path) == {"fast": "mark_unverified"}
    path.write_text('{"frontiers":{"fast":"plugin()"}}', encoding="utf-8")
    with pytest.raises(DiscoveryError, match="invalid frontier admission policy"):
        load_frontier_policies(path)
