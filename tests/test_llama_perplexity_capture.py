from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "capture_llama_perplexity.py"


def _load_capture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_llama_perplexity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_final_perplexity_and_runtime_identity() -> None:
    capture = _load_capture()
    result = capture._parse_result(
        "Final estimate: PPL = 7.1234 +/- 0.05678\n",
        "build = 10809 (5266f24da)\n",
    )

    assert result == {
        "perplexity": "7.1234",
        "standard_error": "0.05678",
        "runtime_build_number": 10809,
        "runtime_commit": "5266f24da",
    }


def test_parse_current_llama_cpp_version_format() -> None:
    capture = _load_capture()

    result = capture._parse_result(
        "Final estimate: PPL = 7.1234 +/- 0.05678\n",
        "version: 0.4.0 (build 10809, commit 5266f24da)\n",
    )

    assert result["runtime_build_number"] == 10809
    assert result["runtime_commit"] == "5266f24da"


@pytest.mark.parametrize(
    "stdout, stderr",
    [
        ("no result", ""),
        (
            "Final estimate: PPL = 7.1 +/- 0.1\nFinal estimate: PPL = 7.2 +/- 0.1",
            "",
        ),
        ("Final estimate: PPL = 7.1 +/- 0.1", "build = 1 (abc)\nbuild = 2 (def)"),
    ],
)
def test_parse_rejects_missing_duplicate_or_conflicting_evidence(
    stdout: str, stderr: str
) -> None:
    capture = _load_capture()

    with pytest.raises(ValueError):
        capture._parse_result(stdout, stderr)
