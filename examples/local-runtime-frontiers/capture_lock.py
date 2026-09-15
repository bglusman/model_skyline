"""Build a platform-native command that holds one benchmark runner lock."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


class LockToolUnavailable(RuntimeError):
    """The host does not provide the platform's expected lock command."""


def _usable_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def coordinated_command(
    command: list[str],
    *,
    lock: Path,
    timeout_seconds: int,
    platform_name: str | None = None,
    lockf_path: Path = Path("/usr/bin/lockf"),
    flock_path: Path = Path("/usr/bin/flock"),
) -> tuple[list[str], dict[str, Any]]:
    """Wrap ``command`` with BSD ``lockf`` or Linux ``flock``.

    Both tools hold the advisory lock for the complete child-process lifetime.
    The caller owns path and timeout validation so argparse can report errors in
    the style of the containing capture command.
    """

    host_platform = sys.platform if platform_name is None else platform_name
    if host_platform == "darwin":
        if not _usable_executable(lockf_path):
            raise LockToolUnavailable("--exclusive-lock requires /usr/bin/lockf on macOS")
        wrapped = [
            str(lockf_path),
            "-k",
            "-t",
            str(timeout_seconds),
            str(lock),
            *command,
        ]
        method = "bsd-lockf"
    elif host_platform.startswith("linux"):
        if not _usable_executable(flock_path):
            raise LockToolUnavailable("--exclusive-lock requires /usr/bin/flock on Linux")
        wrapped = [
            str(flock_path),
            "--exclusive",
            "--wait",
            str(timeout_seconds),
            str(lock),
            *command,
        ]
        method = "linux-flock"
    else:
        raise LockToolUnavailable(f"--exclusive-lock is unsupported on platform {host_platform!r}")
    return (
        wrapped,
        {
            "method": method,
            "lock_file": "${LOCAL_MODEL_RUNNER_LOCK}",
            "timeout_seconds": timeout_seconds,
            "scope": "complete benchmark child-process lifetime",
        },
    )
