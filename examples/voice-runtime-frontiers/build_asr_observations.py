#!/usr/bin/env python3
"""Build ASR observation catalogs from retained Mac and CUDA pilot captures."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
MANIFEST = HERE / "prompts" / "local-asr-pilot-v1.json"
WORKLOAD = {"id": "local-asr-pilot-v1", "version": "1.0.0", "unit": "utterance"}
SAMPLE_COUNT = 24

MLX_MODELS: tuple[dict[str, str], ...] = (
    {
        "slug": "qwen3-asr-06b-8bit",
        "artifact": "mlx-community/Qwen3-ASR-0.6B-8bit",
        "model_id": "Qwen3-ASR-0.6B",
        "revision": "89e96d92ba34aca20b3e29fb10cc284097d1219f",
        "quantization": "8bit",
    },
    {
        "slug": "qwen3-asr-17b-8bit",
        "artifact": "mlx-community/Qwen3-ASR-1.7B-8bit",
        "model_id": "Qwen3-ASR-1.7B",
        "revision": "a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
        "quantization": "8bit",
    },
    {
        "slug": "whisper-large-v3-turbo-fp16",
        "artifact": "mlx-community/whisper-large-v3-turbo-asr-fp16",
        "model_id": "whisper-large-v3-turbo",
        "revision": "624c19c9af5603fa73b83bce14d4aeea96156d18",
        "quantization": "fp16",
    },
    {
        "slug": "parakeet-tdt-06b-v3-fp16",
        "artifact": "mlx-community/parakeet-tdt-0.6b-v3",
        "model_id": "parakeet-tdt-0.6b-v3",
        "revision": "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15",
        "quantization": "fp16",
    },
)

CUDA_MODELS: tuple[dict[str, str], ...] = (
    {
        "slug": "qwen3-asr-06b-bf16",
        "filename": "qwen3-asr-06b-bf16-transformers-5060-local-asr-pilot-v1.json",
        "artifact": "Qwen/Qwen3-ASR-0.6B-hf",
        "model_id": "Qwen3-ASR-0.6B",
        "revision": "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
        "quantization": "bf16",
    },
    {
        "slug": "qwen3-asr-17b-bf16",
        "filename": "qwen3-asr-17b-bf16-transformers-5060-local-asr-pilot-v1.json",
        "artifact": "Qwen/Qwen3-ASR-1.7B-hf",
        "model_id": "Qwen3-ASR-1.7B",
        "revision": "bcd2b5b7f32b480ab5790554cfa8347f246a14f3",
        "quantization": "bf16",
    },
    {
        "slug": "whisper-large-v3-turbo-bf16",
        "filename": ("whisper-large-v3-turbo-bf16-transformers-5060-local-asr-pilot-v1.json"),
        "artifact": "openai/whisper-large-v3-turbo",
        "model_id": "whisper-large-v3-turbo",
        "revision": "41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
        "quantization": "bf16",
    },
    {
        "slug": "parakeet-tdt-06b-v3-bf16",
        "filename": ("parakeet-tdt-06b-v3-bf16-transformers-5060-local-asr-pilot-v1.json"),
        "artifact": "nvidia/parakeet-tdt-0.6b-v3",
        "model_id": "parakeet-tdt-0.6b-v3",
        "revision": "541d1f99c6b0c3cd0b11a95167540bb8edefd82b",
        "quantization": "bf16",
    },
    {
        "slug": "qwen3-asr-06b-bf16-compile-dynamic",
        "filename": (
            "qwen3-asr-06b-bf16-transformers-compile-dynamic-warm3-5060-local-asr-pilot-v1.json"
        ),
        "artifact": "Qwen/Qwen3-ASR-0.6B-hf",
        "model_id": "Qwen3-ASR-0.6B",
        "revision": "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
        "quantization": "bf16",
        "activation_filename": (
            "qwen3-asr-06b-bf16-transformers-compile-dynamic-5060-asr-activation-v1.json"
        ),
    },
    {
        "slug": "qwen3-asr-17b-bf16-compile-dynamic",
        "filename": (
            "qwen3-asr-17b-bf16-transformers-compile-dynamic-warm3-5060-local-asr-pilot-v1.json"
        ),
        "artifact": "Qwen/Qwen3-ASR-1.7B-hf",
        "model_id": "Qwen3-ASR-1.7B",
        "revision": "bcd2b5b7f32b480ab5790554cfa8347f246a14f3",
        "quantization": "bf16",
        "activation_filename": (
            "qwen3-asr-17b-bf16-transformers-compile-dynamic-5060-asr-activation-v1.json"
        ),
    },
    {
        "slug": "parakeet-tdt-06b-v3-bf16-compile-static16",
        "filename": (
            "parakeet-tdt-06b-v3-bf16-transformers-compile-static16-warm10-"
            "5060-local-asr-pilot-v1.json"
        ),
        "artifact": "nvidia/parakeet-tdt-0.6b-v3",
        "model_id": "parakeet-tdt-0.6b-v3",
        "revision": "541d1f99c6b0c3cd0b11a95167540bb8edefd82b",
        "quantization": "bf16",
        "activation_filename": (
            "parakeet-tdt-06b-v3-bf16-transformers-compile-static16-5060-asr-activation-v1.json"
        ),
    },
)

HARDWARE: dict[str, dict[str, Any]] = {
    "m1": {
        "capture_token": "m1",
        "capture_name": "Apple M1 Max",
        "metadata_name": "Apple M1 Max 64 GB",
        "offering_name": "m1-max-64gb",
        "provider": "local:m1-max-64gb",
        "bootstrap": "m1-local-asr-pilot-v1-bootstrap.json",
        "runtime_kind": "mlx",
        "runtime_slug": "mlx",
        "endpoint": "in-process-mlx",
        "memory_comparable": True,
    },
    "m5": {
        "capture_token": "m5",
        "capture_name": "Apple M5 Max",
        "metadata_name": "Apple M5 Max 64 GB",
        "offering_name": "m5-max-64gb",
        "provider": "local:m5-max-64gb",
        "bootstrap": "m5-local-asr-pilot-v1-bootstrap.json",
        "runtime_kind": "mlx",
        "runtime_slug": "mlx",
        "endpoint": "in-process-mlx",
        "memory_comparable": True,
    },
    "5060": {
        "capture_token": "5060",
        "capture_name": "NVIDIA GeForce RTX 5060 Ti",
        "metadata_name": "NVIDIA GeForce RTX 5060 Ti 16 GB",
        "offering_name": "rtx5060ti-16gb",
        "provider": "local:rtx5060ti-16gb",
        "bootstrap": "rtx5060ti-local-asr-pilot-v1-bootstrap.json",
        "runtime_kind": "transformers",
        "runtime_slug": "transformers",
        "endpoint": "in-process-cuda",
        "memory_comparable": False,
    },
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _source(
    slug: str,
    machine: str,
    path: Path,
    machine_spec: dict[str, Any],
    capture: dict[str, Any],
) -> dict[str, Any]:
    runtime = "MLX-Audio" if machine_spec["runtime_kind"] == "mlx" else "Transformers/PyTorch CUDA"
    warmup_runs = capture["offering"].get("warmup_runs", 1)
    warmup = (
        "one unscored warmup" if warmup_runs == 1 else f"{warmup_runs} unscored warmup invocations"
    )
    return {
        "id": f"local-asr-pilot-{slug}-{machine}",
        "version": "1",
        "methodology": (
            f"Resident in-process {runtime} transcription; {warmup}, "
            "then 24 complete audio files sequentially at batch size one."
        ),
        "raw_sha256": _sha256(path),
    }


def _activation_source(
    slug: str,
    machine: str,
    path: Path,
    capture: dict[str, Any],
) -> dict[str, Any]:
    methodology = capture["methodology"]
    return {
        "id": f"local-asr-activation-{slug}-{machine}",
        "version": "1",
        "methodology": (
            f"{capture['summary']['attempt_count']} fresh-process activation attempts after "
            f"{methodology['seed_runs']} seed activation(s); locally cached weights; "
            f"compiler cache policy {methodology['compiler_cache']}; parent clock stops "
            "after receiving the synchronized first transcript over a dedicated pipe."
        ),
        "raw_sha256": _sha256(path),
    }


def _validate_activation_capture(
    path: Path,
    capture: dict[str, Any],
    *,
    runtime_kind: str,
) -> None:
    summary = capture["summary"]
    measurements = capture["measurements"]
    failures = capture["failures"]
    attempts = int(summary["attempt_count"])
    if attempts < 10:
        raise ValueError(f"too few activation attempts in {path.name}")
    if len(measurements) != int(summary["sample_count"]):
        raise ValueError(f"activation success count does not match {path.name}")
    if len(failures) != int(summary["failure_count"]):
        raise ValueError(f"activation failure count does not match {path.name}")
    if len(measurements) + len(failures) != attempts:
        raise ValueError(f"activation attempt count does not match {path.name}")
    public_failure_reasons = {
        "activation_timeout",
        "gpu_idle_check_failed",
        "child_process_failed",
    }
    if any(
        set(failure) != {"run", "error_type", "public_reason"}
        or failure["public_reason"] not in public_failure_reasons
        for failure in failures
    ):
        raise ValueError(f"activation failure details are not public-safe in {path.name}")
    expected_failure_percent = round(len(failures) / attempts * 100.0, 6)
    if float(summary["failure_percent"]) != expected_failure_percent:
        raise ValueError(f"activation failure rate does not match {path.name}")
    launch_times = [float(item["launch_to_first_transcript_seconds"]) for item in measurements]
    if any(value <= 0 for value in launch_times):
        raise ValueError(f"activation time is not positive in {path.name}")
    if round(statistics.median(launch_times), 6) != float(
        summary["launch_to_first_transcript_p50_seconds"]
    ):
        raise ValueError(f"activation median does not match {path.name}")
    variants = {str(item["normalized_hypothesis"]) for item in measurements}
    if len(variants) != int(summary["transcript_variants"]):
        raise ValueError(f"activation transcript variants do not match {path.name}")

    manifest = _load(MANIFEST)
    fixed_probe = manifest["items"][0]
    for measurement in measurements:
        if measurement["testcase_id"] != fixed_probe["testcase_id"]:
            raise ValueError(f"activation testcase does not match {path.name}")
        if measurement["audio_sha256"] != fixed_probe["audio_sha256"]:
            raise ValueError(f"activation audio does not match {path.name}")

    methodology = capture["methodology"]
    expected_cache = "system-default" if runtime_kind == "mlx" else "seeded-isolated"
    if methodology["compiler_cache"] != expected_cache:
        raise ValueError(f"unexpected activation cache policy in {path.name}")
    if methodology.get("require_idle_nvidia") is not (runtime_kind != "mlx"):
        raise ValueError(f"unexpected activation GPU-idle policy in {path.name}")
    if (
        methodology["seed_runs"] != 1
        or len(capture["seed_launch_to_first_transcript_seconds"]) != 1
    ):
        raise ValueError(f"activation seed count does not match {path.name}")
    if capture["offering"].get("panel_sample_count") != SAMPLE_COUNT:
        raise ValueError(f"activation panel count does not match {path.name}")

    harness = capture["harness"]
    if harness.get("wrapper_version") != "1":
        raise ValueError(f"unexpected activation wrapper version in {path.name}")
    if harness.get("wrapper_sha256") != _sha256(HERE / harness["wrapper"]):
        raise ValueError(f"stale activation wrapper binding in {path.name}")
    if harness.get("child_script_sha256") != _sha256(HERE / harness["child_script"]):
        raise ValueError(f"stale activation child binding in {path.name}")
    if not str(harness.get("python_version", "")).startswith("Python 3."):
        raise ValueError(f"missing activation Python version in {path.name}")
    if "/" in str(harness.get("python", "")):
        raise ValueError(f"activation Python path was not normalized in {path.name}")
    expected_backend = "mlx" if runtime_kind == "mlx" else "transformers"
    if harness.get("backend") != expected_backend:
        raise ValueError(f"activation backend does not match {path.name}")
    arguments = harness.get("benchmark_arguments")
    if not isinstance(arguments, list):
        raise ValueError(f"missing activation arguments in {path.name}")
    if len(arguments) % 2 or any(
        not str(arguments[index]).startswith("--") for index in range(0, len(arguments), 2)
    ):
        raise ValueError(f"activation arguments are not normalized pairs in {path.name}")
    argument_map = dict(zip(arguments[::2], arguments[1::2], strict=True))
    if len(argument_map) != len(arguments) // 2:
        raise ValueError(f"activation arguments contain duplicates in {path.name}")
    offering = capture["offering"]
    expected_arguments: dict[str, Any] = {
        "--model": offering["model"],
        "--model-revision": offering["requested_revision"],
        "--audio-dir": "<audio-dir>",
        "--prompt-manifest": "<prompt-manifest>",
        "--language": offering["requested_language"],
        "--max-tokens": offering["max_tokens"],
    }
    if runtime_kind != "mlx":
        expected_arguments.update(
            {
                "--family": offering["family"],
                "--dtype": offering["dtype"],
                "--optimization": offering["optimization"],
                "--warmup-runs": offering["warmup_runs"],
            }
        )
        if offering["static_audio_seconds"] is not None:
            expected_arguments["--static-audio-seconds"] = format(
                float(offering["static_audio_seconds"]), "g"
            )
    if {key: str(value) for key, value in expected_arguments.items()} != argument_map:
        raise ValueError(f"activation arguments do not match offering identity in {path.name}")


def _observation(
    value: Any,
    unit: str,
    observed_at: str,
    source: dict[str, Any],
    *,
    sample_count: int = SAMPLE_COUNT,
    lower: Any | None = None,
    upper: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": _decimal(value),
        "unit": unit,
        "sample_count": sample_count,
        "observed_at": observed_at,
        "source": source,
    }
    if lower is not None:
        result["lower"] = _decimal(lower)
    if upper is not None:
        result["upper"] = _decimal(upper)
    return result


def _bootstrap_entry(bootstrap: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    offering = capture["offering"]
    hardware = offering["hardware"]
    if isinstance(hardware, dict):
        hardware = hardware.get("name")
    key = f"{offering['model']}|{offering['runtime']}|{hardware}"
    optimization = offering.get("optimization", "baseline")
    static_seconds = offering.get("static_audio_seconds")
    warmup_runs = offering.get("warmup_runs", 1)
    if optimization != "baseline" or static_seconds is not None or warmup_runs != 1:
        key += (
            f"|optimization={optimization}|static_audio_seconds={static_seconds}"
            f"|warmup_runs={warmup_runs}"
        )
    try:
        entry = bootstrap["offerings"][key]
    except KeyError as error:
        raise ValueError(f"bootstrap is missing {key}") from error
    if not isinstance(entry, dict):
        raise ValueError(f"bootstrap entry for {key} is not an object")
    return entry


def _offering(spec: dict[str, str], machine: str, machine_spec: dict[str, Any]) -> dict[str, Any]:
    filename = spec.get("filename") or (
        f"{spec['slug']}-mlx-{machine_spec['capture_token']}-local-asr-pilot-v1.json"
    )
    capture_path = RAW / filename
    activation_filename = spec.get("activation_filename") or (
        f"{spec['slug']}-{machine_spec['runtime_slug']}-{machine_spec['capture_token']}"
        "-asr-activation-v1.json"
    )
    activation_path = RAW / activation_filename
    bootstrap_path = RAW / machine_spec["bootstrap"]
    capture = _load(capture_path)
    activation = _load(activation_path)
    bootstrap = _load(bootstrap_path)
    manifest = _load(MANIFEST)
    manifest_sha = _sha256(MANIFEST)

    if capture.get("schema") != "model-skyline/experimental-local-asr-capture/v1alpha1":
        raise ValueError(f"unsupported capture schema in {capture_path.name}")
    if capture["workload"]["manifest_sha256"] != manifest_sha:
        raise ValueError(f"unexpected manifest in {capture_path.name}")
    if capture["summary"]["sample_count"] != SAMPLE_COUNT:
        raise ValueError(f"unexpected sample count in {capture_path.name}")
    if activation.get("schema") != ("model-skyline/experimental-asr-activation-capture/v1alpha1"):
        raise ValueError(f"unsupported activation capture schema in {activation_path.name}")
    exact = capture["offering"]
    if exact["model"] != spec["artifact"]:
        raise ValueError(f"unexpected model in {capture_path.name}")
    if exact["resolved_revision"] != spec["revision"]:
        raise ValueError(f"unexpected revision in {capture_path.name}")
    capture_hardware = exact["hardware"]
    if isinstance(capture_hardware, dict):
        capture_hardware = capture_hardware.get("name")
    if capture_hardware != machine_spec["capture_name"]:
        raise ValueError(f"unexpected hardware in {capture_path.name}")
    activation_exact = activation["offering"]
    activation_hardware = activation_exact["hardware"]
    if isinstance(activation_hardware, dict):
        activation_hardware = activation_hardware.get("name")
    if activation_exact["model"] != exact["model"]:
        raise ValueError(f"activation model does not match {capture_path.name}")
    if activation_exact["resolved_revision"] != exact["resolved_revision"]:
        raise ValueError(f"activation revision does not match {capture_path.name}")
    if activation_hardware != capture_hardware:
        raise ValueError(f"activation hardware does not match {capture_path.name}")
    if activation_exact.get("optimization", "baseline") != exact.get("optimization", "baseline"):
        raise ValueError(f"activation optimization does not match {capture_path.name}")
    if activation_exact.get("static_audio_seconds") != exact.get("static_audio_seconds"):
        raise ValueError(f"activation static shape does not match {capture_path.name}")
    if activation_exact["max_tokens"] != exact["max_tokens"]:
        raise ValueError(f"activation token limit does not match {capture_path.name}")
    if machine_spec["runtime_kind"] != "mlx" and activation_exact["runtime"] != exact["runtime"]:
        raise ValueError(f"activation runtime does not match {capture_path.name}")
    for package in ("mlx_audio", "mlx", "transformers", "torch", "cuda"):
        if package in exact and activation_exact.get(package) != exact[package]:
            raise ValueError(f"activation {package} version does not match {capture_path.name}")
    if activation_exact["manifest_sha256"] != manifest_sha:
        raise ValueError(f"unexpected activation manifest in {activation_path.name}")
    _validate_activation_capture(
        activation_path,
        activation,
        runtime_kind=machine_spec["runtime_kind"],
    )
    resident_first = capture["measurements"][0]["normalized_hypothesis"]
    activation_first = {item["normalized_hypothesis"] for item in activation["measurements"]}
    if activation_first != {resident_first}:
        raise ValueError(f"activation transcript does not match {capture_path.name}")
    activation_summary = activation["summary"]
    interval = _bootstrap_entry(bootstrap, capture)
    if interval["capture_sha256"] != _sha256(capture_path):
        raise ValueError(f"stale bootstrap binding for {capture_path.name}")

    observed_at = capture["captured_at"]
    summary = capture["summary"]
    confidence = interval["interval_95"]
    source = _source(spec["slug"], machine, capture_path, machine_spec, capture)
    activation_source = _activation_source(spec["slug"], machine, activation_path, activation)
    empty_percent = Decimal(summary["empty_hypothesis_count"]) * Decimal(100)
    empty_percent /= Decimal(SAMPLE_COUNT)
    offering_id = (
        f"self-hosted/{spec['slug']}-{machine_spec['runtime_slug']}"
        f"@{machine_spec['offering_name']}-en"
    )
    if machine_spec["runtime_kind"] == "mlx":
        runtime = f"MLX-Audio {exact['mlx_audio']} / MLX {exact['mlx']}"
        runtime_config = (
            f"batch_size=1; max_tokens={exact['max_tokens']}; "
            f"requested_language={exact['requested_language']}; "
            f"language_argument={exact['language_argument']!r}"
        )
    else:
        runtime = (
            f"Transformers {exact['transformers']} / PyTorch {exact['torch']} / "
            f"CUDA {exact['cuda']}"
        )
        runtime_config = (
            f"batch_size=1; dtype={exact['dtype']}; max_tokens={exact['max_tokens']}; "
            f"language_argument={exact['language_argument']!r}; "
            f"attention={exact['attention']}; "
            f"optimization={exact.get('optimization', 'baseline')}; "
            f"static_audio_seconds={exact.get('static_audio_seconds')}; "
            f"warmup_runs={exact.get('warmup_runs', 1)}"
        )
    activation_runtime_config = (
        "fresh_process=true; per_process_warmup_runs=0; "
        f"seed_runs={activation['methodology']['seed_runs']}; "
        f"compiler_cache={activation['methodology']['compiler_cache']}; "
        f"timeout_seconds={activation['methodology']['timeout_seconds']}; "
        f"fixed_probe={activation['measurements'][0]['testcase_id']}; "
        f"audio_seconds={manifest['items'][0]['audio_seconds']}"
    )
    signals = {
        "asr_corpus_wer_percent": _observation(
            summary["corpus_wer_percentage"],
            "percent",
            observed_at,
            source,
            lower=confidence["corpus_wer_percentage"][0],
            upper=confidence["corpus_wer_percentage"][1],
        ),
        "asr_final_latency_p50_ms": _observation(
            summary["final_latency_p50_ms"],
            "milliseconds",
            observed_at,
            source,
            lower=confidence["final_latency_p50_ms"][0],
            upper=confidence["final_latency_p50_ms"][1],
        ),
        "asr_final_latency_p95_ms": _observation(
            summary["final_latency_p95_ms"],
            "milliseconds",
            observed_at,
            source,
            lower=confidence["final_latency_p95_ms"][0],
            upper=confidence["final_latency_p95_ms"][1],
        ),
        "asr_realtime_factor": _observation(
            summary["real_time_factor_corpus"], "ratio", observed_at, source
        ),
        "asr_empty_hypothesis_percent": _observation(empty_percent, "percent", observed_at, source),
        "asr_repeat_activation_p50_seconds": _observation(
            activation_summary["launch_to_first_transcript_p50_seconds"],
            "seconds",
            activation["captured_at"],
            activation_source,
            sample_count=activation_summary["sample_count"],
        ),
        "asr_activation_failure_percent": _observation(
            activation_summary["failure_percent"],
            "percent",
            activation["captured_at"],
            activation_source,
            sample_count=activation_summary["attempt_count"],
        ),
    }
    if machine_spec["memory_comparable"]:
        signals["asr_process_rss_peak_mb"] = _observation(
            summary["process_rss_mb_max"], "megabytes", observed_at, source
        )
    return {
        "offering": {
            "offering_id": offering_id,
            "model_id": spec["model_id"],
            "provider": machine_spec["provider"],
            "endpoint": machine_spec["endpoint"],
            "service_tier": "resident",
            "quantization": spec["quantization"],
            "agent_harness": "model-skyline-local-asr-pilot@1",
            "capabilities": ["asr", "audio", "stt"],
        },
        "metadata": {
            "hardware": machine_spec["metadata_name"],
            "artifact": exact["model"],
            "artifact_revision": exact["resolved_revision"],
            "runtime": runtime,
            "runtime_config": runtime_config,
            "activation_runtime_config": activation_runtime_config,
            "quality_scope": (
                "24-utterance English pilot across meeting, financial-call, "
                "difficult audiobook, and non-US political speech"
            ),
            "latency_scope": (
                "complete audio file already available to final transcript return; "
                "excludes end-of-turn detection and live-stream capture"
            ),
            "capture": f"raw/{filename}",
            "activation_capture": f"raw/{activation_filename}",
            "activation_capture_sha256": _sha256(activation_path),
            "activation_scope": (
                "repeat application-cold launch with already-downloaded weights and "
                "one fixed 12.35-second meeting clip; includes Python/runtime imports, "
                "device initialization, model load, and first synchronized inference; "
                "excludes download, WER scoring, JSON writing, and process teardown"
            ),
            "bootstrap": f"raw/{machine_spec['bootstrap']}",
            "bootstrap_sha256": _sha256(bootstrap_path),
            "manifest_sha256": manifest_sha,
            "audio_set_sha256": capture["workload"]["audio_set_sha256"],
        },
        "signals": signals,
    }


def _render(hardware: str) -> str:
    machines = HARDWARE if hardware == "all" else {hardware: HARDWARE[hardware]}
    catalog = {
        "schema_version": "model-skyline/v1alpha1",
        "workload": WORKLOAD,
        "offerings": [
            _offering(spec, machine, machine_spec)
            for machine, machine_spec in machines.items()
            for spec in (MLX_MODELS if machine_spec["runtime_kind"] == "mlx" else CUDA_MODELS)
        ],
    }
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", choices=("all", "m1", "m5", "5060"), default="all")
    parser.add_argument("--output", type=Path, default=HERE / "asr-observations.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if --output is not byte-identical to the generated catalog.",
    )
    args = parser.parse_args()
    rendered = _render(args.hardware)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale generated catalog: {args.output}")
        print(f"valid generated catalog: {args.output}")
        return
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
