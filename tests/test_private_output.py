from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

import model_skyline.private_output as private_output_module
from model_skyline.private_output import PrivateOutputError, write_private_text

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX output hardening")


def _staging_files(parent: Path) -> list[Path]:
    return list(parent.glob(".model-skyline-private-*.tmp"))


def test_private_text_is_no_clobber_atomic_and_mode_0600(tmp_path: Path) -> None:
    output = tmp_path / "private" / "report.json"

    assert write_private_text(output, "first\n") == output
    assert output.read_text(encoding="utf-8") == "first\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) & 0o077 == 0

    with pytest.raises(PrivateOutputError, match="refusing to overwrite"):
        write_private_text(output, "second\n")
    assert output.read_text(encoding="utf-8") == "first\n"

    write_private_text(output, "second\n", overwrite=True)
    assert output.read_text(encoding="utf-8") == "second\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not _staging_files(output.parent)


def test_private_text_rejects_symlink_target_and_parent(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    output = tmp_path / "report.json"
    output.symlink_to(victim)

    with pytest.raises(PrivateOutputError, match="symbolic link"):
        write_private_text(output, "attacker\n", overwrite=True)
    assert victim.read_text(encoding="utf-8") == "unchanged\n"
    assert output.is_symlink()

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(PrivateOutputError, match="symbolic link"):
        write_private_text(linked_parent / "report.json", "attacker\n")
    assert not (real_parent / "report.json").exists()


def test_private_text_failed_overwrite_keeps_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "report.json"
    write_private_text(output, "first\n")

    def fail_replace(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(private_output_module.os, "replace", fail_replace)
    with pytest.raises(PrivateOutputError, match="injected replace failure"):
        write_private_text(output, "second\n", overwrite=True)

    assert output.read_text(encoding="utf-8") == "first\n"
    assert not _staging_files(tmp_path)


def test_private_text_enforces_utf8_byte_limit_before_touching_filesystem(
    tmp_path: Path,
) -> None:
    output = tmp_path / "missing" / "report.json"

    with pytest.raises(PrivateOutputError, match="byte limit"):
        write_private_text(output, "too large", max_bytes=3)

    assert not output.parent.exists()
