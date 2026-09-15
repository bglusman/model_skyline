from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
ROUTER = ROOT / "examples" / "local-runtime-frontiers" / "inference-vm-router"


def test_ollama_override_is_loopback_only() -> None:
    override = (ROUTER / "ollama-model-skyline.conf").read_text(encoding="utf-8")

    assert "Environment=OLLAMA_HOST=127.0.0.1:11434" in override
    assert "0.0.0.0:11434" not in override
    assert "Environment=OLLAMA_MAX_LOADED_MODELS=1" in override


def test_exclusive_wrapper_checks_cuda_after_lock_and_before_exec() -> None:
    wrapper = ROUTER / "local-model-exclusive"
    script = wrapper.read_text(encoding="utf-8")

    lock_position = script.index("/usr/bin/flock -x 9")
    check_position = script.index("--query-compute-apps=pid")
    launch_position = script.rindex('exec "$@"')
    assert lock_position < check_position < launch_position
    assert "CUDA compute process already owns the GPU" in script
    assert "exit 75" in script


def test_router_shell_helpers_are_executable_and_parse() -> None:
    for name in ("local-model-exclusive", "verify-ollama-loopback"):
        path = ROUTER / name
        assert os.access(path, os.X_OK)
        result = subprocess.run(
            ["sh", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_loopback_verifier_checks_effective_state_without_private_addresses() -> None:
    script = (ROUTER / "verify-ollama-loopback").read_text(encoding="utf-8")

    assert "systemctl show" in script
    assert "OLLAMA_HOST=127.0.0.1:11434" in script
    assert "ss -H -ltn" in script
    assert "ss -H -tn" in script
    assert "awk '{print $4}'" in script
    assert "192.168." not in script
