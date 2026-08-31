from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, cast

import pytest

from model_skyline.adapters import _publication
from model_skyline.adapters._publication import (
    BundlePublicationError,
    publish_text_bundle,
)


def _bundle(value: str) -> dict[str, str]:
    return {
        "data.txt": value + "\n",
        "import.json": '{"value":"' + value + '"}\n',
    }


def _transient_paths(parent: Path) -> list[Path]:
    return [
        path
        for path in parent.iterdir()
        if path.name.startswith((".model-skyline-stage-", ".model-skyline-backup-"))
    ]


def test_stages_complete_bundle_and_writes_manifest_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bundle"
    writes: list[str] = []
    original_write = _publication._write_payload

    def recording_write(path: Path, payload: bytes) -> None:
        writes.append(path.name)
        original_write(path, payload)

    monkeypatch.setattr(_publication, "_write_payload", recording_write)

    targets = publish_text_bundle(output, _bundle("new"), manifest_name="import.json")

    assert writes == ["data.txt", "import.json"]
    assert targets == (output / "data.txt", output / "import.json")
    assert (output / "data.txt").read_text(encoding="utf-8") == "new\n"
    assert not _transient_paths(tmp_path)


def test_render_failure_does_not_touch_filesystem(tmp_path: Path) -> None:
    output = tmp_path / "new-parent" / "bundle"
    invalid = cast(dict[str, str], {"data.txt": object(), "import.json": "{}\n"})

    with pytest.raises(BundlePublicationError, match="must be text"):
        publish_text_bundle(output, invalid, manifest_name="import.json")

    assert not output.parent.exists()


def test_stage_failure_leaves_no_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bundle"
    original_write = _publication._write_payload

    def failing_write(path: Path, payload: bytes) -> None:
        if path.name == "import.json":
            raise OSError("injected manifest write failure")
        original_write(path, payload)

    monkeypatch.setattr(_publication, "_write_payload", failing_write)

    with pytest.raises(BundlePublicationError, match="injected manifest write failure"):
        publish_text_bundle(output, _bundle("new"), manifest_name="import.json")

    assert not output.exists()
    assert not _transient_paths(tmp_path)


def test_overwrite_publish_failure_restores_previous_complete_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bundle"
    publish_text_bundle(output, _bundle("old"), manifest_name="import.json")
    original_replace = os.replace

    def failing_publish(source: Any, destination: Any) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path.name.startswith(".model-skyline-stage-")
            and destination_path == output.absolute()
        ):
            raise OSError("injected directory publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(_publication.os, "replace", failing_publish)

    with pytest.raises(BundlePublicationError, match="cannot publish complete output bundle"):
        publish_text_bundle(
            output,
            _bundle("new"),
            manifest_name="import.json",
            overwrite=True,
        )

    assert (output / "data.txt").read_text(encoding="utf-8") == "old\n"
    assert (output / "import.json").read_text(encoding="utf-8") == '{"value":"old"}\n'
    assert not _transient_paths(tmp_path)


def test_overwrite_refuses_to_delete_unmanaged_entries(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    publish_text_bundle(output, _bundle("old"), manifest_name="import.json")
    (output / "operator-notes.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(BundlePublicationError, match="unmanaged entries"):
        publish_text_bundle(
            output,
            _bundle("new"),
            manifest_name="import.json",
            overwrite=True,
        )

    assert (output / "data.txt").read_text(encoding="utf-8") == "old\n"
    assert (output / "operator-notes.txt").read_text(encoding="utf-8") == "keep me\n"


def test_rejects_symlink_target_and_parent_component(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "data.txt").symlink_to(victim)

    with pytest.raises(BundlePublicationError, match="symbolic link"):
        publish_text_bundle(
            output,
            _bundle("new"),
            manifest_name="import.json",
            overwrite=True,
        )
    assert victim.read_text(encoding="utf-8") == "unchanged\n"

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(BundlePublicationError, match="symbolic link component"):
        publish_text_bundle(
            linked_parent / "other-bundle",
            _bundle("new"),
            manifest_name="import.json",
        )
    assert not (real_parent / "other-bundle").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission modes")
def test_bundle_supports_exact_private_directory_and_file_modes(tmp_path: Path) -> None:
    output = tmp_path / "private-bundle"

    targets = publish_text_bundle(
        output,
        _bundle("private"),
        manifest_name="import.json",
        directory_mode=0o700,
        file_mode=0o600,
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(target.stat().st_mode) == 0o600 for target in targets)


def test_explicit_file_mode_fails_before_filesystem_without_nofollow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "new-parent" / "private-bundle"
    monkeypatch.delattr(_publication.os, "O_NOFOLLOW", raising=False)

    with pytest.raises(BundlePublicationError, match="POSIX no-follow file creation"):
        publish_text_bundle(
            output,
            _bundle("private"),
            manifest_name="import.json",
            directory_mode=0o700,
            file_mode=0o600,
        )

    assert not output.parent.exists()


def test_default_public_bundle_does_not_require_nofollow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "public-bundle"
    monkeypatch.delattr(_publication.os, "O_NOFOLLOW", raising=False)

    targets = publish_text_bundle(output, _bundle("public"), manifest_name="import.json")

    assert targets == (output / "data.txt", output / "import.json")
