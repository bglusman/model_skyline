from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from model_skyline.io import load_local_measurement

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "local-runtime-frontiers"
SCRIPT = EXAMPLE / "normalize_llama_bench.py"
CAPTURE = EXAMPLE / "raw" / "ornith-q4km-m5max64-pp2048-tg512.json"
HARDWARE = EXAMPLE / "hardware" / "macbook-m5max-64.json"


def test_normalizer_requires_exact_quant_and_derives_workload_identity(tmp_path: Path) -> None:
    output = tmp_path / "measurement.json"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--capture",
            str(CAPTURE),
            "--hardware",
            str(HARDWARE),
            "--output",
            str(output),
            "--raw-artifact-path",
            "raw/test.json",
            "--measurement-id",
            "custom-mixed-test",
            "--model-id",
            "ornith-ai/Ornith-1.5-35B-A3B",
            "--checkpoint",
            "Ornith-1.5-35B-A3B",
            "--model-revision",
            "fixture-revision",
            "--model-source-url",
            "https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF",
            "--model-license",
            "MIT",
            "--quantization",
            "ShoeHorn-mixed-10.981bpw",
            "--context-capacity",
            "131072",
        ],
        check=True,
    )

    record = load_local_measurement(output)
    assert record.artifact.quantization == "ShoeHorn-mixed-10.981bpw"
    assert record.workload.reference.id == "llama-bench-pp2048-tg512"
    assert record.workload.reference.version == "llama.cpp@5266f24da/model-skyline@v1"
    assert record.provenance.methodology.startswith(
        "llama-bench built-in warmup followed by five serial"
    )
