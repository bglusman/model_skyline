from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "examples" / "local-runtime-frontiers" / "capture_lock.py"


def _load_capture_lock() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_lock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_macos_uses_bsd_lockf(tmp_path: Path) -> None:
    capture_lock = _load_capture_lock()
    lockf = _executable(tmp_path / "lockf")
    lock = tmp_path / "runner.lock"

    command, coordination = capture_lock.coordinated_command(
        ["llama-bench", "-m", "model.gguf"],
        lock=lock,
        timeout_seconds=7,
        platform_name="darwin",
        lockf_path=lockf,
    )

    assert command == [
        str(lockf),
        "-k",
        "-t",
        "7",
        str(lock),
        "llama-bench",
        "-m",
        "model.gguf",
    ]
    assert coordination == {
        "method": "bsd-lockf",
        "lock_file": "${LOCAL_MODEL_RUNNER_LOCK}",
        "timeout_seconds": 7,
        "scope": "complete benchmark child-process lifetime",
    }


def test_linux_uses_flock(tmp_path: Path) -> None:
    capture_lock = _load_capture_lock()
    flock = _executable(tmp_path / "flock")
    lock = tmp_path / "runner.lock"

    command, coordination = capture_lock.coordinated_command(
        ["llama-perplexity"],
        lock=lock,
        timeout_seconds=0,
        platform_name="linux",
        flock_path=flock,
    )

    assert command == [
        str(flock),
        "--exclusive",
        "--wait",
        "0",
        str(lock),
        "llama-perplexity",
    ]
    assert coordination["method"] == "linux-flock"
    assert coordination["timeout_seconds"] == 0


@pytest.mark.parametrize(
    ("platform_name", "message"),
    [
        ("darwin", "requires /usr/bin/lockf on macOS"),
        ("linux", "requires /usr/bin/flock on Linux"),
        ("win32", "unsupported on platform 'win32'"),
    ],
)
def test_missing_or_unsupported_lock_tool_fails_closed(
    tmp_path: Path,
    platform_name: str,
    message: str,
) -> None:
    capture_lock = _load_capture_lock()

    with pytest.raises(capture_lock.LockToolUnavailable, match=message):
        capture_lock.coordinated_command(
            ["benchmark"],
            lock=tmp_path / "runner.lock",
            timeout_seconds=0,
            platform_name=platform_name,
            lockf_path=tmp_path / "missing-lockf",
            flock_path=tmp_path / "missing-flock",
        )
