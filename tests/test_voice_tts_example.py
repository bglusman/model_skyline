from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
BENCHMARK = ROOT / "examples" / "voice-runtime-frontiers" / "bench_openai_tts.py"
EXAMPLE = ROOT / "examples" / "voice-runtime-frontiers"
RAW = EXAMPLE / "raw"
AGGREGATOR = EXAMPLE / "aggregate_tts_seed_panel.py"
NARI_STEM = "qwen3-tts-1.7b-bf16-nari-router-5060-seed1234"
ROUTER = ROOT / "examples" / "local-runtime-frontiers" / "inference-vm-router"


def test_openai_tts_benchmark_can_omit_optional_request_fields(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []
    pcm = struct.pack("<h", 2_000) * 4_800

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(json.loads(body))
            self.send_response(200)
            self.send_header("Content-Type", "audio/pcm")
            self.send_header("Content-Length", str(len(pcm)))
            self.end_headers()
            self.wfile.write(pcm)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "capture.json"
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(BENCHMARK),
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--model",
                "example/tts",
                "--endpoint-label",
                "http://router.invalid/v1/audio/speech",
                "--runtime-label",
                "strict-test-server",
                "--server-hardware",
                "test-host",
                "--repetitions",
                "2",
                "--stream-format",
                "omit",
                "--no-non-streaming-mode",
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert len(requests) == 2
    assert all("stream_format" not in request for request in requests)
    assert all(request["non_streaming_mode"] is False for request in requests)
    capture = json.loads(output.read_text(encoding="utf-8"))
    offering = capture["offering"]
    assert offering["endpoint"] == "http://router.invalid/v1/audio/speech"
    assert offering["request_stream_format"] is None
    assert offering["request_non_streaming_mode"] is False
    assert capture["harness"] == {
        "script": "bench_openai_tts.py",
        "sha256": hashlib.sha256(BENCHMARK.read_bytes()).hexdigest(),
    }


def test_nari_router_capture_is_exact_bound_and_on_the_latency_frontiers() -> None:
    latency_path = RAW / f"{NARI_STEM}-coval-tts-v1.json"
    quality_path = RAW / f"{NARI_STEM}-coval-tts-v1-wer.json"
    completion_path = RAW / f"{NARI_STEM}-coval-tts-v1-completion.json"
    latency = json.loads(latency_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    completion = json.loads(completion_path.read_text(encoding="utf-8"))

    assert latency["harness"]["sha256"] == hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    assert latency["offering"]["model_revision"] == ("0c0e3051f131929182e2c023b9537f8b1c68adfe")
    assert latency["offering"]["endpoint"] == ("http://llama-swap.local:8090/v1/audio/speech")
    assert latency["resident_summary_excluding_first_request"] == {
        "coval_ttfa_p50_ms": 48.772,
        "coval_ttfa_p95_ms": 82.865,
        "playback_ttfa_p50_ms": 48.772,
        "playback_ttfa_p95_ms": 82.865,
        "pre_audible_buffer_delay_case_count": 0,
        "pre_audible_buffer_delay_p95_ms": 0.0,
        "real_time_factor_p05": 4.917,
        "real_time_factor_p50": 4.971,
        "sample_count": 30,
    }
    assert (
        quality["source"]["latency_capture_sha256"]
        == hashlib.sha256(latency_path.read_bytes()).hexdigest()
    )
    assert quality["summary"]["corpus_wer_percentage"] == 10.03861
    assert (
        completion["source"]["wer_capture_sha256"]
        == hashlib.sha256(quality_path.read_bytes()).hexdigest()
    )
    assert completion["summary"]["diagnostic_warning_count"] == 0

    result = subprocess.run(
        [sys.executable, str(EXAMPLE / "build_observations.py"), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    catalog = json.loads((EXAMPLE / "observations.json").read_text(encoding="utf-8"))
    assert len(catalog["offerings"]) == 5
    nari_id = "self-hosted/qwen3-tts-1.7b-bf16-nari-consumer@rtx5060ti-16gb-vivian-en"
    nari = next(item for item in catalog["offerings"] if item["offering"]["offering_id"] == nari_id)
    assert nari["signals"]["tts_playback_ttfa_p95_ms"]["value"] == "57.053"
    assert nari["signals"]["tts_corpus_wer_percent"]["sample_count"] == 90
    for frontier_id in (
        "tts-responsive-intelligibility",
        "tts-typical-intelligibility",
        "tts-batch-intelligibility",
    ):
        snapshot = json.loads(
            (EXAMPLE / "generated" / f"{frontier_id}.json").read_text(encoding="utf-8")
        )
        assert nari_id in [item["offering"]["offering_id"] for item in snapshot["members"]]

    for path in RAW.glob(f"{NARI_STEM}-*.json"):
        serialized = path.read_text(encoding="utf-8")
        assert "192.168." not in serialized
        assert "/Users/" not in serialized
        assert "/root/" not in serialized


def test_tts_seed_panel_replays_raw_hashes_and_pooled_metrics() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(AGGREGATOR),
            "--panel",
            str(EXAMPLE / "tts-seed-panel.json"),
            "--output",
            str(RAW / "tts-seed-panel-v1-results.json"),
            "--check",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    panel = json.loads((RAW / "tts-seed-panel-v1-results.json").read_text())
    assert panel["panel"]["seeds"] == [7, 1234, 2026]
    assert panel["panel"]["samples_per_offering"] == 90
    offerings = {item["slug"]: item for item in panel["offerings"]}
    assert set(offerings) == {
        "qwen3-tts-1.7b-6bit-mlx-m5",
        "qwen3-tts-1.7b-bf16-vllm-omni-5060",
        "qwen3-tts-1.7b-bf16-nari-router-5060",
        "loudr-1-turbo-loudkit-m5",
        "loudr-1-turbo-loudkit-5060",
    }
    assert offerings["qwen3-tts-1.7b-bf16-nari-router-5060"]["summary"] == {
        "corpus_wer_percentage": 10.03861,
        "corpus_wer_percentage_lower": 6.662206,
        "corpus_wer_percentage_upper": 14.061668,
        "corpus_words_per_minute": 113.777387,
        "internal_pause_fraction_p50": 0.157659,
        "invalid_case_count": 0,
        "invalid_case_percent": 0.0,
        "playback_ttfa_p50_ms": 47.249,
        "playback_ttfa_p95_ms": 57.053,
        "real_time_factor_p50": 4.971,
        "sample_count": 90,
        "seed_count": 3,
        "total_errors": 156,
        "total_reference_words": 1554,
        "words_per_minute_p50": 119.556131,
        "words_per_minute_p95": 165.235437,
    }
    assert offerings["qwen3-tts-1.7b-6bit-mlx-m5"]["invalid_cases"] == [
        {"seed": 7, "testcase_id": "A9"}
    ]
    for offering in offerings.values():
        assert [run["seed"] for run in offering["source_runs"]] == [7, 1234, 2026]
        for run in offering["source_runs"]:
            for source in run["sources"].values():
                source_path = EXAMPLE / source["path"]
                assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source["sha256"]
                raw = source_path.read_text(encoding="utf-8")
                assert "192.168." not in raw
                assert "/Users/" not in raw
                assert "/root/" not in raw


def test_tts_seed_panel_frontiers_have_two_exact_and_one_model_winner() -> None:
    expected_exact = {
        "self-hosted/qwen3-tts-1.7b-bf16-nari-consumer@rtx5060ti-16gb-vivian-en",
        "self-hosted/qwen3-tts-1.7b-bf16-vllm-omni@rtx5060ti-16gb-vivian-en",
    }
    for frontier_id in (
        "tts-responsive-intelligibility",
        "tts-typical-intelligibility",
        "tts-batch-intelligibility",
    ):
        snapshot = json.loads(
            (EXAMPLE / "generated" / f"{frontier_id}.json").read_text(encoding="utf-8")
        )
        assert {item["offering"]["offering_id"] for item in snapshot["members"]} == expected_exact
        assert len(snapshot["rejected"]) == 3
        view = json.loads(
            (EXAMPLE / "generated" / f"{frontier_id}-model-view.json").read_text(encoding="utf-8")
        )
        assert [item["model_id"] for item in view["best_available"]["members"]] == [
            "Qwen3-TTS-12Hz-1.7B-CustomVoice"
        ]
        assert view["balanced_average"] is None


def test_nari_long_form_capture_has_no_deterministic_warning() -> None:
    latency_path = RAW / f"{NARI_STEM}-speaker-consistency-v1.json"
    quality_path = RAW / f"{NARI_STEM}-speaker-consistency-v1-wer.json"
    completion_path = RAW / f"{NARI_STEM}-speaker-consistency-v1-completion.json"
    diarization_path = RAW / f"{NARI_STEM}-speaker-consistency-v1-diarization.json"
    latency = json.loads(latency_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    diarization = json.loads(diarization_path.read_text(encoding="utf-8"))

    latency_digest = hashlib.sha256(latency_path.read_bytes()).hexdigest()
    quality_digest = hashlib.sha256(quality_path.read_bytes()).hexdigest()
    assert latency["offering"]["model_revision"] == ("0c0e3051f131929182e2c023b9537f8b1c68adfe")
    assert quality["source"]["latency_capture_sha256"] == latency_digest
    assert quality["summary"]["corpus_wer_percentage"] == 1.324503
    assert completion["source"]["wer_capture_sha256"] == quality_digest
    assert completion["summary"]["diagnostic_warning_count"] == 0
    assert diarization["source"]["latency_capture_sha256"] == latency_digest
    assert diarization["summary"]["cases_with_unexpected_speaker"] == 0


def test_nari_router_launcher_and_compatibility_patch_are_pinned() -> None:
    launcher = ROUTER / "nari-qwen3-tts-consumer"
    patch = ROUTER / "nari-qwen3-tts-consumer.patch"
    config = (ROUTER / "config.yaml").read_text(encoding="utf-8")
    launcher_text = launcher.read_text(encoding="utf-8")

    syntax = subprocess.run(
        ["sh", "-n", str(launcher)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert launcher.stat().st_mode & 0o100
    assert "/local-model-exclusive" in launcher_text
    assert "0c0e3051f131929182e2c023b9537f8b1c68adfe" in launcher_text
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == (
        "95f3c610b33ea4fad1d059d041ec898bf63497651efd0302fce708bcd54199ea"
    )
    assert "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice:" in config
    assert "checkEndpoint: /ready" in config
