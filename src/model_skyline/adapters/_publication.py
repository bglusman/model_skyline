"""Hardened publication for adapter-generated, multi-file bundles.

Adapter output is a small release artifact, not a collection of independent
files.  Build it out of view, write the manifest last as its commit marker, and
publish the directory only after every payload is durable enough to expose.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path


class BundlePublicationError(RuntimeError):
    """A complete adapter output bundle could not be published safely."""


def _render_payloads(
    files: Mapping[str, str],
    *,
    manifest_name: str,
) -> tuple[tuple[str, bytes], ...]:
    if not files:
        raise BundlePublicationError("output bundle must contain at least one file")
    if manifest_name not in files:
        raise BundlePublicationError(f"output bundle is missing manifest {manifest_name!r}")

    rendered: list[tuple[str, bytes]] = []
    for name, content in files.items():
        if not isinstance(name, str) or not name or name in {".", ".."}:
            raise BundlePublicationError("output filenames must be non-empty strings")
        if "\x00" in name or Path(name).name != name or "/" in name or "\\" in name:
            raise BundlePublicationError(f"output filename must not contain a path: {name!r}")
        if not isinstance(content, str):
            raise BundlePublicationError(f"rendered output {name!r} must be text")
        try:
            payload = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BundlePublicationError(
                f"rendered output {name!r} is not valid UTF-8 text"
            ) from exc
        rendered.append((name, payload))

    # A manifest is the bundle's commit marker even while inspecting a staging
    # directory left behind by an interrupted process.
    return tuple(item for item in rendered if item[0] != manifest_name) + (
        (manifest_name, dict(rendered)[manifest_name]),
    )


def _absolute_path_without_following_links(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise BundlePublicationError(f"invalid output directory {path}: {exc}") from exc


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BundlePublicationError(f"cannot inspect output path {path}: {exc}") from exc


def _reject_link_components(path: Path) -> None:
    """Reject every existing symlink component without resolving through it."""

    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        status = _lstat(current)
        if status is None:
            # Descendants cannot exist after the first missing component.
            return
        if stat.S_ISLNK(status.st_mode):
            raise BundlePublicationError(f"output path contains symbolic link component: {current}")
        if current != path and not stat.S_ISDIR(status.st_mode):
            raise BundlePublicationError(f"output path component is not a directory: {current}")


def _validate_directory_contents(
    directory: Path,
    *,
    expected_names: tuple[str, ...],
    overwrite: bool,
) -> bool:
    status = _lstat(directory)
    if status is None:
        return False
    if stat.S_ISLNK(status.st_mode):
        raise BundlePublicationError(f"output directory cannot be a symbolic link: {directory}")
    if not stat.S_ISDIR(status.st_mode):
        raise BundlePublicationError(f"output path is not a directory: {directory}")

    expected = set(expected_names)
    entries: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise BundlePublicationError(f"cannot inspect output directory {directory}: {exc}") from exc

    unmanaged: list[str] = []
    existing: list[str] = []
    for entry in entries:
        entry_status = _lstat(entry)
        if entry_status is None:
            raise BundlePublicationError(f"output entry disappeared during inspection: {entry}")
        if stat.S_ISLNK(entry_status.st_mode):
            raise BundlePublicationError(f"output target cannot be a symbolic link: {entry}")
        if entry.name not in expected:
            unmanaged.append(entry.name)
        elif not stat.S_ISREG(entry_status.st_mode):
            raise BundlePublicationError(f"output target is not a regular file: {entry}")
        else:
            existing.append(entry.name)

    if unmanaged:
        names = ", ".join(sorted(unmanaged))
        raise BundlePublicationError(
            "refusing to replace output directory containing unmanaged entries: " + names
        )
    if existing and not overwrite:
        ordered = [name for name in expected_names if name in existing]
        raise BundlePublicationError(
            "refusing to overwrite existing output files: " + ", ".join(ordered)
        )
    return True


def _explicit_mode_open_flags() -> int:
    """Return fail-closed flags for payloads that promise an exact mode."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name != "posix" or nofollow == 0 or not hasattr(os, "fchmod"):
        raise BundlePublicationError(
            "explicit bundle file modes require POSIX no-follow file creation"
        )
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | getattr(os, "O_CLOEXEC", 0)


def _write_payload(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    if mode is None:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return

    descriptor = os.open(path, _explicit_mode_open_flags(), mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            os.fchmod(handle.fileno(), mode)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_mode(value: int | None, *, label: str, optional: bool = False) -> None:
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0o777:
        raise BundlePublicationError(
            f"{label} must be an integer permission mode from 0000 to 0777"
        )


def _sync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_owned_tree(path: Path) -> None:
    status = _lstat(path)
    if status is None:
        return
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise BundlePublicationError(f"refusing to clean unexpected staging path: {path}")
    shutil.rmtree(path)


def _reserve_absent_sibling(parent: Path, *, prefix: str) -> Path:
    # tempfile chooses an unpredictable same-filesystem name.  The final rename
    # needs an absent destination, so immediately remove the empty reservation.
    reserved = Path(tempfile.mkdtemp(dir=parent, prefix=prefix))
    reserved.rmdir()
    return reserved


def publish_text_bundle(
    output_directory: str | Path,
    files: Mapping[str, str],
    *,
    manifest_name: str,
    overwrite: bool = False,
    directory_mode: int = 0o755,
    file_mode: int | None = None,
) -> tuple[Path, ...]:
    """Publish a flat text bundle atomically at the directory boundary.

    All rendering is supplied and UTF-8 encoded before the filesystem is
    touched.  Existing directories must be empty or contain only this bundle's
    regular files; this lets overwrite replace the whole managed directory
    without deleting unrelated user data.  ``file_mode=None`` preserves the
    existing umask-derived adapter default; callers handling private evidence
    can request exact restrictive file and directory modes.  Explicit file
    modes require POSIX no-follow creation and fail before filesystem changes
    when that capability is unavailable.
    """

    _validate_mode(directory_mode, label="directory_mode")
    _validate_mode(file_mode, label="file_mode", optional=True)
    # An explicit mode is used for private adapter bundles.  Validate the
    # security primitive before creating a parent or staging directory; the
    # default, umask-derived public path remains portable.
    if file_mode is not None:
        _explicit_mode_open_flags()
    rendered = _render_payloads(files, manifest_name=manifest_name)
    expected_names = tuple(name for name, _payload in rendered)
    requested_directory = Path(output_directory)
    directory = _absolute_path_without_following_links(requested_directory)
    if directory == Path(directory.anchor):
        raise BundlePublicationError("refusing to publish an output bundle at a filesystem root")

    _reject_link_components(directory)
    parent = directory.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BundlePublicationError(f"cannot create output parent {parent}: {exc}") from exc
    _reject_link_components(directory)
    _validate_directory_contents(
        directory,
        expected_names=expected_names,
        overwrite=overwrite,
    )

    stage: Path | None = None
    backup: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(dir=parent, prefix=".model-skyline-stage-"))
        for name, payload in rendered:
            if file_mode is None:
                _write_payload(stage / name, payload)
            else:
                _write_payload(stage / name, payload, mode=file_mode)
        # mkdtemp starts private.  Match a conventional project directory only
        # after its complete contents, including the last-written manifest, exist.
        stage.chmod(directory_mode)
        _sync_directory(stage)

        # Close the inspection-to-publication window as far as portable Python
        # permits.  A changed target is rejected instead of followed or merged.
        _reject_link_components(directory)
        existed = _validate_directory_contents(
            directory,
            expected_names=expected_names,
            overwrite=overwrite,
        )
        if existed:
            backup = _reserve_absent_sibling(
                parent,
                prefix=".model-skyline-backup-",
            )
            os.replace(directory, backup)
            try:
                _validate_directory_contents(
                    backup,
                    expected_names=expected_names,
                    overwrite=True,
                )
            except BundlePublicationError as validation_error:
                try:
                    os.replace(backup, directory)
                    backup = None
                except OSError as restore_error:
                    recovery_copy = backup
                    backup = None
                    raise BundlePublicationError(
                        "output changed during publication and the previous directory could "
                        f"not be restored; recovery copy remains at {recovery_copy}: "
                        f"{restore_error}"
                    ) from validation_error
                raise

        try:
            os.replace(stage, directory)
            stage = None
        except OSError as publish_error:
            publication_rollback_error: OSError | None = None
            if backup is not None:
                try:
                    os.replace(backup, directory)
                    backup = None
                except OSError as exc:
                    publication_rollback_error = exc
            if publication_rollback_error is not None:
                recovery_copy = backup
                backup = None
                raise BundlePublicationError(
                    "cannot publish output bundle and cannot restore the previous bundle; "
                    f"recovery copy remains at {recovery_copy}: {publication_rollback_error}"
                ) from publish_error
            raise BundlePublicationError(
                f"cannot publish complete output bundle to {directory}: {publish_error}"
            ) from publish_error

        _sync_directory(parent)
        if backup is not None:
            _remove_owned_tree(backup)
            backup = None
            _sync_directory(parent)
    except BundlePublicationError:
        raise
    except OSError as exc:
        raise BundlePublicationError(f"cannot publish output bundle to {directory}: {exc}") from exc
    finally:
        cleanup_errors: list[str] = []
        for transient in (stage, backup):
            if transient is None:
                continue
            try:
                _remove_owned_tree(transient)
            except (BundlePublicationError, OSError) as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            # Cleanup failure is exceptional and must not be hidden.  Raising
            # here deliberately preserves a complete live or restored bundle.
            raise BundlePublicationError(
                "cannot clean publication staging: " + "; ".join(cleanup_errors)
            )

    return tuple(requested_directory / name for name, _payload in rendered)
