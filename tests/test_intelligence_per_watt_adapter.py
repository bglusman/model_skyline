from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from model_skyline.adapters.intelligence_per_watt import (
    IPW_IMPORT_SCHEMA_VERSION,
    IPW_REVIEWED_REVISION,
    IntelligencePerWattAdapterError,
    IntelligencePerWattImportBinding,
    IntelligencePerWattRunIdentity,
    intelligence_per_watt_workload_version,
    load_intelligence_per_watt_binding,
    normalize_intelligence_per_watt_accuracy_bytes,
    normalize_intelligence_per_watt_accuracy_file,
)
from model_skyline.cli import app
from model_skyline.local_measurements import (
    LocalArtifactIdentity,
    LocalHardwareIdentity,
    LocalRuntimeIdentity,
    local_system_offering_key,
)
from model_skyline.models import OfferingKey

OBSERVED_AT = datetime(2026, 9, 16, 14, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 9, 16, 15, tzinfo=UTC)
SHA = "a" * 64
runner = CliRunner()


def _binding(**updates: Any) -> IntelligencePerWattImportBinding:
    run = IntelligencePerWattRunIdentity(
        dataset_id="TIGER-Lab/MMLU-Pro",
        dataset_revision="revision-a",
        task_manifest_sha256=SHA,
        task_set_kind="screen",
        attempts_per_task=1,
        concurrency=1,
        configuration_sha256="b" * 64,
    )
    hardware = LocalHardwareIdentity(
        hardware_id="macbook-m5max-64-adapter60",
        manufacturer="Apple",
        machine_model="Mac17,6",
        chip="Apple M5 Max",
        architecture="arm64",
        memory_bytes=68719476736,
        cpu_cores=18,
        cpu_core_groups={"super": 6, "performance": 12},
        gpu_cores=40,
        os_name="macOS",
        os_version="26.6.2 (25G83)",
        power_source="ac",
        power_mode="pmset-2-adapter-60w-system-reports-low-power",
        negotiated_adapter_watts=60,
    )
    artifact = LocalArtifactIdentity(
        model_id="model",
        checkpoint="model",
        revision="revision-a",
        format="mlx",
        quantization="q4",
        size_bytes=16_000_000_000,
        content_sha256="c" * 64,
    )
    runtime = LocalRuntimeIdentity(
        runtime_id="omlx",
        version="0.6.4",
        backend="metal",
        context_capacity_tokens=262144,
        kv_cache="f16",
        prefix_cache_enabled=True,
        agent_harness="ipw-openai-server",
    )
    offering = local_system_offering_key(
        hardware=hardware,
        artifact=artifact,
        runtime=runtime,
        capabilities=("text",),
    )
    payload: dict[str, Any] = {
        "schema_version": IPW_IMPORT_SCHEMA_VERSION,
        "upstream_revision": IPW_REVIEWED_REVISION,
        "expected_model": "local-test-model",
        "expected_energy_basis": "soc",
        "observed_at": OBSERVED_AT,
        "workload_id": "mmlu-pro-local-screen-v1",
        "workload_unit": "question",
        "run": run,
        "hardware": hardware,
        "artifact": artifact,
        "runtime": runtime,
        "offering": offering,
        "metadata": {"operator_note": "synthetic fixture"},
    }
    payload.update(updates)
    return IntelligencePerWattImportBinding.model_validate(payload)


def _artifact() -> dict[str, Any]:
    summary = {
        "model": "local-test-model",
        "correct": 1,
        "incorrect": 1,
        "unevaluated": 0,
        "failed": 0,
        "skipped_empty_responses": 0,
        "total_scored": 2,
        "accuracy": 0.5,
        "intelligence_per_joule": 1 / 30,
        "intelligence_per_watt": 1 / 12,
        "avg_per_query_energy_joules": 15,
        "avg_per_query_power_watts": 6,
        "energy_sample_count": 2,
        "power_sample_count": 2,
        "telemetry_coverage": {
            "records_checked": 2,
            "records_energy_short_of_power_x_time": 0,
            "worst_energy_to_power_x_time_ratio": None,
        },
        "energy_basis": "soc",
    }
    return {
        "analysis": "accuracy",
        "summary": summary,
        "data": {
            "per_model": {"local-test-model": summary},
            "records": {
                "local-test-model": [
                    {
                        "problem": "private prompt",
                        "reference_answer": "private answer",
                        "model_answer": "private response",
                    },
                    {
                        "problem": "second private prompt",
                        "reference_answer": "second private answer",
                        "model_answer": "second private response",
                    },
                ]
            },
            "efficiency": {
                "local-test-model": {
                    "intelligence_per_joule": 1 / 30,
                    "intelligence_per_watt": 1 / 12,
                    "energy": {
                        "avg": 15,
                        "min": 10,
                        "max": 20,
                        "count": 2,
                        "total": 30,
                        "accuracy": 0.5,
                        "zero_values": 0,
                        "imputed_from_power": None,
                        "imputed_count": 0,
                    },
                    "power": {
                        "avg": 6,
                        "min": 5,
                        "max": 7,
                        "count": 2,
                        "accuracy": 0.5,
                        "zero_values": 0,
                        "derived_power_samples": 2,
                        "power_metric_samples": 0,
                    },
                }
            },
        },
    }


def _encoded(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def test_projects_only_complete_aggregate_evidence() -> None:
    catalog = normalize_intelligence_per_watt_accuracy_bytes(
        _encoded(_artifact()),
        binding=_binding(metadata={"adapter": "cannot override", "note": "retained"}),
        retrieved_at=RETRIEVED_AT,
    )

    offering = catalog.offerings[0]
    assert catalog.workload.version == intelligence_per_watt_workload_version(_binding().run)
    assert offering.signals["ipw_accuracy_percent"].value == 50
    assert offering.signals["ipw_average_energy_joules_per_item"].value == 15
    assert offering.signals["ipw_average_power_watts"].value == 6
    assert offering.signals["ipw_intelligence_per_joule"].sample_count == 2
    assert offering.metadata["energy_basis"] == "soc"
    assert offering.metadata["binding_metadata"] == {
        "adapter": "cannot override",
        "note": "retained",
    }
    assert offering.metadata["adapter"] == {
        "id": "model-skyline/intelligence-per-watt-accuracy",
        "upstream_revision": IPW_REVIEWED_REVISION,
        "version": "1",
    }
    assert offering.metadata["raw_artifact_publication_safe"] is False
    serialized = catalog.model_dump_json()
    assert "private prompt" not in serialized
    assert "private answer" not in serialized
    assert "private response" not in serialized


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("summary", "energy_basis"), "gpu", "energy basis"),
        (("summary", "energy_sample_count"), 1, "cover every scored item"),
        (
            ("summary", "telemetry_coverage", "records_energy_short_of_power_x_time"),
            1,
            "truncated energy windows",
        ),
        (("data", "efficiency", "local-test-model", "energy", "imputed_count"), 1, "imputed"),
        (("data", "efficiency", "local-test-model", "power", "zero_values"), 1, "non-positive"),
    ],
)
def test_rejects_incomplete_or_incomparable_telemetry(
    path: tuple[str, ...], value: Any, message: str
) -> None:
    artifact = _artifact()
    target: dict[str, Any] = artifact
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(IntelligencePerWattAdapterError, match=message):
        normalize_intelligence_per_watt_accuracy_bytes(
            _encoded(artifact),
            binding=_binding(),
            retrieved_at=RETRIEVED_AT,
        )


def test_rejects_recomputed_accuracy_or_efficiency_disagreement() -> None:
    artifact = _artifact()
    artifact["summary"]["accuracy"] = 0.75
    with pytest.raises(IntelligencePerWattAdapterError, match="accuracy does not match"):
        normalize_intelligence_per_watt_accuracy_bytes(
            _encoded(artifact), binding=_binding(), retrieved_at=RETRIEVED_AT
        )

    artifact = _artifact()
    artifact["summary"]["intelligence_per_watt"] = 99
    with pytest.raises(IntelligencePerWattAdapterError, match="does not replay"):
        normalize_intelligence_per_watt_accuracy_bytes(
            _encoded(artifact), binding=_binding(), retrieved_at=RETRIEVED_AT
        )


def test_zero_accuracy_accepts_upstream_null_ratios() -> None:
    artifact = _artifact()
    artifact["summary"].update(correct=0, incorrect=2, accuracy=0)
    artifact["summary"]["intelligence_per_joule"] = None
    artifact["summary"]["intelligence_per_watt"] = None
    artifact["data"]["efficiency"]["local-test-model"]["intelligence_per_joule"] = None
    artifact["data"]["efficiency"]["local-test-model"]["intelligence_per_watt"] = None
    artifact["data"]["efficiency"]["local-test-model"]["energy"]["accuracy"] = 0
    artifact["data"]["efficiency"]["local-test-model"]["power"]["accuracy"] = 0
    catalog = normalize_intelligence_per_watt_accuracy_bytes(
        _encoded(artifact), binding=_binding(), retrieved_at=RETRIEVED_AT
    )

    assert catalog.offerings[0].signals["ipw_intelligence_per_joule"].value == 0
    assert catalog.offerings[0].signals["ipw_intelligence_per_watt"].value == 0


def test_binding_requires_reviewed_exact_local_power_tier() -> None:
    with pytest.raises(ValidationError, match="reviewed Intelligence Per Watt revision"):
        _binding(upstream_revision="c" * 40)

    with pytest.raises(ValidationError, match="Apple Silicon.*soc basis"):
        _binding(expected_energy_basis="gpu")

    offering = _binding().offering.model_copy(update={"service_tier": None})
    with pytest.raises(ValidationError, match="exact power service tier"):
        _binding(offering=offering)

    offering = OfferingKey.model_validate(
        {**_binding().offering.model_dump(mode="json"), "offering_id": "invented"}
    )
    with pytest.raises(ValidationError, match="does not match the exact hardware"):
        _binding(offering=offering)


def test_file_loaders_do_not_follow_symlinks(tmp_path: Path) -> None:
    artifact = tmp_path / "accuracy.json"
    artifact.write_bytes(_encoded(_artifact()))
    artifact_link = tmp_path / "accuracy-link.json"
    artifact_link.symlink_to(artifact)
    with pytest.raises(IntelligencePerWattAdapterError, match="cannot open"):
        normalize_intelligence_per_watt_accuracy_file(
            artifact_link, binding=_binding(), retrieved_at=RETRIEVED_AT
        )

    binding_file = tmp_path / "binding.json"
    binding_file.write_text(_binding().model_dump_json())
    binding_link = tmp_path / "binding-link.json"
    binding_link.symlink_to(binding_file)
    with pytest.raises(IntelligencePerWattAdapterError, match="cannot open"):
        load_intelligence_per_watt_binding(binding_link)


def test_duplicate_json_keys_are_rejected() -> None:
    artifact = _encoded(_artifact())
    duplicate = artifact.replace(
        b'{"analysis":"accuracy",', b'{"analysis":"accuracy","analysis":"accuracy",'
    )
    with pytest.raises(IntelligencePerWattAdapterError, match="duplicate JSON key"):
        normalize_intelligence_per_watt_accuracy_bytes(
            duplicate, binding=_binding(), retrieved_at=RETRIEVED_AT
        )


def test_cli_projects_private_artifact_to_prompt_free_catalog(tmp_path: Path) -> None:
    artifact = tmp_path / "accuracy.json"
    artifact.write_bytes(_encoded(_artifact()))
    binding = tmp_path / "binding.json"
    binding.write_text(_binding().model_dump_json(), encoding="utf-8")
    output = tmp_path / "catalog.json"

    result = runner.invoke(
        app,
        [
            "import-intelligence-per-watt",
            str(artifact),
            str(binding),
            "--retrieved-at",
            RETRIEVED_AT.isoformat(),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    projected = output.read_text(encoding="utf-8")
    assert "ipw_average_power_watts" in projected
    assert "private prompt" not in projected
    assert "private answer" not in projected
    assert "private response" not in projected
