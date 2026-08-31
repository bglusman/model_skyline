"""Race-resistant publication for sensitive single-file CLI output."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path

MAX_PRIVATE_TEXT_BYTES = 64_000_000
_TEMP_ATTEMPTS = 128


class PrivateOutputError(RuntimeError):
    """Sensitive output could not be published without weakening safety."""


def _absolute_path(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise PrivateOutputError(f"invalid private output path {path}: {exc}") from exc


def _entry_status(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PrivateOutputError(f"cannot inspect private output entry {name!r}: {exc}") from exc


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if os.name != "posix" or nofollow == 0 or directory == 0:
        raise PrivateOutputError(
            "secure private output requires POSIX no-follow directory operations"
        )
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _open_private_parent(path: Path) -> int:
    """Open and pin the parent, creating absent descendants no broader than 0700."""

    flags = _directory_flags()
    try:
        descriptor = os.open(path.anchor, flags)
    except OSError as exc:
        raise PrivateOutputError(f"cannot open private output filesystem root: {exc}") from exc

    try:
        for component in path.parts[1:]:
            status = _entry_status(descriptor, component)
            if status is None:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    # Another actor won the creation race.  The no-follow open
                    # and fstat below still decide whether it is acceptable.
                    pass
                except OSError as exc:
                    raise PrivateOutputError(
                        f"cannot create private output parent component {component!r}: {exc}"
                    ) from exc
            elif stat.S_ISLNK(status.st_mode):
                raise PrivateOutputError(
                    f"private output parent component is a symbolic link: {component!r}"
                )
            elif not stat.S_ISDIR(status.st_mode):
                raise PrivateOutputError(
                    f"private output parent component is not a directory: {component!r}"
                )

            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise PrivateOutputError(
                    f"cannot securely open private output parent component {component!r}: {exc}"
                ) from exc
            child_status = os.fstat(child)
            if not stat.S_ISDIR(child_status.st_mode):  # pragma: no cover - O_DIRECTORY guards it
                os.close(child)
                raise PrivateOutputError(
                    f"private output parent component is not a directory: {component!r}"
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _create_private_temporary(parent_descriptor: int) -> tuple[int, str]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _attempt in range(_TEMP_ATTEMPTS):
        name = f".model-skyline-private-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise PrivateOutputError(f"cannot create private output staging file: {exc}") from exc
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            os.close(descriptor)
            with suppress(OSError):
                os.unlink(name, dir_fd=parent_descriptor)
            raise
        return descriptor, name
    raise PrivateOutputError("cannot allocate a unique private output staging file")


def _target_identity(status: os.stat_result | None) -> tuple[int, int] | None:
    if status is None:
        return None
    return status.st_dev, status.st_ino


def _validate_target(status: os.stat_result | None, *, overwrite: bool, path: Path) -> None:
    if status is None:
        return
    if stat.S_ISLNK(status.st_mode):
        raise PrivateOutputError(f"private output target cannot be a symbolic link: {path}")
    if not stat.S_ISREG(status.st_mode):
        raise PrivateOutputError(f"private output target must be a regular file: {path}")
    if not overwrite:
        raise PrivateOutputError(f"refusing to overwrite existing private output: {path}")


def write_private_text(
    path: str | Path,
    value: str,
    *,
    overwrite: bool = False,
    max_bytes: int = MAX_PRIVATE_TEXT_BYTES,
) -> Path:
    """Atomically publish bounded UTF-8 text as a mode-0600 regular file.

    The destination and every parent component are accessed without following
    symbolic links.  The default commit is no-clobber; ``overwrite=True`` is an
    explicit authorization to atomically replace the named regular file.
    """

    if not isinstance(value, str):
        raise PrivateOutputError("private output must be text")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise PrivateOutputError("private output byte limit must be a positive integer")
    try:
        payload = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PrivateOutputError("private output is not valid UTF-8 text") from exc
    if len(payload) > max_bytes:
        raise PrivateOutputError(f"private output exceeds the {max_bytes}-byte limit")

    requested = Path(path)
    target = _absolute_path(requested)
    if target == Path(target.anchor):
        raise PrivateOutputError("private output target cannot be a filesystem root")
    parent_descriptor = _open_private_parent(target.parent)
    temporary_name: str | None = None
    staged_identity: tuple[int, int] | None = None
    cleanup_error: OSError | None = None
    try:
        initial = _entry_status(parent_descriptor, target.name)
        _validate_target(initial, overwrite=overwrite, path=requested)
        descriptor, temporary_name = _create_private_temporary(parent_descriptor)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as staging:
                if staging.write(payload) != len(payload):
                    raise PrivateOutputError(f"cannot completely stage private output {requested}")
                staging.flush()
                os.fsync(staging.fileno())
                staged_status = os.fstat(staging.fileno())
                staged_identity = (staged_status.st_dev, staged_status.st_ino)
        except OSError as exc:
            raise PrivateOutputError(f"cannot stage private output {requested}: {exc}") from exc

        current = _entry_status(parent_descriptor, target.name)
        _validate_target(current, overwrite=overwrite, path=requested)
        if _target_identity(current) != _target_identity(initial):
            raise PrivateOutputError(
                f"private output target changed during publication: {requested}"
            )

        if overwrite:
            try:
                os.replace(
                    temporary_name,
                    target.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise PrivateOutputError(
                    f"cannot atomically replace private output {requested}: {exc}"
                ) from exc
            temporary_name = None
        else:
            try:
                os.link(
                    temporary_name,
                    target.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise PrivateOutputError(
                    f"refusing to overwrite existing private output: {requested}"
                ) from None
            except OSError as exc:
                raise PrivateOutputError(
                    f"cannot atomically create private output {requested}: {exc}"
                ) from exc
            os.unlink(temporary_name, dir_fd=parent_descriptor)
            temporary_name = None

        installed = _entry_status(parent_descriptor, target.name)
        if installed is None or not stat.S_ISREG(installed.st_mode):
            raise PrivateOutputError(f"private output was not installed safely: {requested}")
        if _target_identity(installed) != staged_identity:
            raise PrivateOutputError(f"private output changed during installation: {requested}")
        if stat.S_IMODE(installed.st_mode) != 0o600:
            raise PrivateOutputError(f"private output does not have mode 0600: {requested}")
        os.fsync(parent_descriptor)
    except PrivateOutputError:
        raise
    except OSError as exc:
        raise PrivateOutputError(f"cannot publish private output {requested}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_error = exc
        os.close(parent_descriptor)
        if cleanup_error is not None:
            raise PrivateOutputError(
                f"cannot clean private output staging file: {cleanup_error}"
            ) from cleanup_error
    return requested
