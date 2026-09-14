from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from model_skyline.io import load_catalog, load_config, load_frontier_snapshot
from model_skyline.local_measurements import LocalArtifactIdentity, LocalRuntimeIdentity
from model_skyline.models import EvidenceTier, UncertaintyMode

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "local-runtime-frontiers"
RECIPES = EXAMPLE / "recommended-frontier-recipes.yaml"
PILOT = EXAMPLE / "harbor-quality-pilot.yaml"
FLASH_CODER_SCREEN = EXAMPLE / "harbor-quality-screen-qwen38-flash-coder.yaml"
FROZEN_HARBOR_PILOT_SHA256 = "07e4af7f9acdfef54b0627a3722984baa7ab48bba8b1472e97f42ecc00147c41"
TASK_MANIFEST = EXAMPLE / "terminal-bench-2.1-task-manifest.json"
HARBOR_PILOT_SMOKE_SUMMARIES = sorted(
    path
    for path in (EXAMPLE / "raw").glob("harbor-smoke-*-summary.json")
    if "qwen38-flash-coder" not in path.name
)
FLASH_CODER_SCREEN_SUMMARIES = {
    "qwen38_flash_coder_q4km": (
        EXAMPLE / "raw" / "harbor-smoke-qwen38-flash-coder-q4km-fix-git-summary.json"
    ),
    "qwen38_flash_coder_q4km_xhigh": (
        EXAMPLE / "raw" / "harbor-smoke-qwen38-flash-coder-q4km-xhigh-fix-git-summary.json"
    ),
}
HARBOR_PILOT_SUMMARIES = {
    "ornith": EXAMPLE / "raw" / "harbor-pilot5-ornith15-baseline-summary.json",
    "ds4": EXAMPLE / "raw" / "harbor-pilot5-qwen38-flash-next-ds4-summary.json",
    "qwen38": EXAMPLE / "raw" / "harbor-pilot5-qwen38-baseline-f16kv-summary.json",
    "qwen38_low_think4k": EXAMPLE / "raw" / "harbor-pilot5-qwen38-low-think4k-summary.json",
    "muse": EXAMPLE / "raw" / "harbor-pilot5-muse-glimmer-target-summary.json",
}


def test_recommended_local_frontier_recipes_are_valid_and_uncertainty_aware() -> None:
    config = load_config(RECIPES)

    assert set(config.frontiers) == {
        "fixed-128k-usefulness",
        "interactive-local-value",
        "local-agent-cache-demand",
        "local-agent-memory-value",
        "quantization-screening",
        "remote-agent-value",
        "session-endurance",
        "warm-cache-operation",
    }
    assert config.frontiers["quantization-screening"].uncertainty is UncertaintyMode.POINT
    assert config.workloads["measured-local-agent-v1"].unit == "benchmark_task"
    assert config.frontiers["interactive-local-value"].axes[1].metric == "p95_agent_task_wall"
    for frontier_id, metric in {
        "interactive-local-value": "measured_agent_quality",
        "local-agent-cache-demand": "measured_agent_quality",
        "local-agent-memory-value": "measured_agent_quality",
        "fixed-128k-usefulness": "measured_long_context_quality",
    }.items():
        assert config.frontiers[frontier_id].eligibility.minimum_axis_values[metric] == 60
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
    assert full["workload_unit"] == "task"
    assert full["workload_version"].endswith("+full-v1")
    assert full["task_manifest_sha256"] == hashlib.sha256(TASK_MANIFEST.read_bytes()).hexdigest()
    manifest = json.loads(TASK_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "model-skyline/harbor-task-manifest/v1"
    assert manifest["revision"] == pilot["benchmark"]["revision"]
    assert len(manifest["tasks"]) == full["expected_task_count"]
    manifest_names = {task["name"] for task in manifest["tasks"]}
    manifest_digests = {task["digest"] for task in manifest["tasks"]}
    assert len(manifest_names) == len(manifest_digests) == 89
    assert {task["name"] for task in selected["tasks"]} <= manifest_names
    assert {task["digest"] for task in selected["tasks"]} <= manifest_digests

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
    assert harness["execution_timezone"] == "America/New_York"
    assert harness["model_switching"] == "batch_all_tasks_for_one_route"
    assert "verifier_ctrf_artifact_present_and_parseable" in pilot["validity_gates"]
    assert "trial_exception_is_absent_or_protocol_quality_attributable" in pilot["validity_gates"]
    assert "memory_capture_job_lock_hash_matches_quality_summary" in pilot["validity_gates"]
    assert (
        "memory_frontier_uses_only_tasks_sampled_before_agent_execution" in pilot["validity_gates"]
    )
    assert pilot["publication"]["full_benchmark_estimation_allowed"] is False
    assert pilot["publication"]["infrastructure_invalid_trials_count_as_failures"] is False
    assert selected["workload_unit"] == "task"
    assert selected["workload_version"].startswith("terminal-bench@")


def test_published_harbor_smoke_summaries_are_prompt_free_and_auditable() -> None:
    assert len(HARBOR_PILOT_SMOKE_SUMMARIES) >= 2
    expected_digest = "sha256:16948b980df9d96de616a205f5acca1c5d395de83ff4f8ffabcafacb93226f2e"

    for path in HARBOR_PILOT_SMOKE_SUMMARIES:
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


def test_flash_coder_screen_is_additive_auditable_and_not_promoted() -> None:
    pilot_digest = hashlib.sha256(PILOT.read_bytes()).hexdigest()
    screen_digest = hashlib.sha256(FLASH_CODER_SCREEN.read_bytes()).hexdigest()
    screen = yaml.safe_load(FLASH_CODER_SCREEN.read_text(encoding="utf-8"))
    pilot = yaml.safe_load(PILOT.read_text(encoding="utf-8"))

    assert screen["schema_version"] == "model-skyline/local-quality-pilot/v1"
    assert pilot_digest == FROZEN_HARBOR_PILOT_SHA256
    assert screen["screen"]["relationship_to_frozen_pilot"] == "additive_candidate_gate"
    assert screen["screen"]["comparable_protocol_sha256"] == FROZEN_HARBOR_PILOT_SHA256
    assert screen["screen"]["on_failure"] == "do_not_run_five_task_pilot"
    for field in (
        "name",
        "version",
        "harbor_version",
        "harbor_revision",
        "parser",
        "temperature",
        "top_p",
        "max_turns",
        "max_input_tokens",
        "max_output_tokens",
    ):
        assert screen["harness"][field] == pilot["harness"][field]

    for candidate_name, candidate in screen["candidates"].items():
        profile_path = EXAMPLE / candidate["system_profile"]
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        assert (
            candidate["system_profile_sha256"]
            == hashlib.sha256(profile_path.read_bytes()).hexdigest()
        )
        assert profile["served_model"] == candidate["route"]
        assert "long-context" not in profile["capabilities"]
        LocalArtifactIdentity.model_validate(profile["artifact"])
        runtime = LocalRuntimeIdentity.model_validate(profile["runtime"])
        assert runtime.context_capacity_tokens == 131072

        summary = json.loads(
            FLASH_CODER_SCREEN_SUMMARIES[candidate_name].read_text(encoding="utf-8")
        )
        serialized = json.dumps(summary)
        assert summary["contains_prompts_or_model_messages"] is False
        assert "/Users/" not in serialized
        assert summary["protocol"]["protocol_sha256"] == screen_digest
        assert summary["aggregate"] == {
            "invalid_trials": 0,
            "success_percent": "0.0",
            "successes": "0.0",
            "valid_trials": 1,
        }
        trial = summary["trials"][0]
        assert trial["reward"] == "0.0"
        assert trial["agent_episodes"] == 40
        assert trial["ctrf"]["failed"] == trial["ctrf"]["tests"] == 2
        assert trial["parser_feedback_events"]["errors"] > 0

    for profile_path in (EXAMPLE / "system-profiles").glob("qwen38-flash-coder-26gb-q4km-*.json"):
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        LocalArtifactIdentity.model_validate(profile["artifact"])
        LocalRuntimeIdentity.model_validate(profile["runtime"])

    generated = EXAMPLE / "generated"
    uncached_catalog = load_catalog(generated / "tool-agent-uncached-p2048-o256-catalog.json")
    uncached_candidate = [
        offering
        for offering in uncached_catalog.offerings
        if offering.offering.model_id == "Jab1718/qwen3.8-flash-coder-26gb-gguf"
    ]
    assert len(uncached_candidate) == 1
    uncached_frontier = load_frontier_snapshot(
        generated / "tool-agent-uncached-p2048-o256-frontier.json"
    )
    assert [member.offering for member in uncached_frontier.members] == [
        uncached_candidate[0].offering
    ]

    warm_catalog = load_catalog(generated / "tool-agent-warm-p2048-o1024-catalog.json")
    assert any(
        offering.offering.model_id == "Jab1718/qwen3.8-flash-coder-26gb-gguf"
        for offering in warm_catalog.offerings
    )
    warm_frontier = load_frontier_snapshot(generated / "tool-agent-warm-p2048-o1024-frontier.json")
    assert all(
        member.offering.model_id != "Jab1718/qwen3.8-flash-coder-26gb-gguf"
        for member in warm_frontier.members
    )

    coverage = json.loads((generated / "cross-frontier-coverage.json").read_text(encoding="utf-8"))
    assert coverage["schema_version"] == "model-skyline/local-frontier-coverage/v1"
    flash_coder_family = next(
        family
        for family in coverage["model_families"]
        if family["model_id"] == "Jab1718/qwen3.8-flash-coder-26gb-gguf"
    )
    assert flash_coder_family["frontier_count"] == 1
    assert flash_coder_family["frontiers"] == ["uncached-agent-tools"]


def test_published_capacity_frontier_retains_failed_candidate_for_audit() -> None:
    generated = EXAMPLE / "generated"
    catalog = load_catalog(generated / "validated-capacity-catalog.json")
    offerings = {offering.offering.model_id: offering for offering in catalog.offerings}

    assert set(offerings) == {
        "Jab1718/qwen3.8-flash-coder-26gb-gguf",
        "Qwen/Qwen3.8-27B",
        "Qwen/Qwen3.8-Flash-Next",
        "ornith-ai/Ornith-1.5-35B-A3B",
    }
    flash_coder = offerings["Jab1718/qwen3.8-flash-coder-26gb-gguf"]
    ornith = offerings["ornith-ai/Ornith-1.5-35B-A3B"]
    assert "long-context" not in flash_coder.offering.capabilities
    assert "local_validated_context_tokens" not in flash_coder.signals
    assert "local_peak_process_physical_footprint_bytes" not in flash_coder.signals
    assert flash_coder.metadata["capacity_validation"] == {
        "attempted_position_count": 4,
        "configured_context_tokens": 131072,
        "fully_passing_position_count": 0,
        "largest_attempted_context_tokens": 125964,
        "largest_validated_context_tokens": None,
    }
    flash_ladder = flash_coder.metadata["capacity_ladder"]
    assert isinstance(flash_ladder, list)
    assert [item["actual_input_tokens"] for item in flash_ladder] == [
        2012,
        32732,
        65500,
        125964,
    ]
    assert all(item["passed"] is False for item in flash_ladder)
    assert len({item["raw_sha256"] for item in flash_ladder}) == 1
    assert "local_validated_context_tokens" not in ornith.signals
    assert "local_peak_process_physical_footprint_bytes" not in ornith.signals
    assert ornith.metadata["capacity_validation"] == {
        "attempted_position_count": 1,
        "configured_context_tokens": 262144,
        "fully_passing_position_count": 0,
        "largest_attempted_context_tokens": 125964,
        "largest_validated_context_tokens": None,
    }

    frontier = load_frontier_snapshot(generated / "validated-capacity-frontier.json")
    assert [member.offering.model_id for member in frontier.members] == ["Qwen/Qwen3.8-Flash-Next"]
    assert {rejected.offering_id for rejected in frontier.rejected} == {
        flash_coder.offering.offering_id,
        ornith.offering.offering_id,
    }

    coverage = json.loads((generated / "cross-frontier-coverage.json").read_text(encoding="utf-8"))
    capacity = next(item for item in coverage["frontiers"] if item["label"] == "validated-capacity")
    assert capacity["snapshot_id"] == frontier.snapshot_id
    assert capacity["rejected_count"] == 2


def test_published_pilot_population_and_quality_frontiers_are_exact() -> None:
    summaries = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in HARBOR_PILOT_SUMMARIES.items()
    }
    expected_aggregates = {
        "ornith": ("60.0", "3.0"),
        "ds4": ("60.0", "3.0"),
        "qwen38": ("40.0", "2.0"),
        "qwen38_low_think4k": ("80.0", "4.0"),
        "muse": ("60.0", "3.0"),
    }
    for name, summary in summaries.items():
        serialized = json.dumps(summary)
        assert summary["contains_prompts_or_model_messages"] is False
        assert "/Users/" not in serialized
        assert "all_messages" not in serialized
        success_percent, successes = expected_aggregates[name]
        assert summary["aggregate"] == {
            "invalid_trials": 0,
            "success_percent": success_percent,
            "successes": successes,
            "valid_trials": 5,
        }

    expected_timeout_counts = {
        "ornith": 1,
        "ds4": 2,
        "qwen38": 3,
        "qwen38_low_think4k": 1,
        "muse": 1,
    }
    expected_incomplete_counts = {
        "ornith": 1,
        "ds4": 2,
        "qwen38": 3,
        "qwen38_low_think4k": 1,
        "muse": 0,
    }
    for name, summary in summaries.items():
        timeouts = [trial for trial in summary["trials"] if trial["quality_attributable_exception"]]
        assert len(timeouts) == expected_timeout_counts[name]
        assert all(
            trial["quality_attributable_exception"] == "AgentTimeoutError" for trial in timeouts
        )
        assert (
            sum(trial["incomplete_api_requests"] for trial in summary["trials"])
            == (expected_incomplete_counts[name])
        )

    catalog = load_catalog(EXAMPLE / "generated" / "harbor-pilot5-quality-catalog.json")
    assert len(catalog.offerings) == 5
    offerings = {
        offering.metadata["pilot"]["candidate"]: offering for offering in catalog.offerings
    }
    ornith = offerings["ornith15_baseline"]
    ds4 = offerings["qwen38_flash_ds4"]
    qwen38 = offerings["qwen38_baseline"]
    qwen38_low_think4k = offerings["qwen38_low_think4k"]
    muse = offerings["muse_glimmer_target"]
    assert ornith.signals["local_pilot_task_success_percent"].value == 60
    assert ds4.signals["local_pilot_task_success_percent"].value == 60
    assert qwen38.signals["local_pilot_task_success_percent"].value == 40
    assert qwen38_low_think4k.signals["local_pilot_task_success_percent"].value == 80
    assert muse.signals["local_pilot_task_success_percent"].value == 60
    assert "local_pilot_total_uncached_input_tokens" not in ornith.signals
    assert "local_pilot_total_uncached_input_tokens" not in ds4.signals
    assert "local_pilot_total_uncached_input_tokens" not in qwen38.signals
    assert "local_pilot_total_uncached_input_tokens" not in qwen38_low_think4k.signals
    assert ornith.metadata["pilot"]["token_accounting"]["incomplete_api_requests"] == 1
    assert ds4.metadata["pilot"]["token_accounting"]["incomplete_api_requests"] == 2
    assert qwen38.metadata["pilot"]["token_accounting"]["incomplete_api_requests"] == 3
    assert qwen38_low_think4k.metadata["pilot"]["token_accounting"]["incomplete_api_requests"] == 1
    assert muse.signals["local_pilot_total_uncached_input_tokens"].value == 51_424
    assert muse.metadata["pilot"]["token_accounting"]["incomplete_api_requests"] == 0
    assert "local_peak_process_physical_footprint_bytes" not in ornith.signals
    assert ornith.metadata["pilot"]["memory"]["eligible"] is False
    assert ds4.signals["local_peak_process_physical_footprint_bytes"].value == 5_461_911_280
    assert ds4.metadata["pilot"]["memory"]["eligible"] is True
    assert qwen38.signals["local_peak_process_physical_footprint_bytes"].value == 23_198_069_456
    assert qwen38.metadata["pilot"]["memory"]["eligible"] is True
    assert (
        qwen38_low_think4k.signals["local_peak_process_physical_footprint_bytes"].value
        == 23_782_290_440
    )
    assert qwen38_low_think4k.metadata["pilot"]["memory"]["eligible"] is True
    assert muse.signals["local_peak_process_physical_footprint_bytes"].value == 3_484_714_192
    assert muse.metadata["pilot"]["memory"]["eligible"] is True

    expected_members = {
        "latency": [qwen38_low_think4k],
        "memory": [qwen38_low_think4k, muse],
    }
    for name, expected in expected_members.items():
        frontier = load_frontier_snapshot(
            EXAMPLE / "generated" / f"harbor-pilot5-quality-{name}-frontier.json"
        )
        assert [member.offering for member in frontier.members] == [
            offering.offering for offering in expected
        ]

    cache_frontier = load_frontier_snapshot(
        EXAMPLE / "generated" / "harbor-pilot5-quality-cache-efficiency-frontier.json"
    )
    assert [member.offering for member in cache_frontier.members] == [muse.offering]
