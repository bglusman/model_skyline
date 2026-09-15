from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from model_skyline.canonical import content_hash

ROOT = Path(__file__).parents[1]
BASE = ROOT / "examples" / "local-runtime-frontiers"


def _load(relative_path: str) -> dict[str, Any]:
    return json.loads((BASE / relative_path).read_text(encoding="utf-8"))


def test_shoehorn_plan_is_internally_consistent() -> None:
    plan = _load("artifacts/laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-plan.json")
    observation = plan["solver_observation"]

    assert sum(item["tensors"] for item in observation["assignments"]) == 678
    assert observation["tensor_count"] == 678
    assert observation["solved_tensor_count"] == 478
    assert observation["imatrix_solved_tensor_count"] == 476
    assert observation["fixed_tensor_count"] == 200
    assert observation["reported_weight_slack_bytes"] == 512
    assert plan["output"]["size_bytes"] == 12_481_779_328
    assert plan["output"]["sha256"] == (
        "2a0b5d0cfb25d7e5387330511e71c16452878772d757505cf5c7f5849dfa70f2"
    )


def test_shoehorn_manifest_hashes_the_exact_file_list() -> None:
    manifest = _load(
        "artifacts/laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-manifest.json"
    )

    assert manifest["size_bytes"] == sum(item["size_bytes"] for item in manifest["files"])
    assert manifest["content_sha256"] == content_hash(manifest["files"])


def test_memory_captures_bind_their_workload_files() -> None:
    for memory_name in (
        "laguna-xs21-q2kl-5060-service-memory-v2-load-none-linuxswap.json",
        "laguna-xs21-shoehorn-5060-service-memory-v2-load-none-linuxswap.json",
    ):
        capture = _load(f"raw/{memory_name}")
        assert capture["summary"]["peak_host_swap_growth_bytes"] < 1024**2
        for workload in capture["workload_captures"]:
            raw_path = BASE / "raw" / workload["name"]
            assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == workload["sha256"]


def test_normalized_measurements_bind_their_raw_captures() -> None:
    names = (
        "laguna-xs21-q2kl-5060-pp2048-tg512.json",
        "laguna-xs21-q2kl-llamacpp-cuda-load-none-retrieval-mid-r3-"
        "p30000-o64-disabled-needle-0p5.json",
        "laguna-xs21-q2kl-llamacpp-cuda-load-none-tool30-auto-p2048-o256-disabled.json",
        "laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-pp2048-tg512.json",
        "laguna-xs21-shoehorn-5060-load-none-retrieval-mid-r3-"
        "p30000-o64-disabled-needle-0p5.json",
        "laguna-xs21-shoehorn-5060-load-none-tool30-auto-p2048-o256-disabled.json",
    )
    for name in names:
        measurement = _load(f"measurements/{name}")
        raw_path = BASE / measurement["provenance"]["raw_artifact_path"]
        assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == (
            measurement["provenance"]["raw_sha256"]
        )


def test_custom_fit_retains_invalid_perplexity_evidence() -> None:
    for suffix in ("invalid", "fa-off-f16kv"):
        capture = _load(
            "raw/laguna-xs21-shoehorn-5060-ctx32k-q8kv-exact-"
            f"wikitext2-head512-ppl-{suffix}.json"
        )
        assert capture["status"] == "invalid"
        assert capture["result"] is None
        assert capture["process_returncode"] == 0
        assert capture["stdout"].count("nan") == 6
        assert "Unexpected negative standard deviation" in capture["stderr"]


def test_matched_retrieval_gate_rejects_the_custom_fit() -> None:
    stock = _load(
        "measurements/laguna-xs21-q2kl-llamacpp-cuda-load-none-"
        "retrieval-mid-r3-p30000-o64-disabled-needle-0p5.json"
    )
    custom = _load(
        "measurements/laguna-xs21-shoehorn-5060-load-none-"
        "retrieval-mid-r3-p30000-o64-disabled-needle-0p5.json"
    )

    assert stock["workload"]["input_definition_sha256"] == (
        custom["workload"]["input_definition_sha256"]
    )
    assert stock["performance"]["actual_input_tokens"] == 30_000
    assert custom["performance"]["actual_input_tokens"] == 30_000
    assert stock["integrity"]["retrieval"] == {"passed": 3, "total": 3}
    assert custom["integrity"]["retrieval"] == {"passed": 0, "total": 3}


def test_generated_frontiers_keep_narrow_winners_distinct() -> None:
    throughput = _load("generated/laguna-xs21-5060-quant-short-throughput-frontier.json")
    tools = _load("generated/laguna-xs21-5060-quant-tool30-p2048-o256-frontier.json")
    retrieval = _load("generated/laguna-xs21-5060-quant-retrieval-p30000-frontier.json")

    assert throughput["members"][0]["offering"]["quantization"].startswith("shoehorn-")
    assert tools["members"][0]["offering"]["quantization"].startswith("ShoeHorn ")
    assert retrieval["members"][0]["offering"]["quantization"] == "Q2_K_L"
    assert len(retrieval["rejected"]) == 1
    assert "ShoeHorn exact mixed 2.985 bpw" in retrieval["rejected"][0]["offering_id"]
    assert retrieval["rejected"][0]["reasons"] == [
        "retrieval_success: value 0 is below eligible minimum 1E+2"
    ]


def test_published_laguna_5060_evidence_has_no_private_machine_paths() -> None:
    paths = []
    for directory in ("artifacts", "generated", "measurements", "raw", "system-profiles"):
        paths.extend((BASE / directory).glob("laguna-xs21-*5060*.json"))

    assert paths
    for path in paths:
        serialized = path.read_text(encoding="utf-8")
        assert "192.168." not in serialized
        assert "/Users/" not in serialized
        assert "/root/" not in serialized
        assert "admin:admin" not in serialized
