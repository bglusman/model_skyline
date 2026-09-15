from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "capture_llama_perplexity.py"


def _load_capture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_llama_perplexity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPT.parent))
    return module


def test_parse_final_perplexity_and_runtime_identity() -> None:
    capture = _load_capture()
    result = capture._parse_result(
        (
            "perplexity: calculating perplexity over 6 chunks, n_ctx=4096\n"
            "Final estimate: PPL = 7.1234 +/- 0.05678\n"
        ),
        "build = 10809 (5266f24da)\n",
    )

    assert result == {
        "perplexity": "7.1234",
        "standard_error": "0.05678",
        "chunks_evaluated": 6,
        "runtime_build_number": 10809,
        "runtime_commit": "5266f24da",
        "unused_tensors": [],
        "unused_tensor_bytes": 0,
    }


def test_parse_current_llama_cpp_version_format() -> None:
    capture = _load_capture()

    result = capture._parse_result(
        (
            "perplexity: calculating perplexity over 6 chunks, n_ctx=4096\n"
            "Final estimate: PPL = 7.1234 +/- 0.05678\n"
        ),
        "version: 0.4.0 (build 10809, commit 5266f24da)\n",
    )

    assert result["runtime_build_number"] == 10809
    assert result["runtime_commit"] == "5266f24da"


def test_parse_retains_ignored_tensor_cost() -> None:
    capture = _load_capture()

    result = capture._parse_result(
        (
            "perplexity: calculating perplexity over 3 chunks, n_ctx=4096\n"
            "Final estimate: PPL = 7.0 +/- 0.1\n"
        ),
        (
            "model has unused tensor blk.40.attn_q.weight "
            "(size = 33554432 bytes) -- ignoring\n"
            "build: 10809 (5266f24da)\n"
        ),
    )

    assert result["chunks_evaluated"] == 3
    assert result["unused_tensors"] == [{"name": "blk.40.attn_q.weight", "size_bytes": 33_554_432}]
    assert result["unused_tensor_bytes"] == 33_554_432


@pytest.mark.parametrize(
    "stdout, stderr",
    [
        ("no result", ""),
        (
            (
                "perplexity: calculating perplexity over 6 chunks\n"
                "Final estimate: PPL = 7.1 +/- 0.1\n"
                "Final estimate: PPL = 7.2 +/- 0.1"
            ),
            "",
        ),
        (
            "perplexity: calculating perplexity over 6 chunks\nFinal estimate: PPL = 7.1 +/- 0.1",
            "build = 1 (abc)\nbuild = 2 (def)",
        ),
    ],
)
def test_parse_rejects_missing_duplicate_or_conflicting_evidence(stdout: str, stderr: str) -> None:
    capture = _load_capture()

    with pytest.raises(ValueError):
        capture._parse_result(stdout, stderr)
