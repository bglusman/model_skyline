from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from model_skyline.io import load_config
from model_skyline.local_measurements import LocalArtifactIdentity, LocalRuntimeIdentity
from model_skyline.models import EvidenceTier, UncertaintyMode

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "local-runtime-frontiers"
RECIPES = EXAMPLE / "recommended-frontier-recipes.yaml"
PILOT = EXAMPLE / "harbor-quality-pilot.yaml"
HARBOR_SMOKE_SUMMARIES = sorted((EXAMPLE / "raw").glob("harbor-smoke-*-summary.json"))


def test_recommended_local_frontier_recipes_are_valid_and_uncertainty_aware() -> None:
    config = load_config(RECIPES)

    assert set(config.frontiers) == {
        "fixed-128k-usefulness",
        "interactive-local-value",
        "quantization-screening",
        "remote-agent-value",
        "session-endurance",
        "warm-cache-operation",
    }
    assert config.frontiers["quantization-screening"].uncertainty is UncertaintyMode.POINT
    estimated = config.metrics["estimated_quality_lcb"].requirements
    assert estimated.require_bounds is True
    assert estimated.accepted_evidence_tiers == (EvidenceTier.ESTIMATED,)
    assert all(
        frontier.uncertainty is UncertaintyMode.ROBUST
        for frontier_id, frontier in config.frontiers.items()
        if frontier_id != "quantization-screening"
    )


def test_harbor_quality_pilot_is_exact_bounded_and_not_transferable() -> None:
    pilot = yaml.safe_load(PILOT.read_text(encoding="utf-8"))

    assert pilot["schema_version"] == "model-skyline/local-quality-pilot/v1"
    assert pilot["benchmark"]["full_task_count"] == 89
    assert pilot["benchmark"]["leaderboard_attempts_per_task"] == 5
    assert all(phase["task_set"] in pilot["task_sets"] for phase in pilot["phases"])

    smoke = pilot["task_sets"]["smoke"]
    selected = pilot["task_sets"]["pilot_5"]
    assert smoke["evidence_tier"] == EvidenceTier.PROXY.value
    assert smoke["transferable_to_full_benchmark"] is False
    assert selected["evidence_tier"] == EvidenceTier.MEASURED.value
    assert selected["transferable_to_full_benchmark"] is False
    assert len(selected["tasks"]) == 5
    assert len({task["name"] for task in selected["tasks"]}) == 5
    assert len({task["digest"] for task in selected["tasks"]}) == 5
    assert all(task["digest"].startswith("sha256:") for task in selected["tasks"])
    full = pilot["task_sets"]["all_89"]
    assert full["full_dataset"] is True
    assert full["expected_task_count"] == pilot["benchmark"]["full_task_count"]
    assert full["source_revision"] == pilot["benchmark"]["revision"]

    context_capacities = []
    for candidate in pilot["candidates"].values():
        profile_path = (EXAMPLE / candidate["system_profile"]).resolve()
        assert profile_path.is_relative_to(EXAMPLE.resolve())
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        assert profile["served_model"] == candidate["route"]
        assert (
            candidate["system_profile_sha256"]
            == hashlib.sha256(profile_path.read_bytes()).hexdigest()
        )
        LocalArtifactIdentity.model_validate(profile["artifact"])
        runtime = LocalRuntimeIdentity.model_validate(profile["runtime"])
        context_capacities.append(runtime.context_capacity_tokens)

    harness = pilot["harness"]
    assert harness["max_input_tokens"] + harness["max_output_tokens"] <= min(context_capacities)
    assert harness["concurrency"] == 1
    assert harness["model_switching"] == "batch_all_tasks_for_one_route"
    assert "verifier_ctrf_artifact_present_and_parseable" in pilot["validity_gates"]
    assert pilot["publication"]["full_benchmark_estimation_allowed"] is False
    assert pilot["publication"]["infrastructure_invalid_trials_count_as_failures"] is False


def test_published_harbor_smoke_summaries_are_prompt_free_and_auditable() -> None:
    assert len(HARBOR_SMOKE_SUMMARIES) >= 2
    expected_digest = "sha256:16948b980df9d96de616a205f5acca1c5d395de83ff4f8ffabcafacb93226f2e"

    for path in HARBOR_SMOKE_SUMMARIES:
        summary = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(summary)
        assert summary["schema_version"] == "model-skyline/harbor-local-job-summary/v1"
        assert summary["contains_prompts_or_model_messages"] is False
        assert "/Users/" not in serialized
        assert "all_messages" not in serialized
        assert summary["aggregate"]["invalid_trials"] == 0
        assert summary["protocol"] is not None
        assert summary["job"]["harbor"] == {
            "version": "0.23.0",
            "git_commit_hash": "88fdbc9d42e907c0414654f041ece5eaf798f538",
        }
        assert summary["expected"]["task_digests"] == {"terminal-bench/fix-git": expected_digest}
        for trial in summary["trials"]:
            assert trial["task_lock_digest"] == expected_digest
            assert trial["reward"] == "1.0"
            assert trial["agent_configuration"]["max_input_tokens"] == 114688
            assert all(
                len(digest) == 64 and set(digest) <= set("0123456789abcdef")
                for digest in trial["audit"].values()
            )
