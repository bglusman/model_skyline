from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from model_skyline.catalog_composition import (
    catalog_enrichment_policy_hash,
    enrich_catalog_across_workloads,
)
from model_skyline.engine import FrontierEngine
from model_skyline.io import (
    dump_json,
    load_catalog,
    load_catalog_enrichment_policy,
    load_config,
)

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "local-runtime-frontiers"
GENERATED = EXAMPLE / "generated"
POLICY = EXAMPLE / "harbor-pilot5-operational-gates-policy.json"
CONFIG = EXAMPLE / "harbor-operational-gated-frontiers.yaml"
BASE = GENERATED / "harbor-pilot5-quality-catalog.json"
PROJECTIONS = (
    GENERATED / "long-context-uncached-p126k-catalog.json",
    GENERATED / "tool-agent-uncached-p2048-o256-catalog.json",
)
ENRICHED = GENERATED / "harbor-pilot5-operational-gated-catalog.json"
GENERATED_AT = datetime(2026, 9, 14, 14, tzinfo=UTC)


def test_published_operational_gate_catalog_rebuilds_from_pinned_inputs() -> None:
    policy = load_catalog_enrichment_policy(POLICY)
    catalog = enrich_catalog_across_workloads(
        policy,
        load_catalog(BASE),
        (load_catalog(path) for path in PROJECTIONS),
    )

    assert catalog_enrichment_policy_hash(policy) == (
        "c2e35b575683528497455f9be6b0956f8d4d004c997dcb1f8113ffad7addcfeb"
    )
    assert dump_json(catalog) == ENRICHED.read_text(encoding="utf-8")
    assert len(catalog.offerings) == 5

    by_candidate = {}
    for item in catalog.offerings:
        pilot = item.metadata["pilot"]
        assert isinstance(pilot, dict)
        candidate = pilot["candidate"]
        assert isinstance(candidate, str)
        by_candidate[candidate] = item
    assert set(by_candidate["qwen38_flash_ds4"].signals) >= {
        "local_swap_delta_bytes",
        "local_tool_call_success_percent",
        "local_validated_context_tokens",
    }
    assert set(by_candidate["qwen38_baseline"].signals) >= {
        "local_swap_delta_bytes",
        "local_tool_call_success_percent",
        "local_validated_context_tokens",
    }
    # The tuned reasoning route is not inferred from the baseline probe route.
    assert "local_validated_context_tokens" not in by_candidate["qwen38_low_think4k"].signals


def test_published_operational_gate_frontiers_rebuild() -> None:
    config = load_config(CONFIG)
    catalog = load_catalog(ENRICHED)
    expected = {
        "local-agent-quality-latency-validated-126k": (
            "harbor-pilot5-operational-gated-quality-latency-126k-frontier.json",
            1,
        ),
        "local-agent-quality-memory-validated-126k": (
            "harbor-pilot5-operational-gated-quality-memory-126k-frontier.json",
            1,
        ),
        "local-agent-quality-latency-strict-128k": (
            "harbor-pilot5-operational-gated-quality-latency-strict-128k-frontier.json",
            0,
        ),
    }

    for frontier_id, (filename, member_count) in expected.items():
        snapshot = FrontierEngine().calculate(
            config,
            catalog,
            frontier_id,
            generated_at=GENERATED_AT,
        )
        assert dump_json(snapshot) == (GENERATED / filename).read_text(encoding="utf-8")
        assert len(snapshot.members) == member_count
        if member_count:
            assert snapshot.members[0].offering.model_id == "Qwen/Qwen3.8-Flash-Next"


def test_strict_128k_frontier_retains_the_measured_shortfall() -> None:
    snapshot = FrontierEngine().calculate(
        load_config(CONFIG),
        load_catalog(ENRICHED),
        "local-agent-quality-latency-strict-128k",
        generated_at=GENERATED_AT,
    )

    assert snapshot.members == ()
    ds4 = next(
        item
        for item in snapshot.rejected
        if item.offering_id.startswith("local/macbook-m5max-64/Qwen/Qwen3.8-Flash-Next@")
    )
    assert ds4.reasons == (
        "eligibility gate validated_context: value 125964 is below eligible minimum 128000",
    )
