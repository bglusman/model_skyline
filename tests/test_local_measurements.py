from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from model_skyline.cli import app
from model_skyline.io import load_catalog, load_local_measurement
from model_skyline.local_measurements import (
    LocalMeasurementRecord,
    build_local_capacity_catalog,
    build_local_catalog,
    local_offering_key,
    local_system_offering_key,
)
from model_skyline.models import WorkloadReference


def local_measurement_payload() -> dict[str, object]:
    return {
        "schema_version": "model-skyline/local-measurement/v1alpha1",
        "measurement_id": "ornith-q4-m5-short-001",
        "status": "provisional",
        "started_at": "2026-09-12T23:00:00-04:00",
        "completed_at": "2026-09-12T23:02:00-04:00",
        "hardware": {
            "hardware_id": "macbook-m5max-64",
            "manufacturer": "Apple",
            "machine_model": "MacBookPro19,1",
            "chip": "Apple M5 Max",
            "architecture": "arm64",
            "memory_bytes": 68_719_476_736,
            "cpu_cores": 18,
            "cpu_core_groups": {"super": 6, "performance": 12},
            "gpu_cores": 40,
            "os_name": "macOS",
            "os_version": "26.3.1",
            "power_source": "ac",
            "power_mode": "high-power",
            "negotiated_adapter_watts": 140,
            "metadata": {},
        },
        "artifact": {
            "model_id": "ornith-ai/Ornith-1.5-35B-A3B",
            "checkpoint": "Ornith-1.5-35B-A3B",
            "revision": "main",
            "format": "gguf",
            "quantization": "Q4_K_M",
            "size_bytes": 23_313_331_200,
            "content_sha256": "a" * 64,
            "source_url": "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF",
            "license": "MIT",
            "metadata": {},
        },
        "runtime": {
            "runtime_id": "llama.cpp",
            "version": "build-10809",
            "commit": "5266f24da",
            "backend": "metal",
            "context_capacity_tokens": 262_144,
            "kv_cache": "q8_0",
            "prefix_cache_enabled": False,
            "speculative_method": None,
            "agent_harness": "model-skyline/openai-matrix@v1",
            "configuration": {"flash_attention": True, "threads": 6},
        },
        "workload": {
            "reference": {
                "id": "local-short-decode-2048x512",
                "version": "1",
                "unit": "request",
            },
            "kind": "short_decode",
            "input_definition_sha256": "b" * 64,
            "requested_input_tokens": 2_048,
            "max_output_tokens": 512,
            "repetitions": 3,
            "warmup_repetitions": 1,
            "concurrency": 1,
            "runner_state": "warm",
            "prefix_cache_state": "disabled",
            "position": {"batch": 2048, "micro_batch": 512},
        },
        "performance": {
            "actual_input_tokens": 2_048,
            "output_token_counts": [512, 512, 512],
            "metrics": {
                "prompt_tokens_per_second": {
                    "unit": "token/s",
                    "values": ["830000.1", "829999.5", "831000.4"],
                },
                "decode_tokens_per_second": {
                    "unit": "token/s",
                    "values": ["49.2", "50.1", "49.8"],
                },
                "peak_process_rss_bytes": {
                    "unit": "byte",
                    "values": ["30000000000", "30100000000", "29900000000"],
                },
            },
        },
        "integrity": {
            "retrieval": {"passed": 3, "total": 3},
            "tool_calls": {"passed": 3, "total": 3},
            "tool_argument_parsing": {"passed": 3, "total": 3},
            "structured_output": {"passed": 2, "total": 3},
        },
        "capabilities": ["tools", "structured-output", "long-context", "text"],
        "provenance": {
            "tool": "llama-bench",
            "tool_version": "build-10809",
            "command_sha256": "c" * 64,
            "raw_artifact_path": "raw/ornith-q4-m5-short-001.json",
            "raw_sha256": "d" * 64,
            "captured_at": "2026-09-12T23:02:01-04:00",
            "methodology": "One warmup followed by three serial measurements.",
            "source_url": None,
            "license": "CC0-1.0",
        },
        "notes": None,
    }


def test_local_record_is_strict_exact_and_normalizes_timestamps() -> None:
    record = LocalMeasurementRecord.model_validate(local_measurement_payload())

    assert record.started_at == datetime(2026, 9, 13, 3, tzinfo=UTC)
    assert record.capabilities == ("long-context", "structured-output", "text", "tools")
    assert record.performance is not None
    assert record.performance.metrics["decode_tokens_per_second"].values[0] == Decimal("49.2")

    payload = local_measurement_payload()
    payload["model_quality"] = "79"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LocalMeasurementRecord.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("hardware", "chip", "Apple M1 Max"),
        ("artifact", "content_sha256", "e" * 64),
        ("artifact", "quantization", "Q5_K_M"),
        ("runtime", "kv_cache", "f16"),
        ("runtime", "context_capacity_tokens", 131_072),
        ("runtime", "configuration", {"flash_attention": False, "threads": 6}),
    ],
)
def test_exact_local_offering_identity_changes_for_every_system_dimension(
    section: str,
    field: str,
    value: object,
) -> None:
    baseline = local_measurement_payload()
    changed = deepcopy(baseline)
    subsection = changed[section]
    assert isinstance(subsection, dict)
    subsection[field] = value

    baseline_key = local_offering_key(LocalMeasurementRecord.model_validate(baseline))
    changed_key = local_offering_key(LocalMeasurementRecord.model_validate(changed))

    assert baseline_key.offering_id != changed_key.offering_id


def test_workload_and_raw_capture_do_not_change_system_identity() -> None:
    baseline = local_measurement_payload()
    changed = deepcopy(baseline)
    workload = changed["workload"]
    provenance = changed["provenance"]
    assert isinstance(workload, dict)
    assert isinstance(provenance, dict)
    workload["requested_input_tokens"] = 4_096
    workload["reference"] = {"id": "another-position", "version": "1", "unit": "request"}
    provenance["raw_sha256"] = "f" * 64
    artifact = changed["artifact"]
    hardware = changed["hardware"]
    assert isinstance(artifact, dict)
    assert isinstance(hardware, dict)
    artifact["source_url"] = "https://example.test/mirror"
    artifact["license"] = "MIT"
    hardware["metadata"] = {"inventory_note": "non-identity metadata"}

    baseline_key = local_offering_key(LocalMeasurementRecord.model_validate(baseline))
    changed_key = local_offering_key(LocalMeasurementRecord.model_validate(changed))

    assert baseline_key == changed_key


def test_system_components_build_the_same_exact_offering_key() -> None:
    record = LocalMeasurementRecord.model_validate(local_measurement_payload())

    assert local_system_offering_key(
        hardware=record.hardware,
        artifact=record.artifact,
        runtime=record.runtime,
        capabilities=record.capabilities,
    ) == local_offering_key(record)


def test_catalog_projection_retains_samples_integrity_and_exact_metadata() -> None:
    record = LocalMeasurementRecord.model_validate(local_measurement_payload())
    catalog = build_local_catalog([record])
    offering = catalog.offerings[0]

    decode = offering.signals["local_decode_tokens_per_second"]
    assert decode.value == Decimal("49.8")
    assert decode.lower == Decimal("49.2")
    assert decode.upper == Decimal("50.1")
    assert decode.sample_count == 3
    assert offering.signals["local_tool_call_success_percent"].value == Decimal("100")
    assert offering.signals["local_tool_argument_parse_success_percent"].value == Decimal("100")
    assert offering.signals["local_structured_output_success_percent"].value == Decimal(
        "66.66666666666666666666666666666667"
    )
    assert offering.signals["local_validated_context_tokens"].value == Decimal("2048")
    assert offering.metadata["artifact"]["content_sha256"] == "a" * 64


def test_catalog_percentage_does_not_inherit_ambient_decimal_context() -> None:
    record = LocalMeasurementRecord.model_validate(local_measurement_payload())

    with localcontext(Context(prec=6)):
        value = (
            build_local_catalog([record])
            .offerings[0]
            .signals["local_structured_output_success_percent"]
            .value
        )

    assert value == Decimal("66.66666666666666666666666666666667")


def test_catalog_requires_same_workload_position_and_unique_exact_offerings() -> None:
    first = LocalMeasurementRecord.model_validate(local_measurement_payload())
    payload = local_measurement_payload()
    payload["measurement_id"] = "second"
    second = LocalMeasurementRecord.model_validate(payload)

    with pytest.raises(ValueError, match="duplicate exact local offerings"):
        build_local_catalog([first, second])

    changed = deepcopy(payload)
    workload = changed["workload"]
    assert isinstance(workload, dict)
    workload["prefix_cache_state"] = "warm"
    with pytest.raises(ValueError, match="same workload position"):
        build_local_catalog([first, LocalMeasurementRecord.model_validate(changed)])


def test_catalog_ignores_observed_response_metadata_in_workload_position() -> None:
    first_payload = local_measurement_payload()
    first_payload["measurement_id"] = "first"
    first_artifact = first_payload["artifact"]
    first_workload = first_payload["workload"]
    assert isinstance(first_artifact, dict)
    assert isinstance(first_workload, dict)
    first_artifact["content_sha256"] = "1" * 64
    first_workload["position"] = {
        "batch": 2048,
        "time_to_first_token_source": "server-reported",
        "finish_reasons": {"stop": 3},
    }
    second_payload = deepcopy(first_payload)
    second_payload["measurement_id"] = "second"
    second_artifact = second_payload["artifact"]
    second_workload = second_payload["workload"]
    assert isinstance(second_artifact, dict)
    assert isinstance(second_workload, dict)
    second_artifact["content_sha256"] = "2" * 64
    second_workload["position"] = {
        "batch": 2048,
        "time_to_first_token_source": "unavailable",
        "finish_reasons": {"tool_calls": 3},
    }

    catalog = build_local_catalog(
        [
            LocalMeasurementRecord.model_validate(first_payload),
            LocalMeasurementRecord.model_validate(second_payload),
        ]
    )

    assert len(catalog.offerings) == 2


def test_uncached_catalog_combines_disabled_and_proven_zero_hit_miss() -> None:
    disabled_payload = local_measurement_payload()
    disabled_payload["measurement_id"] = "disabled"
    disabled_artifact = disabled_payload["artifact"]
    assert isinstance(disabled_artifact, dict)
    disabled_artifact["content_sha256"] = "1" * 64
    miss_payload = deepcopy(disabled_payload)
    miss_payload["measurement_id"] = "miss"
    miss_artifact = miss_payload["artifact"]
    miss_runtime = miss_payload["runtime"]
    miss_workload = miss_payload["workload"]
    miss_performance = miss_payload["performance"]
    assert isinstance(miss_artifact, dict)
    assert isinstance(miss_runtime, dict)
    assert isinstance(miss_workload, dict)
    assert isinstance(miss_performance, dict)
    miss_artifact["content_sha256"] = "2" * 64
    miss_runtime["prefix_cache_enabled"] = True
    miss_workload["prefix_cache_state"] = "miss"
    metrics = miss_performance["metrics"]
    assert isinstance(metrics, dict)
    metrics["prefix_cache_hit_tokens"] = {
        "unit": "token",
        "values": ["0", "0", "0"],
    }

    catalog = build_local_catalog(
        [
            LocalMeasurementRecord.model_validate(disabled_payload),
            LocalMeasurementRecord.model_validate(miss_payload),
        ],
        workload=WorkloadReference(id="uncached-agent-v1", version="1", unit="request"),
        cache_cohort="uncached",
    )

    assert catalog.workload.id == "uncached-agent-v1"
    assert len(catalog.offerings) == 2


def test_uncached_catalog_rejects_warm_or_unproven_miss() -> None:
    payload = local_measurement_payload()
    runtime = payload["runtime"]
    workload = payload["workload"]
    assert isinstance(runtime, dict)
    assert isinstance(workload, dict)
    runtime["prefix_cache_enabled"] = True
    workload["prefix_cache_state"] = "miss"
    record = LocalMeasurementRecord.model_validate(payload)

    with pytest.raises(ValueError, match="zero-hit"):
        build_local_catalog([record], cache_cohort="uncached")


def _capacity_payload(tokens: int, measurement_id: str) -> dict[str, object]:
    payload = local_measurement_payload()
    payload["measurement_id"] = measurement_id
    workload = payload["workload"]
    performance = payload["performance"]
    integrity = payload["integrity"]
    assert isinstance(workload, dict)
    assert isinstance(performance, dict)
    assert isinstance(integrity, dict)
    workload.update(
        {
            "kind": "long_context_retrieval",
            "requested_input_tokens": tokens,
            "max_output_tokens": 64,
            "repetitions": 3,
            "position": {
                "mode": "retrieval",
                "system_prompt_sha256": "e" * 64,
                "tool_schema_sha256": None,
                "tool_count": 0,
                "tool_choice": None,
                "thinking_mode": "disabled",
                "sampling": {"temperature": 0, "seed": 90421},
                "api": "openai-chat-completions-streaming",
                "retrieval_character_fraction": "0.5",
                "prompt_bytes": tokens * 2,
                "user_prompt_sha256": "f" * 64,
            },
        }
    )
    performance["actual_input_tokens"] = tokens
    performance["output_token_counts"] = [21, 21, 21]
    metrics = performance["metrics"]
    assert isinstance(metrics, dict)
    metrics["peak_process_physical_footprint_bytes"] = {
        "unit": "byte",
        "values": ["20000000000", "21000000000", "20500000000"],
    }
    integrity["retrieval"] = {"passed": 3, "total": 3}
    return payload


def test_capacity_catalog_selects_largest_passing_position() -> None:
    small = LocalMeasurementRecord.model_validate(_capacity_payload(2_048, "small"))
    large = LocalMeasurementRecord.model_validate(_capacity_payload(126_000, "large"))

    catalog = build_local_capacity_catalog(
        [small, large],
        workload=WorkloadReference(
            id="validated-capacity-v1",
            version="1",
            unit="context_position",
        ),
    )

    signals = catalog.offerings[0].signals
    assert signals["local_validated_context_tokens"].value == Decimal("126000")
    assert signals["local_peak_process_physical_footprint_bytes"].value == Decimal("20500000000")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (("performance", "actual_input_tokens", 300_000), "context capacity"),
        (("performance", "metrics", {}), "at least 1 item"),
        (("capabilities", "value", ["text"]), "requires that capability"),
    ],
)
def test_local_record_rejects_incomparable_or_unsupported_claims(
    mutate: tuple[str, str, object],
    message: str,
) -> None:
    payload = local_measurement_payload()
    section_name, field, value = mutate
    if section_name == "capabilities":
        payload[section_name] = value
    else:
        section = payload[section_name]
        assert isinstance(section, dict)
        section[field] = value

    with pytest.raises(ValidationError, match=message):
        LocalMeasurementRecord.model_validate(payload)


def test_local_loader_round_trips_canonical_json(tmp_path) -> None:
    source = tmp_path / "measurement.json"
    source.write_text(json.dumps(local_measurement_payload()), encoding="utf-8")

    loaded = load_local_measurement(source)

    assert loaded.measurement_id == "ornith-q4-m5-short-001"


def test_local_cli_validates_and_builds_an_ordinary_catalog(tmp_path) -> None:
    source = tmp_path / "measurement.json"
    source.write_text(json.dumps(local_measurement_payload()), encoding="utf-8")
    output = tmp_path / "catalog.json"
    runner = CliRunner()

    validated = runner.invoke(app, ["validate-local-measurement", str(source)])
    built = runner.invoke(
        app,
        ["build-local-catalog", str(source), "--output", str(output)],
    )

    assert validated.exit_code == 0
    assert "ornith-q4-m5-short-001" in validated.stdout
    assert built.exit_code == 0
    assert load_catalog(output).offerings[0].signals["local_decode_tokens_per_second"]
