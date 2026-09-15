from __future__ import annotations

import json
import subprocess
import sys
import textwrap
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
NOW = datetime(2026, 9, 15, 13, 20, tzinfo=UTC)
FRONTIERS = (
    "asr-responsive-intelligibility",
    "asr-typical-intelligibility",
    "asr-batch-intelligibility",
    "asr-small-resident",
    "asr-restart-intelligibility",
    "asr-framework-hot-intelligibility",
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
        for frontier_id in (
            "asr-restart-intelligibility",
            "asr-framework-hot-intelligibility",
        ):
            assert _models(frontier_id, catalog_name) == {"parakeet-tdt-0.6b-v3"}


def test_asr_cuda_frontiers_select_the_optimized_parakeet_offering() -> None:
    expected = {
        "self-hosted/parakeet-tdt-06b-v3-bf16-compile-static16-transformers@rtx5060ti-16gb-en"
    }
    for frontier_id in FRONTIERS[:3]:
        assert _offering_ids(frontier_id, "asr-observations-5060.json") == expected
    assert _models("asr-small-resident", "asr-observations-5060.json") == set()


def test_asr_cuda_restart_frontier_keeps_real_activation_tradeoffs() -> None:
    assert _offering_ids("asr-restart-intelligibility", "asr-observations-5060.json") == {
        "self-hosted/whisper-large-v3-turbo-bf16-transformers@rtx5060ti-16gb-en",
        "self-hosted/parakeet-tdt-06b-v3-bf16-transformers@rtx5060ti-16gb-en",
        "self-hosted/qwen3-asr-17b-bf16-transformers@rtx5060ti-16gb-en",
        "self-hosted/parakeet-tdt-06b-v3-bf16-compile-static16-transformers@rtx5060ti-16gb-en",
    }


def test_asr_cuda_framework_hot_frontier_keeps_model_load_tradeoffs() -> None:
    assert _offering_ids("asr-framework-hot-intelligibility", "asr-observations-5060.json") == {
        "self-hosted/whisper-large-v3-turbo-bf16-transformers@rtx5060ti-16gb-en",
        "self-hosted/parakeet-tdt-06b-v3-bf16-transformers@rtx5060ti-16gb-en",
        "self-hosted/qwen3-asr-17b-bf16-transformers@rtx5060ti-16gb-en",
        "self-hosted/parakeet-tdt-06b-v3-bf16-compile-static16-transformers@rtx5060ti-16gb-en",
    }


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


def test_asr_activation_captures_are_complete_stable_and_path_scrubbed() -> None:
    captures = sorted((EXAMPLE / "raw").glob("*-asr-activation-v1.json"))
    assert len(captures) == 15
    for path in captures:
        raw = path.read_text(encoding="utf-8")
        capture = json.loads(raw)
        assert capture["summary"]["attempt_count"] == 10
        assert capture["summary"]["sample_count"] == 10
        assert capture["summary"]["failure_count"] == 0
        assert capture["summary"]["transcript_variants"] == 1
        assert capture["harness"]["python"] == "python"
        assert capture["harness"]["python_version"].startswith("Python 3.")
        assert capture["harness"]["wrapper"] == "probe_asr_activation.py"
        assert capture["harness"]["wrapper_version"] == "1"
        assert "/Users/" not in raw
        assert "/root/" not in raw
        assert "192.168." not in raw
        assert "id=" not in raw


def test_asr_framework_hot_captures_are_complete_stable_and_path_scrubbed() -> None:
    captures = sorted((EXAMPLE / "raw").glob("*-asr-framework-hot-v1.json"))
    assert len(captures) == 15
    for path in captures:
        raw = path.read_text(encoding="utf-8")
        capture = json.loads(raw)
        assert capture["summary"]["attempt_count"] == 10
        assert capture["summary"]["sample_count"] == 10
        assert capture["summary"]["failure_count"] == 0
        assert capture["summary"]["transcript_variants"] == 1
        assert capture["harness"]["python"] == "python"
        assert capture["harness"]["python_version"].startswith("Python 3.")
        assert capture["harness"]["wrapper"] == "probe_asr_framework_hot.py"
        assert capture["harness"]["launcher"] == "asr_framework_hot_child.py"
        assert capture["harness"]["wrapper_version"] == "1"
        assert capture["harness"]["launcher_version"] == "1"
        assert all(
            "launch_to_first_transcript_seconds" not in measurement
            for measurement in capture["measurements"]
        )
        assert "/Users/" not in raw
        assert "/root/" not in raw
        assert "192.168." not in raw
        assert "id=" not in raw


def test_m5_activation_captures_record_high_power_without_thermal_warning() -> None:
    for filename in (
        "parakeet-tdt-06b-v3-fp16-mlx-m5-asr-activation-v1.json",
        "parakeet-tdt-06b-v3-fp16-mlx-m5-asr-framework-hot-v1.json",
    ):
        capture = json.loads((EXAMPLE / "raw" / filename).read_text(encoding="utf-8"))
        before = capture["host_state_before"]
        assert before["power"]["source"] == "AC Power"
        assert before["power"]["ac_power_powermode"] == 2
        assert "No thermal warning" in before["thermal"]


def test_asr_panel_manifest_commits_metadata_but_not_audio() -> None:
    manifest = EXAMPLE / "prompts" / "local-asr-pilot-v1.json"
    assert manifest.exists()
    assert not list(EXAMPLE.rglob("*.wav"))


def test_asr_activation_probe_uses_fresh_child_processes(tmp_path: Path) -> None:
    fake_child = tmp_path / "fake_asr_child.py"
    fake_child.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import struct
            import sys
            from pathlib import Path

            output = Path(sys.argv[sys.argv.index("--output") + 1])
            payload = {
                "schema": "model-skyline/experimental-local-asr-capture/v1alpha1",
                "offering": {
                    "model": "example/asr",
                    "resolved_revision": "abc123",
                    "runtime": "fake-runtime",
                    "hardware": "fake-hardware",
                    "os": "fake-os",
                    "startup_probe": True,
                },
                "workload": {
                    "manifest_id": "local-asr-pilot-v1",
                    "manifest_version": "1.0.0",
                    "manifest_sha256": "manifest-sha",
                    "audio_set_sha256": "audio-sha",
                    "panel_sample_count": 24,
                },
                "model_load_seconds": 0.001,
                "measurements": [{
                    "testcase_id": "fixed-probe",
                    "audio_sha256": "clip-sha",
                    "hypothesis": "stable transcript",
                    "normalized_hypothesis": "stable transcript",
                    "final_latency_ms": 2.0,
                    "errors": 0,
                    "reference_words": 2,
                }],
                "summary": {"sample_count": 1},
            }
            transcript = payload["measurements"][0]["hypothesis"].encode("utf-8")
            fd = int(os.environ["MODEL_SKYLINE_STARTUP_READY_FD"])
            os.write(fd, struct.pack("!Q", len(transcript)) + transcript)
            os.close(fd)
            output.write_text(json.dumps(payload))
            """
        ),
        encoding="utf-8",
    )
    output = tmp_path / "activation.json"
    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE / "probe_asr_activation.py"),
            "--backend",
            "transformers",
            "--python",
            sys.executable,
            "--benchmark-script",
            str(fake_child),
            "--runs",
            "3",
            "--seed-runs",
            "1",
            "--compiler-cache",
            "seeded-isolated",
            "--output",
            str(output),
            "--",
            "--model",
            "example/asr",
            "--audio-dir",
            str(tmp_path / "audio"),
            "--prompt-manifest",
            str(tmp_path / "manifest.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    capture = json.loads(output.read_text(encoding="utf-8"))
    assert capture["schema"].endswith("asr-activation-capture/v1alpha1")
    assert capture["methodology"]["compiler_cache"] == "seeded-isolated"
    assert len(capture["seed_launch_to_first_transcript_seconds"]) == 1
    assert len(capture["measurements"]) == 3
    assert capture["summary"]["sample_count"] == 3
    assert capture["summary"]["attempt_count"] == 3
    assert capture["summary"]["failure_percent"] == 0
    assert capture["summary"]["transcript_variants"] == 1
    assert capture["offering"]["resolved_revision"] == "abc123"
    assert capture["harness"]["wrapper"] == "probe_asr_activation.py"
    assert capture["harness"]["python_version"].startswith("Python 3.")
    assert capture["harness"]["benchmark_arguments"] == [
        "--model",
        "example/asr",
        "--audio-dir",
        "<audio-dir>",
        "--prompt-manifest",
        "<prompt-manifest>",
    ]


def test_asr_framework_hot_probe_starts_clock_after_framework_ready(tmp_path: Path) -> None:
    fake_child = tmp_path / "fake_framework_hot_asr.py"
    fake_child.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import struct
            import sys
            import time
            from pathlib import Path

            time.sleep(0.05)

            class FakeMLX:
                @staticmethod
                def zeros(shape):
                    return [0] * shape[0]

                @staticmethod
                def eval(value):
                    return value

                @staticmethod
                def synchronize():
                    return None

                @staticmethod
                def clear_cache():
                    return None

            mx = FakeMLX()

            def main():
                output = Path(sys.argv[sys.argv.index("--output") + 1])
                payload = {
                    "schema": "model-skyline/experimental-local-asr-capture/v1alpha1",
                    "offering": {
                        "model": "example/asr",
                        "resolved_revision": "abc123",
                        "runtime": "fake-runtime",
                        "hardware": "fake-hardware",
                        "os": "fake-os",
                        "startup_probe": True,
                    },
                    "workload": {
                        "manifest_id": "local-asr-pilot-v1",
                        "manifest_version": "1.0.0",
                        "manifest_sha256": "manifest-sha",
                        "audio_set_sha256": "audio-sha",
                        "panel_sample_count": 24,
                    },
                    "model_load_seconds": 0.001,
                    "measurements": [{
                        "testcase_id": "fixed-probe",
                        "audio_sha256": "clip-sha",
                        "hypothesis": "stable transcript",
                        "normalized_hypothesis": "stable transcript",
                        "final_latency_ms": 2.0,
                        "errors": 0,
                        "reference_words": 2,
                    }],
                    "summary": {"sample_count": 1},
                }
                transcript = payload["measurements"][0]["hypothesis"].encode("utf-8")
                fd = int(os.environ["MODEL_SKYLINE_STARTUP_READY_FD"])
                os.write(fd, struct.pack("!Q", len(transcript)) + transcript)
                os.close(fd)
                output.write_text(json.dumps(payload))
            """
        ),
        encoding="utf-8",
    )
    output = tmp_path / "framework-hot.json"
    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE / "probe_asr_framework_hot.py"),
            "--backend",
            "mlx",
            "--python",
            sys.executable,
            "--launcher",
            str(EXAMPLE / "asr_framework_hot_child.py"),
            "--benchmark-script",
            str(fake_child),
            "--runs",
            "3",
            "--seed-runs",
            "1",
            "--compiler-cache",
            "system-default",
            "--output",
            str(output),
            "--",
            "--model",
            "example/asr",
            "--audio-dir",
            str(tmp_path / "audio"),
            "--prompt-manifest",
            str(tmp_path / "manifest.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    capture = json.loads(output.read_text(encoding="utf-8"))
    assert capture["schema"].endswith("asr-framework-hot-capture/v1alpha1")
    assert capture["summary"]["sample_count"] == 3
    assert capture["summary"]["failure_count"] == 0
    assert capture["summary"]["framework_prepare_p50_seconds"] >= 0.05
    assert capture["summary"]["framework_hot_activation_p50_seconds"] < 0.05
    assert all(
        "launch_to_first_transcript_seconds" not in measurement
        for measurement in capture["measurements"]
    )
    assert capture["harness"]["launcher"] == "asr_framework_hot_child.py"
