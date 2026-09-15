#!/usr/bin/env python3
"""Build the experimental TTS observation catalog from retained raw captures."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
WORKLOAD = {
    "id": "coval-tts-v1-local-whisper-seed-panel",
    "version": "1.2.0+local-whisper-seed-panel.1",
    "unit": "utterance",
}
SEED_PANEL = RAW / "tts-seed-panel-v1-results.json"
SERVICE_MEMORY_PANEL = RAW / "tts-service-memory-panel-v1-results.json"

SPECS: tuple[dict[str, Any], ...] = (
    {
        "slug": "qwen3-tts-1.7b-6bit-mlx-m5",
        "model_id": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "endpoint": "in-process-mlx",
        "quantization": "6bit",
        "hardware": "Apple M5 Max 64 GB",
        "artifact": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit",
        "artifact_revision": "1c6c0ff58c43afa8df571facde2efa077efd85e2",
        "revision_provenance": "capture-time declaration and resolved snapshot",
        "runtime": "mlx-audio 0.5.4 / MLX 0.32.2",
        "runtime_config": "streaming_interval=0.32s; guarded_1920_sample_bootstrap_suppression",
        "voice": "Vivian; English",
    },
    {
        "slug": "qwen3-tts-1.7b-bf16-vllm-omni-5060",
        "model_id": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "endpoint": "openai-compatible-pcm-http",
        "quantization": "bf16",
        "hardware": "NVIDIA GeForce RTX 5060 Ti 16 GB",
        "artifact": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "artifact_revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe",
        "revision_provenance": "capture-time declaration and server cache inspection",
        "runtime": "vLLM-Omni 0.26.0",
        "runtime_config": (
            "omni pipeline; max_model_len=2048; VLLM_USE_FLASHINFER_SAMPLER=0; "
            "guarded_1920_sample_bootstrap_suppression"
        ),
        "voice": "Vivian; English",
        "capture_harness_sha256": (
            "005924052023900169d263a2dcc534ef7d8c1ec32975f0ed30400cf6c4e6b8ff"
        ),
    },
    {
        "slug": "qwen3-tts-1.7b-bf16-nari-router-5060",
        "model_id": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "endpoint": "llama-swap-openai-compatible-pcm-http",
        "quantization": "bf16",
        "hardware": "NVIDIA GeForce RTX 5060 Ti 16 GB",
        "artifact": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "artifact_revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe",
        "revision_provenance": "capture-time declaration and server cache inspection",
        "runtime": ("llama-swap 255 / nari-qwen3-tts @ e8c5b2bf6d65a965037f161d713bfa3e8856feb3"),
        "runtime_config": (
            "consumer patch sha256=95f3c610b33ea4fad1d059d041ec898bf63497651efd0302"
            "fce708bcd54199ea; batch=1; BF16 talker; kv_pages=32; "
            "workspace=128MiB; max_active_requests=1; server bootstrap suppression; "
            "llama-swap exclusive local-memory group"
        ),
        "capture_harness_sha256": (
            "005924052023900169d263a2dcc534ef7d8c1ec32975f0ed30400cf6c4e6b8ff"
        ),
        "voice": "Vivian; English",
    },
    {
        "slug": "loudr-1-turbo-loudkit-m5",
        "model_id": "loudr-1-turbo",
        "endpoint": "in-process-loudkit",
        "quantization": "mixed-fp16-fp32",
        "hardware": "Apple M5 Max 64 GB",
        "artifact": "loudreader/loudr-1-turbo",
        "artifact_revision": (
            "checkpoint-sha256:590dcf9e1dcec31c54650ee139f345b248bdd7ba11288def664c5f1508f33593"
        ),
        "revision_provenance": "capture-time checkpoint digest",
        "runtime": "loudkit 0.1.1 @ 7a17fa2351e59978b34d5aac07bae6349a7ca5d9",
        "runtime_config": "generator=cpu; renderer=mps; deterministic",
        "voice": "joe; English",
    },
    {
        "slug": "loudr-1-turbo-loudkit-5060",
        "model_id": "loudr-1-turbo",
        "endpoint": "in-process-loudkit",
        "quantization": "mixed-fp16-fp32",
        "hardware": "NVIDIA GeForce RTX 5060 Ti 16 GB",
        "artifact": "loudreader/loudr-1-turbo",
        "artifact_revision": (
            "checkpoint-sha256:590dcf9e1dcec31c54650ee139f345b248bdd7ba11288def664c5f1508f33593"
        ),
        "revision_provenance": "capture-time checkpoint digest",
        "runtime": "loudkit 0.1.1 @ 7a17fa2351e59978b34d5aac07bae6349a7ca5d9",
        "runtime_config": "cuda; sdpa; deterministic ragged vocoder",
        "voice": "joe; English",
    },
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _source(slug: str, kind: str, path: Path, methodology: str) -> dict[str, Any]:
    return {
        "id": f"voice-seed-panel-{slug}-{kind}",
        "version": "1",
        "methodology": methodology,
        "raw_sha256": _sha256(path),
    }


def _observation(
    value: Any,
    unit: str,
    observed_at: str,
    source: dict[str, Any],
    *,
    sample_count: int,
    lower: str | None = None,
    upper: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": _decimal(value),
        "unit": unit,
        "sample_count": sample_count,
        "observed_at": observed_at,
        "source": source,
    }
    if lower is not None:
        result["lower"] = lower
    if upper is not None:
        result["upper"] = upper
    return result


def _verify_identity(
    spec: dict[str, Any],
    panel_offering: dict[str, Any],
) -> None:
    identity = panel_offering["offering_identity_without_seed"]
    if identity.get("model") != spec["artifact"]:
        raise ValueError(f"{spec['slug']} has an unexpected model artifact")
    revision = spec["artifact_revision"]
    if "resolved_revision" in identity and identity["resolved_revision"] != revision:
        raise ValueError(f"{spec['slug']} has an unexpected resolved revision")
    if "model_revision" in identity and identity["model_revision"] != revision:
        raise ValueError(f"{spec['slug']} has an unexpected model revision")
    if "checkpoint_sha256" in identity:
        expected_checkpoint = revision.removeprefix("checkpoint-sha256:")
        if identity["checkpoint_sha256"] != expected_checkpoint:
            raise ValueError(f"{spec['slug']} has an unexpected checkpoint digest")
    capture_harness_sha256 = spec.get("capture_harness_sha256")
    if capture_harness_sha256 is not None:
        for run in panel_offering["source_runs"]:
            path = HERE / run["sources"]["latency"]["path"]
            if _load(path).get("harness", {}).get("sha256") != capture_harness_sha256:
                raise ValueError(f"unexpected capture harness in {path.name}")


def _observed_at(panel_offering: dict[str, Any], kind: str) -> str:
    values = [
        run["sources"][kind]["captured_at"]
        for run in panel_offering["source_runs"]
        if isinstance(run["sources"][kind].get("captured_at"), str)
    ]
    if not values:
        raise ValueError(f"{panel_offering['slug']} has no {kind} capture time")
    return max(values)


def _offering(
    spec: dict[str, Any],
    panel_offering: dict[str, Any],
    memory_offering: dict[str, Any],
) -> dict[str, Any]:
    if panel_offering.get("slug") != spec["slug"]:
        raise ValueError(f"seed-panel slug mismatch for {spec['slug']}")
    _verify_identity(spec, panel_offering)
    summary = panel_offering["summary"]
    sample_count = int(summary["sample_count"])
    if sample_count != 90 or summary["seed_count"] != 3:
        raise ValueError(f"{spec['slug']} is not a complete 3 x 30 seed panel")

    latency_source = _source(
        spec["slug"],
        "latency",
        SEED_PANEL,
        (
            "Ninety resident synthesis measurements pooled from 30 matched prompts "
            "at each of three seeds; percentiles are calculated from utterances, "
            "not from per-seed medians."
        ),
    )
    quality_source = _source(
        spec["slug"],
        "wer",
        SEED_PANEL,
        (
            "Saved audio scored by pinned local MLX Whisper with CoVAL "
            "normalization v2; bounds are a descriptive two-stage bootstrap "
            "over seeds and prompts."
        ),
    )
    pacing_source = _source(
        spec["slug"],
        "pacing",
        SEED_PANEL,
        (
            "Deterministic reference-word rate and energy-pause proxies from the same "
            "saved audio; no naturalness threshold is asserted."
        ),
    )
    capture_harness_sha256 = spec.get("capture_harness_sha256")
    seeds = [run["seed"] for run in panel_offering["source_runs"]]
    if memory_offering.get("slug") != spec["slug"]:
        raise ValueError(f"service-memory slug mismatch for {spec['slug']}")
    if memory_offering.get("offering_id") != panel_offering["offering_id"]:
        raise ValueError(f"service-memory offering ID mismatch for {spec['slug']}")
    memory_summary = memory_offering["summary"]
    memory_source = _source(
        spec["slug"],
        "service-memory",
        SERVICE_MEMORY_PANEL,
        (
            "One continuous whole-service capture spanning cold load and three matched "
            "30-prompt runs. Apple rows use process-tree physical footprint. CUDA rows "
            "use same-sample host PSS plus device allocation; component peaks remain separate."
        ),
    )
    memory_observed_at = memory_offering["memory_capture"]["captured_at"]
    memory_metadata = {
        "memory_architecture": memory_offering["memory_architecture"],
        "memory_accounting": memory_offering["instrument"]["combined_measure"],
        "memory_axis_warning": (
            "The combined split-memory value compares resource efficiency; fit still requires "
            "checking the independent host and device peaks."
        ),
        "peak_host_physical_bytes": memory_summary["peak_host_physical_bytes"],
        "peak_device_memory_bytes": memory_summary["peak_device_memory_bytes"],
        "host_bytes_at_combined_peak": memory_summary["host_bytes_at_combined_peak"],
        "device_bytes_at_combined_peak": memory_summary["device_bytes_at_combined_peak"],
        "memory_capture_sample_count": memory_summary["sample_count"],
        "memory_capture": memory_offering["memory_capture"]["path"],
        "memory_capture_sha256": memory_offering["memory_capture"]["sha256"],
        "memory_capture_isolation": memory_offering["isolation"],
    }
    memory_signals = {
        "tts_peak_service_capacity_bytes": _observation(
            memory_summary["peak_combined_capacity_bytes"],
            "bytes",
            memory_observed_at,
            memory_source,
            sample_count=1,
        ),
        "tts_peak_host_physical_bytes": _observation(
            memory_summary["peak_host_physical_bytes"],
            "bytes",
            memory_observed_at,
            memory_source,
            sample_count=1,
        ),
    }
    if memory_summary["peak_device_memory_bytes"] is not None:
        memory_signals["tts_peak_device_memory_bytes"] = _observation(
            memory_summary["peak_device_memory_bytes"],
            "bytes",
            memory_observed_at,
            memory_source,
            sample_count=1,
        )
    return {
        "offering": {
            "offering_id": panel_offering["offering_id"],
            "model_id": spec["model_id"],
            "provider": "self-hosted",
            "endpoint": spec["endpoint"],
            "service_tier": "resident",
            "quantization": spec["quantization"],
            "agent_harness": "model-skyline-voice-pilot@1",
            "capabilities": ["audio", "streaming", "tts"],
        },
        "metadata": {
            "hardware": spec["hardware"],
            "artifact": spec["artifact"],
            "artifact_revision": spec["artifact_revision"],
            "artifact_revision_provenance": spec["revision_provenance"],
            "runtime": spec["runtime"],
            "runtime_config": spec["runtime_config"],
            "voice": spec["voice"],
            "synthesis_seeds": seeds,
            "quality_scope": "local Whisper corpus WER; not naturalness or speaker identity",
            "evidence_status": "provisional three-seed panel",
            **(
                {"capture_harness_sha256": capture_harness_sha256}
                if capture_harness_sha256 is not None
                else {}
            ),
            "seed_panel_definition": "tts-seed-panel.json",
            "seed_panel_capture": f"raw/{SEED_PANEL.name}",
            "source_run_count": len(panel_offering["source_runs"]),
            "audio_set_sha256s": [run["audio_set_sha256"] for run in panel_offering["source_runs"]],
            **memory_metadata,
        },
        "signals": {
            "tts_corpus_wer_percent": _observation(
                summary["corpus_wer_percentage"],
                "percent",
                _observed_at(panel_offering, "quality"),
                quality_source,
                sample_count=sample_count,
                lower=_decimal(summary["corpus_wer_percentage_lower"]),
                upper=_decimal(summary["corpus_wer_percentage_upper"]),
            ),
            "tts_playback_ttfa_p50_ms": _observation(
                summary["playback_ttfa_p50_ms"],
                "milliseconds",
                _observed_at(panel_offering, "latency"),
                latency_source,
                sample_count=sample_count,
            ),
            "tts_playback_ttfa_p95_ms": _observation(
                summary["playback_ttfa_p95_ms"],
                "milliseconds",
                _observed_at(panel_offering, "latency"),
                latency_source,
                sample_count=sample_count,
            ),
            "tts_realtime_factor_p50": _observation(
                summary["real_time_factor_p50"],
                "ratio",
                _observed_at(panel_offering, "latency"),
                latency_source,
                sample_count=sample_count,
            ),
            "tts_invalid_case_percent": _observation(
                summary["invalid_case_percent"],
                "percent",
                max(
                    _observed_at(panel_offering, "latency"),
                    _observed_at(panel_offering, "quality"),
                    _observed_at(panel_offering, "completion"),
                ),
                latency_source,
                sample_count=sample_count,
            ),
            "tts_corpus_words_per_minute": _observation(
                summary["corpus_words_per_minute"],
                "words/minute",
                _observed_at(panel_offering, "pacing"),
                pacing_source,
                sample_count=sample_count,
            ),
            "tts_words_per_minute_p95": _observation(
                summary["words_per_minute_p95"],
                "words/minute",
                _observed_at(panel_offering, "pacing"),
                pacing_source,
                sample_count=sample_count,
            ),
            "tts_internal_pause_fraction_p50": _observation(
                summary["internal_pause_fraction_p50"],
                "ratio",
                _observed_at(panel_offering, "pacing"),
                pacing_source,
                sample_count=sample_count,
            ),
            **memory_signals,
        },
    }


def _render() -> str:
    panel = _load(SEED_PANEL)
    memory_panel = _load(SERVICE_MEMORY_PANEL)
    if memory_panel.get("panel", {}).get("quality_panel_sha256") != _sha256(SEED_PANEL):
        raise ValueError("service-memory panel does not bind the current quality panel")
    panel_offerings = {offering["slug"]: offering for offering in panel.get("offerings", [])}
    memory_offerings = {
        offering["slug"]: offering for offering in memory_panel.get("offerings", [])
    }
    expected_slugs = {spec["slug"] for spec in SPECS}
    if set(panel_offerings) != expected_slugs:
        raise ValueError("seed-panel offerings do not match catalog specifications")
    if set(memory_offerings) != expected_slugs:
        raise ValueError("service-memory offerings do not match catalog specifications")
    catalog = {
        "schema_version": "model-skyline/v1alpha1",
        "workload": WORKLOAD,
        "offerings": [
            _offering(
                spec,
                panel_offerings[spec["slug"]],
                memory_offerings[spec["slug"]],
            )
            for spec in SPECS
        ],
    }
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "observations.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if --output is not byte-identical to the generated catalog.",
    )
    args = parser.parse_args()
    rendered = _render()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated catalog: {args.output}")
        print(f"valid generated catalog: {args.output}")
        return
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
