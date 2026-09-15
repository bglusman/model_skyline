from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from model_skyline.engine import FrontierEngine
from model_skyline.io import (
    load_catalog,
    load_config,
    load_frontier_snapshot,
    load_model_frontier_view_snapshot,
)

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "voice-runtime-frontiers"
GENERATED = EXAMPLE / "generated"
NOW = datetime(2026, 9, 15, 4, tzinfo=UTC)
FRONTIERS = (
    "asr-responsive-intelligibility",
    "asr-typical-intelligibility",
    "asr-batch-intelligibility",
    "asr-small-resident",
)


def _models(frontier_id: str, catalog_name: str) -> set[str]:
    snapshot = FrontierEngine().calculate(
        load_config(EXAMPLE / "asr-frontier.yaml"),
        load_catalog(EXAMPLE / catalog_name),
        frontier_id,
        generated_at=NOW,
    )
    return {member.offering.model_id for member in snapshot.members}


def _offering_ids(frontier_id: str, catalog_name: str) -> set[str]:
    snapshot = FrontierEngine().calculate(
        load_config(EXAMPLE / "asr-frontier.yaml"),
        load_catalog(EXAMPLE / catalog_name),
        frontier_id,
        generated_at=NOW,
    )
    return {member.offering.offering_id for member in snapshot.members}


def test_asr_observation_catalogs_rebuild_from_bound_raw_captures() -> None:
    script = EXAMPLE / "build_asr_observations.py"
    for hardware, filename in (
        ("all", "asr-observations.json"),
        ("m1", "asr-observations-m1.json"),
        ("m5", "asr-observations-m5.json"),
        ("5060", "asr-observations-5060.json"),
    ):
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--hardware",
                hardware,
                "--output",
                str(EXAMPLE / filename),
                "--check",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_asr_frontier_residents_are_the_same_on_both_macs() -> None:
    speed_frontiers = FRONTIERS[:3]
    for catalog_name in ("asr-observations-m1.json", "asr-observations-m5.json"):
        for frontier_id in speed_frontiers:
            assert _models(frontier_id, catalog_name) == {"parakeet-tdt-0.6b-v3"}
        assert _models("asr-small-resident", catalog_name) == {
            "Qwen3-ASR-0.6B",
            "parakeet-tdt-0.6b-v3",
        }


def test_asr_cuda_frontiers_select_the_optimized_parakeet_offering() -> None:
    expected = {
        "self-hosted/parakeet-tdt-06b-v3-bf16-compile-static16-transformers@rtx5060ti-16gb-en"
    }
    for frontier_id in FRONTIERS[:3]:
        assert _offering_ids(frontier_id, "asr-observations-5060.json") == expected
    assert _models("asr-small-resident", "asr-observations-5060.json") == set()


def test_asr_generated_model_views_agree_for_best_and_balanced_reductions() -> None:
    for frontier_id in FRONTIERS:
        exact = load_frontier_snapshot(GENERATED / f"{frontier_id}.json")
        view = load_model_frontier_view_snapshot(GENERATED / f"{frontier_id}-model-view.json")
        assert view.source_snapshot_id == exact.snapshot_id
        best = {member.model_id for member in view.best_available.members}
        assert view.balanced_average is not None
        balanced = {member.model_id for member in view.balanced_average.members}
        assert best == balanced
        if frontier_id == "asr-small-resident":
            assert best == {"Qwen3-ASR-0.6B", "parakeet-tdt-0.6b-v3"}
        else:
            assert best == {"parakeet-tdt-0.6b-v3"}


def test_asr_compiled_cuda_repeats_are_transcript_stable() -> None:
    pairs = (
        (
            "parakeet-tdt-06b-v3-bf16-transformers-compile-static16-warm10-"
            "5060-local-asr-pilot-v1.json",
            "parakeet-tdt-06b-v3-bf16-transformers-compile-static16-warm10-repeat2-"
            "5060-local-asr-pilot-v1.json",
        ),
        (
            "qwen3-asr-06b-bf16-transformers-compile-dynamic-warm3-5060-local-asr-pilot-v1.json",
            "qwen3-asr-06b-bf16-transformers-compile-dynamic-warm3-repeat2-"
            "5060-local-asr-pilot-v1.json",
        ),
        (
            "qwen3-asr-17b-bf16-transformers-compile-dynamic-warm3-5060-local-asr-pilot-v1.json",
            "qwen3-asr-17b-bf16-transformers-compile-dynamic-warm3-repeat2-"
            "5060-local-asr-pilot-v1.json",
        ),
    )
    for first_name, repeat_name in pairs:
        first = json.loads((EXAMPLE / "raw" / first_name).read_text())
        repeat = json.loads((EXAMPLE / "raw" / repeat_name).read_text())
        assert first["workload"] == repeat["workload"]
        assert (
            first["summary"]["corpus_wer_percentage"] == repeat["summary"]["corpus_wer_percentage"]
        )
        assert [item["hypothesis"] for item in first["measurements"]] == [
            item["hypothesis"] for item in repeat["measurements"]
        ]


def test_asr_panel_manifest_commits_metadata_but_not_audio() -> None:
    manifest = EXAMPLE / "prompts" / "local-asr-pilot-v1.json"
    assert manifest.exists()
    assert not list(EXAMPLE.rglob("*.wav"))
