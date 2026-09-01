from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import pytest
from typer.testing import CliRunner

from model_skyline.adapters.aider import import_aider_polyglot
from model_skyline.adapters.models_dev import (
    AiderModelsDevMapping,
    ModelsDevAdapterError,
    load_models_dev_source,
    project_aider_with_models_dev,
)
from model_skyline.cli import app
from model_skyline.engine import FrontierEngine
from model_skyline.io import load_catalog, load_config, load_frontier_history
from model_skyline.publisher import publish_project
from model_skyline.renderers import frontier_view
from model_skyline.version import VERSION

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
AIDER_FIXTURE = FIXTURES / "aider_polyglot_tiny.yml"
PRICING_FIXTURE = FIXTURES / "models_dev_tiny.json"
REVIEWED_MAPPING = ROOT / "examples" / "mappings" / "aider-models-dev.json"
PUBLICATION_WORKFLOW = ROOT / ".github" / "workflows" / "publish-models-dev-pages.yml"
RETRIEVED_AT = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)
runner = CliRunner()


def test_reviewed_publication_mapping_is_an_explicit_legacy_cohort() -> None:
    raw = REVIEWED_MAPPING.read_bytes()
    mapping = AiderModelsDevMapping.model_validate_json(raw)

    assert len(mapping.mappings) == 6
    assert all(entry.provider_id == "openai" for entry in mapping.mappings)
    assert sorted(entry.model_id for entry in mapping.mappings) == [
        "gpt-5",
        "gpt-5",
        "gpt-5",
        "o3",
        "o3",
        "o3-pro",
    ]
    assert all(entry.allow_deprecated for entry in mapping.mappings)
    assert all("2026-12-11" in entry.evidence for entry in mapping.mappings)

    mapping_sha256 = hashlib.sha256(raw).hexdigest()
    assert f"EXPECTED_MAPPING_SHA256: {mapping_sha256}" in PUBLICATION_WORKFLOW.read_text(
        encoding="utf-8"
    )


def _command_sha(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _mapping_value() -> dict[str, Any]:
    return {
        "schema_version": "model-skyline/aider-models-dev-mapping/v1alpha1",
        "scenario": "cache_disabled",
        "mappings": [
            {
                "source_offering_id": "aider-polyglot/2025-01-01--alpha-diff",
                "expected_source_model_id": "alpha-model",
                "provider_id": "provider-alpha",
                "model_id": "model@2026-08",
                "relationship": "same_provider_model_route",
                "expected_reasoning_effort": "high",
                "expected_command_sha256": _command_sha(
                    "aider --model provider-alpha/model@2026-08"
                ),
                "evidence": "Fixture command names this exact provider/model route.",
                "reviewed_at": "2026-08-31T14:00:00Z",
            },
            {
                "source_offering_id": "aider-polyglot/2025-01-02--beta-whole",
                "expected_source_model_id": "beta-model",
                "provider_id": "provider-beta",
                "model_id": "model:stable",
                "relationship": "same_provider_model_route",
                "expected_reasoning_effort": None,
                "expected_command_sha256": _command_sha("aider --model provider-beta/model:stable"),
                "evidence": "Fixture command names this exact provider/model route.",
                "reviewed_at": "2026-08-31T14:00:00+00:00",
            },
        ],
    }


def _mapping_bytes(value: dict[str, Any] | None = None) -> bytes:
    return json.dumps(value or _mapping_value(), separators=(",", ":")).encode()


def _aider(*, include_dirty: bool = False):
    return import_aider_polyglot(
        AIDER_FIXTURE,
        retrieved_at=RETRIEVED_AT,
        include_dirty=include_dirty,
    )


def _pricing(path: Path = PRICING_FIXTURE):
    return load_models_dev_source(path, retrieved_at=RETRIEVED_AT)


def test_exact_cache_disabled_projection_preserves_decimal_cost_and_both_sources() -> None:
    result = project_aider_with_models_dev(_aider(), _pricing(), _mapping_bytes())

    assert len(result.catalog.offerings) == 2
    assert result.catalog_source.id == "operator-models-dev-compatible"
    assert result.catalog_source.version == f"sha256:{result.catalog_source.raw_sha256}"
    assert result.pricing_source.id == "operator-selected-cache-disabled-prices"
    assert result.pricing_source.version == (
        f"selected-prices-sha256:{result.selected_prices_sha256}"
    )
    assert result.pricing_source.raw_sha256 is None
    assert result.pricing_source.license == "NOASSERTION"
    assert result.mapping_sha256 == hashlib.sha256(_mapping_bytes()).hexdigest()

    alpha = next(
        item for item in result.catalog.offerings if item.offering.provider == "provider-alpha"
    )
    assert alpha.offering.model_id == "model@2026-08"
    assert alpha.offering.billing_mode is None
    assert alpha.offering.offering_id.endswith(
        "@models.dev/92e4a812beac966246b5a5a9a711aa17b85d4a5dedefcc3d89d1955836ba434c"
    )
    assert alpha.signals["aider_prompt_tokens_total"].value == Decimal("1000000")
    assert alpha.signals["aider_completion_tokens_total"].value == Decimal("100000")
    assert alpha.signals["models_dev_input_usd_per_million_tokens"].value == Decimal("1")
    assert alpha.signals["models_dev_output_usd_per_million_tokens"].value == Decimal("10")
    assert alpha.metadata["models_dev_optional_rates_usd_per_million"] == {"cache_read": "0.1"}
    assert "cache_write" not in alpha.metadata["models_dev_optional_rates_usd_per_million"]
    assert alpha.metadata["performance_and_token_portability"] == (
        "operator_asserted_same_provider_model_route_across_time"
    )
    assert alpha.metadata["models_dev_reasoning_efforts"] == ["high", "low", "medium"]

    snapshot = FrontierEngine().calculate(
        result.config,
        result.catalog,
        "price-snapshot-reconstructed-token-cost-vs-solve-rate",
        generated_at=datetime(2026, 8, 31, 16, 0, tzinfo=UTC),
    )
    by_provider = {item.offering.provider: item for item in snapshot.evaluated}
    alpha_cost = by_provider["provider-alpha"].axes["price_snapshot_reconstructed_token_cost_usd"]
    beta_cost = by_provider["provider-beta"].axes["price_snapshot_reconstructed_token_cost_usd"]
    assert alpha_cost.value == Decimal("2")
    assert beta_cost.value == Decimal("0.30")
    assert alpha_cost.source_ids == (
        "aider-polyglot-leaderboard",
        "operator-selected-cache-disabled-prices",
    )
    assert {item.offering.provider for item in snapshot.members} == {
        "provider-alpha",
        "provider-beta",
    }
    assert set(result.config.frontiers) == {
        "price-snapshot-cost-per-attempted-vs-solve-rate",
        "price-snapshot-reconstructed-token-cost-vs-solve-rate",
        "price-snapshot-cost-per-solved-vs-solve-rate",
    }

    stale = FrontierEngine().calculate(
        result.config,
        result.catalog,
        "price-snapshot-cost-per-attempted-vs-solve-rate",
        generated_at=RETRIEVED_AT + timedelta(hours=49),
    )
    assert not stale.evaluated
    assert len(stale.rejected) == 2
    assert all("observation is stale" in " ".join(item.reasons) for item in stale.rejected)


def test_zero_solved_row_remains_usable_outside_cost_per_solved_frontier() -> None:
    aider = _aider()
    first = aider.catalog.offerings[0]
    changed = first.model_copy(
        update={
            "metadata": {**first.metadata, "pass_num_2": 0},
            "signals": {
                **first.signals,
                "solve_rate_2": first.signals["solve_rate_2"].model_copy(
                    update={"value": Decimal(0), "lower": Decimal(0)}
                ),
            },
        }
    )
    zero_solved_aider = replace(
        aider,
        catalog=aider.catalog.model_copy(
            update={"offerings": [changed, *aider.catalog.offerings[1:]]}
        ),
    )
    result = project_aider_with_models_dev(
        zero_solved_aider,
        _pricing(),
        _mapping_bytes(),
    )
    generated_at = RETRIEVED_AT + timedelta(hours=1)

    attempted = FrontierEngine().calculate(
        result.config,
        result.catalog,
        "price-snapshot-cost-per-attempted-vs-solve-rate",
        generated_at=generated_at,
    )
    assert any(
        item.offering.offering_id.startswith(first.offering.offering_id)
        for item in attempted.evaluated
    )

    per_solved = FrontierEngine().calculate(
        result.config,
        result.catalog,
        "price-snapshot-cost-per-solved-vs-solve-rate",
        generated_at=generated_at,
    )
    rejection = next(
        item
        for item in per_solved.rejected
        if item.offering_id.startswith(first.offering.offering_id)
    )
    assert any("formula operation div failed" in reason for reason in rejection.reasons)


def test_projection_refuses_tiers_identity_drift_compound_runs_and_implicit_same_route() -> None:
    duplicate_source = _mapping_value()
    duplicate_source["mappings"].append(
        {
            **duplicate_source["mappings"][0],
            "provider_id": "provider-beta",
            "model_id": "model:stable",
        }
    )
    with pytest.raises(ModelsDevAdapterError, match="duplicate source offering mapping"):
        project_aider_with_models_dev(
            _aider(),
            _pricing(),
            _mapping_bytes(duplicate_source),
        )

    duplicate_target = _mapping_value()
    duplicate_target["mappings"].append(
        {
            **duplicate_target["mappings"][0],
            "source_offering_id": "aider-polyglot/2025-01-03--dirty-architect",
            "expected_source_model_id": "gamma-model + editor-model",
            "expected_command_sha256": _command_sha(
                "aider --architect --model gamma-model --editor-model editor-model"
            ),
        }
    )
    with pytest.raises(ModelsDevAdapterError, match="duplicate target route mapping"):
        project_aider_with_models_dev(
            _aider(include_dirty=True),
            _pricing(),
            _mapping_bytes(duplicate_target),
        )

    tiered = _mapping_value()
    tiered["mappings"] = [
        {
            **tiered["mappings"][1],
            "model_id": "tiered-model",
        }
    ]
    with pytest.raises(ModelsDevAdapterError, match="tiered pricing"):
        project_aider_with_models_dev(_aider(), _pricing(), _mapping_bytes(tiered))

    changed = _mapping_value()
    changed["mappings"][0]["expected_source_model_id"] = "different"
    with pytest.raises(ModelsDevAdapterError, match="model changed"):
        project_aider_with_models_dev(_aider(), _pricing(), _mapping_bytes(changed))

    missing_evidence = _mapping_value()
    missing_evidence["mappings"][0].pop("expected_command_sha256")
    with pytest.raises(ModelsDevAdapterError, match="require expected_command_sha256"):
        project_aider_with_models_dev(
            _aider(),
            _pricing(),
            _mapping_bytes(missing_evidence),
        )

    compound = _mapping_value()
    compound["mappings"] = [
        {
            "source_offering_id": "aider-polyglot/2025-01-03--dirty-architect",
            "expected_source_model_id": "gamma-model + editor-model",
            "provider_id": "provider-alpha",
            "model_id": "model@2026-08",
            "relationship": "same_provider_model_route",
            "expected_reasoning_effort": "high",
            "expected_command_sha256": _command_sha(
                "aider --architect --model gamma-model --editor-model editor-model"
            ),
            "evidence": "Deliberately invalid compound fixture mapping.",
            "reviewed_at": "2026-08-31T14:00:00Z",
        }
    ]
    with pytest.raises(ModelsDevAdapterError, match="compound multi-model"):
        project_aider_with_models_dev(
            _aider(include_dirty=True),
            _pricing(),
            _mapping_bytes(compound),
        )


def test_projection_rejects_unaccounted_reasoning_meter_and_unsupported_effort(
    tmp_path: Path,
) -> None:
    payload = json.loads(PRICING_FIXTURE.read_text())
    alpha = payload["provider-alpha"]["models"]["model@2026-08"]
    alpha["cost"]["reasoning"] = 20
    reasoning_price = tmp_path / "reasoning-price.json"
    reasoning_price.write_text(json.dumps(payload))
    with pytest.raises(ModelsDevAdapterError, match="distinct reasoning meter"):
        project_aider_with_models_dev(
            _aider(),
            _pricing(reasoning_price),
            _mapping_bytes(),
        )

    del alpha["cost"]["reasoning"]
    alpha["reasoning_options"][0]["values"] = ["low", "medium"]
    unsupported = tmp_path / "unsupported-effort.json"
    unsupported.write_text(json.dumps(payload))
    with pytest.raises(ModelsDevAdapterError, match="does not attest reasoning effort 'high'"):
        project_aider_with_models_dev(
            _aider(),
            _pricing(unsupported),
            _mapping_bytes(),
        )


def test_selected_price_identity_tracks_only_fields_used_by_this_projection(
    tmp_path: Path,
) -> None:
    original = project_aider_with_models_dev(_aider(), _pricing(), _mapping_bytes())
    scaled_raw = PRICING_FIXTURE.read_bytes().replace(b'"input": 1,', b'"input": 1.00,', 1)
    scaled_raw = scaled_raw.replace(b'"output": 10,', b'"output": 10.000,', 1)
    assert scaled_raw != PRICING_FIXTURE.read_bytes()
    scaled_path = tmp_path / "equivalent-decimal-scale.json"
    scaled_path.write_bytes(scaled_raw)
    scaled = project_aider_with_models_dev(
        _aider(),
        _pricing(scaled_path),
        _mapping_bytes(),
    )
    assert original.catalog_source.raw_sha256 != scaled.catalog_source.raw_sha256
    assert original.selected_prices_sha256 == scaled.selected_prices_sha256
    assert original.selected_prices_document == scaled.selected_prices_document
    assert original.config == scaled.config

    payload = json.loads(PRICING_FIXTURE.read_text())
    payload["provider-alpha"]["models"]["model@2026-08"]["cost"]["cache_read"] = 0.2
    cache_changed_path = tmp_path / "cache-changed.json"
    cache_changed_path.write_text(json.dumps(payload))
    cache_changed = project_aider_with_models_dev(
        _aider(),
        _pricing(cache_changed_path),
        _mapping_bytes(),
    )

    assert original.catalog_source.raw_sha256 != cache_changed.catalog_source.raw_sha256
    assert original.selected_prices_sha256 == cache_changed.selected_prices_sha256
    assert original.pricing_source == cache_changed.pricing_source
    assert original.config == cache_changed.config
    assert original.catalog != cache_changed.catalog

    generated_at = RETRIEVED_AT + timedelta(hours=1)
    original_snapshot = FrontierEngine().calculate(
        original.config,
        original.catalog,
        "price-snapshot-cost-per-attempted-vs-solve-rate",
        generated_at=generated_at,
    )
    cache_changed_snapshot = FrontierEngine().calculate(
        cache_changed.config,
        cache_changed.catalog,
        "price-snapshot-cost-per-attempted-vs-solve-rate",
        generated_at=generated_at,
    )
    assert original_snapshot.config_hash == cache_changed_snapshot.config_hash
    assert original_snapshot.catalog_hash != cache_changed_snapshot.catalog_hash
    assert frontier_view(original_snapshot) == frontier_view(cache_changed_snapshot)

    publication = tmp_path / "publication"
    publish_project(
        original.config,
        [original.catalog],
        publication,
        project_id="selected-price-identity",
        generated_at=generated_at,
    )
    publish_project(
        cache_changed.config,
        [cache_changed.catalog],
        publication,
        project_id="selected-price-identity",
        generated_at=generated_at + timedelta(hours=1),
    )
    history = load_frontier_history(
        publication
        / "frontiers"
        / "price-snapshot-cost-per-attempted-vs-solve-rate"
        / "history.json"
    )
    assert len(history.entries) == 2
    feed = ET.parse(publication / "feeds" / "price-snapshot-cost-per-attempted-vs-solve-rate.xml")
    assert len(feed.findall("./channel/item")) == 1

    payload["provider-alpha"]["models"]["model@2026-08"]["cost"]["input"] = 2
    input_changed_path = tmp_path / "input-changed.json"
    input_changed_path.write_text(json.dumps(payload))
    input_changed = project_aider_with_models_dev(
        _aider(),
        _pricing(input_changed_path),
        _mapping_bytes(),
    )
    assert original.selected_prices_sha256 != input_changed.selected_prices_sha256
    assert original.pricing_source != input_changed.pricing_source
    assert original.catalog.workload.version != input_changed.catalog.workload.version


def test_source_loader_and_parser_fail_closed_on_network_and_json_ambiguity(tmp_path: Path) -> None:
    with pytest.raises(ModelsDevAdapterError, match="SHA-256 mismatch"):
        load_models_dev_source(PRICING_FIXTURE, expected_sha256="0" * 64)

    with pytest.raises(ModelsDevAdapterError, match="reserved IP hosts"):
        load_models_dev_source(
            "https://127.0.0.1/api.json",
        )

    with pytest.raises(ModelsDevAdapterError, match="retrieved_at is required"):
        load_models_dev_source(PRICING_FIXTURE)

    with pytest.raises(ModelsDevAdapterError, match="exact official"):
        load_models_dev_source("https://models.dev/private/api.json")

    def redirect(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://models.dev/elsewhere.json"})

    with pytest.raises(ModelsDevAdapterError, match="redirects are not followed"):
        load_models_dev_source(
            "https://models.dev/api.json",
            transport=httpx.MockTransport(redirect),
        )

    def compressed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
            stream=httpx.ByteStream(b"not-used"),
        )

    with pytest.raises(ModelsDevAdapterError, match="identity content encoding"):
        load_models_dev_source(
            "https://models.dev/api.json",
            transport=httpx.MockTransport(compressed),
        )

    def injected_transport(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["user-agent"] == (f"model-skyline-models-dev-adapter/{VERSION}")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(PRICING_FIXTURE.read_bytes()),
        )

    injected = load_models_dev_source(
        "https://models.dev/api.json",
        transport=httpx.MockTransport(injected_transport),
    )
    assert injected.official is False
    injected_result = project_aider_with_models_dev(_aider(), injected, _mapping_bytes())
    assert injected_result.catalog_source.id == "operator-models-dev-compatible"
    assert injected_result.catalog_source.license == "NOASSERTION"

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"provider-alpha":{"id":"first","id":"second","name":"P","models":{}}}')
    with pytest.raises(ModelsDevAdapterError, match="duplicate JSON object key"):
        project_aider_with_models_dev(_aider(), _pricing(duplicate), _mapping_bytes())

    digest = hashlib.sha256(PRICING_FIXTURE.read_bytes()).hexdigest()
    asserted = load_models_dev_source(
        PRICING_FIXTURE,
        expected_sha256=digest,
        retrieved_at=RETRIEVED_AT,
        assert_official_source=True,
    )
    asserted_result = project_aider_with_models_dev(_aider(), asserted, _mapping_bytes())
    assert asserted_result.catalog_source.id == "models-dev-api"
    assert asserted_result.pricing_source.id == "models-dev-selected-cache-disabled-prices"
    assert asserted_result.pricing_source.license == "MIT"

    operator_result = project_aider_with_models_dev(_aider(), _pricing(), _mapping_bytes())
    assert asserted_result.catalog.workload.version != operator_result.catalog.workload.version
    with pytest.raises(ValueError, match="catalog workload does not match"):
        FrontierEngine().calculate(
            asserted_result.config,
            operator_result.catalog,
            "price-snapshot-cost-per-attempted-vs-solve-rate",
            generated_at=RETRIEVED_AT + timedelta(hours=1),
        )


def test_cli_writes_and_evaluates_a_self_contained_real_projection(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_bytes(_mapping_bytes())
    output = tmp_path / "projection"
    result = runner.invoke(
        app,
        [
            "project-aider-models-dev",
            str(output),
            str(mapping),
            "--aider-source",
            str(AIDER_FIXTURE),
            "--aider-retrieved-at",
            "2026-08-31T15:00:00Z",
            "--pricing-source",
            str(PRICING_FIXTURE),
            "--pricing-retrieved-at",
            "2026-08-31T15:00:00Z",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "projected 2 exact" in result.output
    config = load_config(output / "frontier.yaml")
    catalog = load_catalog(output / "observations.json")
    manifest = json.loads((output / "projection.json").read_text())
    assert len(catalog.offerings) == 2
    assert "price-snapshot-cost-per-attempted-vs-solve-rate" in config.frontiers
    assert manifest["scenario"] == "cache_disabled"
    assert manifest["pricing_max_age_hours"] == "48"
    assert (output / "mapping.json").read_bytes() == _mapping_bytes()
    assert (
        manifest["sources"]["pricing_catalog"]["raw_sha256"]
        == hashlib.sha256(PRICING_FIXTURE.read_bytes()).hexdigest()
    )
    assert manifest["sources"]["selected_prices"]["raw_sha256"] is None
    assert (
        hashlib.sha256((output / "selected-prices.json").read_bytes()).hexdigest()
        == manifest["selected_prices_sha256"]
    )

    evaluated = runner.invoke(
        app,
        [
            "evaluate",
            str(output / "frontier.yaml"),
            str(output / "observations.json"),
            "price-snapshot-cost-per-attempted-vs-solve-rate",
            "--as-of",
            "2026-08-31T16:00:00Z",
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    assert "provider-alpha" in evaluated.output
    assert "provider-beta" in evaluated.output
