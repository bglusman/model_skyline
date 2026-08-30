"""Static publication orchestration for frontiers, feeds, and agent selections.

The root ``latest.json`` manifest is the cross-file commit marker. Immutable
artifacts are made durable first, mutable aliases are replaced one file at a
time, and the manifest is replaced last. Readers that need a coherent project
view start from the manifest; a runtime resolving one selection always sees a
complete old or new JSON file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from model_skyline.canonical import content_hash
from model_skyline.engine import FrontierEngine, catalog_hash, frontier_hash_matches
from model_skyline.io import dump_json
from model_skyline.models import (
    PORTABLE_PUBLICATION_ID_PATTERN,
    FrontierHistory,
    FrontierHistoryEntry,
    FrontierSnapshot,
    ObservationCatalog,
    ProjectConfig,
    PublicationManifest,
    PublicationPolicy,
    PublishedCatalog,
    PublishedFile,
    PublishedFrontier,
    PublishedSelection,
    SelectionSnapshot,
    SourceReference,
)
from model_skyline.renderers import frontier_view, render_csv, render_rss_history, render_table
from model_skyline.selection import select_models, selection_hash_matches

MAX_EXISTING_FILES = 100_000
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_HISTORY_ENTRIES = 10_000
MAX_RSS_ITEMS = 1_000
_PORTABLE_ID_RE = re.compile(PORTABLE_PUBLICATION_ID_PATTERN)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PublicationError(RuntimeError):
    """A publication set could not be validated or committed safely."""


@dataclass(frozen=True, slots=True)
class PublicationResult:
    manifest: PublicationManifest
    manifest_path: Path
    changed: bool


ModelT = TypeVar("ModelT", bound=BaseModel)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def publication_hash(manifest: PublicationManifest) -> str:
    """Recompute a publication manifest's content identity."""

    return content_hash(manifest.model_dump(mode="json", exclude={"publication_id"}))


def _axis_hash(snapshot: FrontierSnapshot) -> str:
    return content_hash(
        {
            "workload": snapshot.workload.model_dump(mode="json"),
            "order_by": snapshot.order_by,
            "uncertainty": snapshot.uncertainty,
            "axes": [axis.model_dump(mode="json") for axis in snapshot.axes],
        }
    )


def _view_hash(snapshot: FrontierSnapshot) -> str:
    return content_hash(frontier_view(snapshot))


def _explicit_null_billing_mode_view_hash(snapshot: FrontierSnapshot) -> str:
    value = tuple(
        (
            item.offering.model_dump(mode="json"),
            tuple(
                (
                    axis.metric,
                    item.axes[axis.metric].model_dump(
                        mode="json",
                        include={"value", "lower", "upper"},
                    ),
                )
                for axis in snapshot.axes
            ),
            item.metadata,
        )
        for item in snapshot.members
    )
    return content_hash(value)


def _view_hash_matches(snapshot: FrontierSnapshot, expected: str) -> bool:
    return expected in {
        _view_hash(snapshot),
        _explicit_null_billing_mode_view_hash(snapshot),
    }


def _portable_id(value: str, *, kind: str) -> str:
    if not _PORTABLE_ID_RE.fullmatch(value):
        raise PublicationError(
            f"{kind} {value!r} is not a portable lowercase publication path segment"
        )
    return value


def _artifact_url(base_url: str | None, relative_path: str) -> str | None:
    if base_url is None:
        return None
    return f"{base_url}/{relative_path}"


def _normalized_base_url(value: str | None, *, public: bool) -> str | None:
    if value is None:
        if public:
            raise PublicationError("public publication requires --base-url")
        return None
    if len(value) > 2083:
        raise PublicationError("base URL exceeds 2083 characters")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise PublicationError("base URL must be an absolute HTTP(S) URL")
    if public and parsed.scheme != "https":
        raise PublicationError("public publication requires an HTTPS base URL")
    if parsed.username is not None or parsed.password is not None:
        raise PublicationError("base URL cannot contain user information")
    if parsed.query or parsed.fragment:
        raise PublicationError("base URL cannot contain a query string or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _absolute_path_without_following_links(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise PublicationError(f"invalid publication directory {path}: {exc}") from exc


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PublicationError(f"cannot inspect publication path {path}: {exc}") from exc


def _walk_error(exc: OSError) -> None:
    location = exc.filename or "publication tree"
    raise PublicationError(f"cannot traverse publication path {location}: {exc}") from exc


def _reject_link_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        status = _lstat(current)
        if status is None:
            return
        if stat.S_ISLNK(status.st_mode):
            raise PublicationError(f"publication path contains a symbolic link: {current}")
        if current != path and not stat.S_ISDIR(status.st_mode):
            raise PublicationError(f"publication path component is not a directory: {current}")


def _sync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_root(output_directory: str | Path) -> Path:
    root = _absolute_path_without_following_links(Path(output_directory))
    if root == Path(root.anchor):
        raise PublicationError("refusing to publish at a filesystem root")
    _reject_link_components(root)
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PublicationError(f"cannot create publication parent {root.parent}: {exc}") from exc
    _reject_link_components(root.parent)
    try:
        root.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise PublicationError(f"cannot create publication directory {root}: {exc}") from exc
    status = _lstat(root)
    if status is None or stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise PublicationError("publication root must be a real directory")
    parent_status = _lstat(root.parent)
    if parent_status is None or parent_status.st_dev != status.st_dev:
        raise PublicationError(
            "publication root must share a filesystem with its parent for atomic staging"
        )
    return root


@contextmanager
def _writer_lock(root: Path) -> Iterator[None]:
    lock = root.parent / f".{root.name}.model-skyline-publish.lock"
    flags = os.O_CREAT | os.O_RDWR
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock, flags | nofollow, 0o600)
    except OSError as exc:
        raise PublicationError(f"cannot acquire publication lock {lock}: {exc}") from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise PublicationError(f"publication lock is not a regular file: {lock}")
        if os.name == "nt":  # pragma: no cover - exercised on Windows
            windows_lock: Any = __import__("msvcrt")

            if status.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                windows_lock.locking(descriptor, windows_lock.LK_NBLCK, 1)
            except OSError as exc:
                raise PublicationError(f"publication lock is already held at {lock}") from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PublicationError(f"publication lock is already held at {lock}") from exc
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        payload = f"pid={os.getpid()}\n".encode()
        os.write(descriptor, payload)
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)


def _is_allowed_directory(parts: tuple[str, ...]) -> bool:
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0] in {"frontiers", "selections", "feeds", "publications"}
    return (
        len(parts) == 2
        and parts[0] in {"frontiers", "selections", "feeds"}
        and _PORTABLE_ID_RE.fullmatch(parts[1]) is not None
    )


def _is_allowed_file(parts: tuple[str, ...]) -> bool:
    if parts == ("latest.json",):
        return True
    if len(parts) == 2 and parts[0] == "feeds":
        stem = parts[1][:-4] if parts[1].endswith(".xml") else ""
        if _PORTABLE_ID_RE.fullmatch(stem) is not None:
            return True
        logical_id, separator, digest = stem.rpartition("-")
        return (
            bool(separator)
            and _PORTABLE_ID_RE.fullmatch(logical_id) is not None
            and _SHA256_RE.fullmatch(digest) is not None
        )
    if len(parts) == 3 and parts[0] == "feeds":
        return (
            _PORTABLE_ID_RE.fullmatch(parts[1]) is not None
            and parts[2].endswith(".xml")
            and _SHA256_RE.fullmatch(parts[2][:-4]) is not None
        )
    if len(parts) == 2 and parts[0] == "publications":
        return parts[1].endswith(".json") and _SHA256_RE.fullmatch(parts[1][:-5]) is not None
    if len(parts) != 3 or parts[0] not in {"frontiers", "selections"}:
        return False
    if _PORTABLE_ID_RE.fullmatch(parts[1]) is None:
        return False
    name = parts[2]
    if parts[0] == "frontiers" and name in {
        "latest.json",
        "history.json",
        "table.csv",
        "table.txt",
    }:
        return True
    if parts[0] == "selections" and name == "latest.json":
        return True
    if parts[0] == "frontiers" and name.startswith("history-") and name.endswith(".json"):
        return _SHA256_RE.fullmatch(name[len("history-") : -len(".json")]) is not None
    for suffix in (".json", ".csv", ".txt"):
        if name.endswith(suffix) and _SHA256_RE.fullmatch(name[: -len(suffix)]) is not None:
            return True
    return False


def _validate_existing_tree(root: Path) -> None:
    seen = 0
    for current_value, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=_walk_error,
    ):
        current = Path(current_value)
        relative = current.relative_to(root)
        parts = () if relative == Path(".") else relative.parts
        if not _is_allowed_directory(parts):
            raise PublicationError(f"unmanaged directory in publication root: {relative}")
        for name in directory_names:
            child = current / name
            status = _lstat(child)
            if status is None or stat.S_ISLNK(status.st_mode):
                raise PublicationError(f"publication directory cannot be a symbolic link: {child}")
            if not stat.S_ISDIR(status.st_mode):
                raise PublicationError(f"publication entry is not a directory: {child}")
            child_parts = (*parts, name)
            if not _is_allowed_directory(child_parts):
                raise PublicationError(
                    f"unmanaged directory in publication root: {PurePosixPath(*child_parts)}"
                )
        for name in file_names:
            seen += 1
            if seen > MAX_EXISTING_FILES:
                raise PublicationError(
                    f"publication root exceeds the {MAX_EXISTING_FILES}-file validation limit"
                )
            child = current / name
            status = _lstat(child)
            if status is None or stat.S_ISLNK(status.st_mode):
                raise PublicationError(f"publication file cannot be a symbolic link: {child}")
            if not stat.S_ISREG(status.st_mode):
                raise PublicationError(f"publication entry is not a regular file: {child}")
            child_parts = (*parts, name)
            if not _is_allowed_file(child_parts):
                raise PublicationError(
                    f"unmanaged file in publication root: {PurePosixPath(*child_parts)}"
                )


def _target(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise PublicationError(f"invalid relative artifact path {relative_path!r}")
    return root.joinpath(*relative.parts)


def _read_regular(path: Path) -> bytes:
    status = _lstat(path)
    if status is None:
        raise PublicationError(f"published artifact is missing: {path}")
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise PublicationError(f"published artifact is not a regular file: {path}")
    if status.st_size > MAX_ARTIFACT_BYTES:
        raise PublicationError(
            f"published artifact {path} exceeds the {MAX_ARTIFACT_BYTES}-byte limit"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PublicationError(f"cannot read published artifact {path}: {exc}") from exc


def _load_model(path: Path, model: type[ModelT]) -> ModelT:
    payload = _read_regular(path)
    try:
        value = json.loads(payload, parse_float=Decimal, parse_constant=Decimal)
        return model.model_validate(value)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise PublicationError(
            f"published artifact {path} is not a valid {model.__name__}: {exc}"
        ) from exc


def _published_file(path: str, payload: bytes, media_type: str) -> PublishedFile:
    return PublishedFile(path=path, sha256=_sha256(payload), media_type=media_type)


def _verify_file_reference(root: Path, reference: PublishedFile) -> bytes:
    payload = _read_regular(_target(root, reference.path))
    actual = _sha256(payload)
    if actual != reference.sha256:
        raise PublicationError(
            f"published artifact digest mismatch for {reference.path}: "
            f"expected {reference.sha256}, received {actual}"
        )
    return payload


def _immutable_manifest_files(manifest: PublicationManifest) -> Iterator[PublishedFile]:
    for frontier in manifest.frontiers:
        yield frontier.snapshot
        yield frontier.csv
        yield frontier.table
        yield frontier.history
        yield frontier.feed
    for selection in manifest.selections:
        yield selection.snapshot


def _validate_manifest_semantics(root: Path, manifest: PublicationManifest) -> None:
    catalogs = {entry.workload.id: entry for entry in manifest.catalogs}
    frontier_snapshots: dict[str, FrontierSnapshot] = {}
    for published_frontier in manifest.frontiers:
        path = _target(root, published_frontier.snapshot.path)
        frontier_snapshot = _load_model(path, FrontierSnapshot)
        catalog = catalogs.get(frontier_snapshot.workload.id)
        if (
            frontier_snapshot.frontier_id != published_frontier.frontier_id
            or frontier_snapshot.snapshot_id != published_frontier.snapshot_id
            or not frontier_hash_matches(frontier_snapshot)
            or frontier_snapshot.generated_at != manifest.generated_at
            or catalog is None
            or catalog.workload != frontier_snapshot.workload
            or catalog.catalog_hash != frontier_snapshot.catalog_hash
        ):
            raise PublicationError(
                f"published frontier {published_frontier.frontier_id!r} contradicts its manifest"
            )
        frontier_snapshots[published_frontier.frontier_id] = frontier_snapshot

    for published_selection in manifest.selections:
        path = _target(root, published_selection.snapshot.path)
        selection_snapshot = _load_model(path, SelectionSnapshot)
        frontier = frontier_snapshots.get(published_selection.frontier_id)
        if (
            selection_snapshot.selection_id != published_selection.selection_id
            or selection_snapshot.snapshot_id != published_selection.snapshot_id
            or not selection_hash_matches(selection_snapshot)
            or selection_snapshot.frontier_id != published_selection.frontier_id
            or selection_snapshot.frontier_snapshot_id != published_selection.frontier_snapshot_id
            or frontier is None
            or selection_snapshot.frontier_snapshot_id != frontier.snapshot_id
            or selection_snapshot.workload != frontier.workload
            or selection_snapshot.generated_at != manifest.generated_at
        ):
            raise PublicationError(
                f"published selection {published_selection.selection_id!r} contradicts its manifest"
            )


def _load_previous_manifest(root: Path, project_id: str) -> PublicationManifest | None:
    latest_path = root / "latest.json"
    if _lstat(latest_path) is None:
        return None
    manifest = _load_model(latest_path, PublicationManifest)
    if manifest.project_id != project_id:
        raise PublicationError(
            f"publication root belongs to project {manifest.project_id!r}, not {project_id!r}"
        )
    if publication_hash(manifest) != manifest.publication_id:
        raise PublicationError("root publication manifest has an invalid content identity")
    immutable = root / "publications" / f"{manifest.publication_id}.json"
    if _read_regular(immutable) != _read_regular(latest_path):
        raise PublicationError("root manifest is not byte-identical to its immutable publication")
    for reference in _immutable_manifest_files(manifest):
        _verify_file_reference(root, reference)
    _validate_manifest_semantics(root, manifest)
    return manifest


def _load_history_snapshot(
    root: Path,
    frontier_id: str,
    entry: FrontierHistoryEntry,
) -> FrontierSnapshot:
    payload = _verify_file_reference(root, entry.snapshot)
    path = _target(root, entry.snapshot.path)
    snapshot = _load_model(path, FrontierSnapshot)
    if (
        snapshot.frontier_id != frontier_id
        or snapshot.snapshot_id != entry.snapshot_id
        or not frontier_hash_matches(snapshot)
        or entry.generated_at != snapshot.generated_at
        or entry.workload != snapshot.workload
        or entry.config_hash != snapshot.config_hash
        or entry.catalog_hash != snapshot.catalog_hash
        or entry.axis_hash != _axis_hash(snapshot)
        or not _view_hash_matches(snapshot, entry.view_hash)
        or entry.snapshot.sha256 != _sha256(payload)
    ):
        raise PublicationError(
            f"frontier history metadata does not match snapshot {entry.snapshot_id}"
        )
    return snapshot


def _load_committed_histories(
    root: Path,
    manifest: PublicationManifest | None,
) -> dict[str, dict[str, FrontierSnapshot]]:
    if manifest is None:
        return {}
    histories: dict[str, dict[str, FrontierSnapshot]] = {}
    for published in manifest.frontiers:
        history_path = _target(root, published.history.path)
        history = _load_model(history_path, FrontierHistory)
        if history.frontier_id != published.frontier_id:
            raise PublicationError(
                f"frontier history identity does not match {published.frontier_id!r}"
            )
        if len(history.entries) > MAX_HISTORY_ENTRIES:
            raise PublicationError(
                f"frontier history exceeds the {MAX_HISTORY_ENTRIES}-entry limit"
            )
        snapshots = {
            entry.snapshot_id: _load_history_snapshot(root, published.frontier_id, entry)
            for entry in history.entries
        }
        if published.snapshot_id not in snapshots:
            raise PublicationError(
                f"committed history omits current snapshot {published.snapshot_id}"
            )
        histories[published.frontier_id] = snapshots
    return histories


def _validated_manifest_chain(
    root: Path,
    current: PublicationManifest | None,
) -> tuple[PublicationManifest, ...]:
    if current is None:
        return ()
    chain = [current]
    seen = {current.publication_id}
    child = current
    while child.previous_publication_id is not None:
        parent_id = child.previous_publication_id
        if parent_id in seen:
            raise PublicationError("publication manifest chain contains a cycle")
        if len(seen) >= MAX_HISTORY_ENTRIES:
            raise PublicationError("publication manifest chain exceeds the validation limit")
        path = root / "publications" / f"{parent_id}.json"
        parent = _load_model(path, PublicationManifest)
        if (
            parent.publication_id != parent_id
            or publication_hash(parent) != parent_id
            or parent.project_id != current.project_id
            or parent.generated_at > child.generated_at
        ):
            raise PublicationError(f"invalid historical publication manifest {parent_id}")
        for reference in _immutable_manifest_files(parent):
            _verify_file_reference(root, reference)
        _validate_manifest_semantics(root, parent)
        seen.add(parent_id)
        chain.append(parent)
        child = parent
    return tuple(chain)


def _manifest_frontier_snapshots(
    root: Path,
    chain: Iterable[PublicationManifest],
) -> tuple[FrontierSnapshot, ...]:
    return tuple(
        _load_model(_target(root, frontier.snapshot.path), FrontierSnapshot)
        for manifest in chain
        for frontier in manifest.frontiers
    )


def _is_mutable_alias(parts: tuple[str, ...]) -> bool:
    if parts == ("latest.json",):
        return True
    if len(parts) == 2 and parts[0] == "feeds":
        return True
    if len(parts) != 3:
        return False
    if parts[0] == "frontiers":
        return parts[2] in {"latest.json", "history.json", "table.csv", "table.txt"}
    return parts[0] == "selections" and parts[2] == "latest.json"


def _reject_unreachable_immutables(
    root: Path,
    chain: tuple[PublicationManifest, ...],
    *,
    candidate_immutable_paths: set[str],
    candidate_aliases: Mapping[str, bytes],
) -> None:
    reachable: set[str] = set(candidate_immutable_paths)
    expected_aliases: dict[str, set[bytes]] = {
        path: {payload} for path, payload in candidate_aliases.items()
    }
    for manifest in chain:
        reachable.add(f"publications/{manifest.publication_id}.json")
        reachable.update(reference.path for reference in _immutable_manifest_files(manifest))
    if chain:
        current_manifest = chain[0]
        expected_aliases.setdefault("latest.json", set()).add(
            _read_regular(root / "publications" / f"{current_manifest.publication_id}.json")
        )
        for frontier in current_manifest.frontiers:
            expected_aliases.setdefault(f"frontiers/{frontier.frontier_id}/latest.json", set()).add(
                _verify_file_reference(root, frontier.snapshot)
            )
            expected_aliases.setdefault(f"frontiers/{frontier.frontier_id}/table.csv", set()).add(
                _verify_file_reference(root, frontier.csv)
            )
            expected_aliases.setdefault(f"frontiers/{frontier.frontier_id}/table.txt", set()).add(
                _verify_file_reference(root, frontier.table)
            )
            expected_aliases.setdefault(
                f"frontiers/{frontier.frontier_id}/history.json", set()
            ).add(_verify_file_reference(root, frontier.history))
            expected_aliases.setdefault(f"feeds/{frontier.frontier_id}.xml", set()).add(
                _verify_file_reference(root, frontier.feed)
            )
        for selection in current_manifest.selections:
            expected_aliases.setdefault(
                f"selections/{selection.selection_id}/latest.json", set()
            ).add(_verify_file_reference(root, selection.snapshot))

    unreachable: list[str] = []
    for current_value, _directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=_walk_error,
    ):
        directory = Path(current_value)
        for name in file_names:
            path = directory / name
            relative = path.relative_to(root)
            parts = relative.parts
            value = PurePosixPath(*parts).as_posix()
            if _is_mutable_alias(parts):
                expected_values = expected_aliases.get(value)
                if expected_values is None or _read_regular(path) not in expected_values:
                    raise PublicationError(
                        f"public publication root contains an uncommitted alias: {value}"
                    )
                continue
            if value not in reachable:
                unreachable.append(value)
    if unreachable:
        display = ", ".join(sorted(unreachable)[:10])
        suffix = " ..." if len(unreachable) > 10 else ""
        raise PublicationError(
            "public publication root contains uncommitted immutable artifacts: " + display + suffix
        )


def _ensure_parent(root: Path, target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PublicationError(f"cannot create artifact directory {target.parent}: {exc}") from exc
    _reject_link_components(target.parent)
    try:
        target.parent.relative_to(root)
    except ValueError as exc:  # pragma: no cover - guarded by generated relative paths
        raise PublicationError("artifact path escaped the publication root") from exc


def _write_temp(root: Path, target: Path, payload: bytes) -> Path:
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise PublicationError(
            f"artifact {target} exceeds the {MAX_ARTIFACT_BYTES}-byte publication limit"
        )
    descriptor, name = tempfile.mkstemp(
        dir=root.parent,
        prefix=".model-skyline-",
        suffix=".tmp",
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _write_immutable(root: Path, relative_path: str, payload: bytes) -> None:
    target = _target(root, relative_path)
    _ensure_parent(root, target)
    existing_status = _lstat(target)
    if existing_status is not None:
        existing = _read_regular(target)
        if existing != payload:
            raise PublicationError(
                f"immutable artifact collision with different bytes: {relative_path}"
            )
        return
    temporary = _write_temp(root, target, payload)
    try:
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _read_regular(target) != payload:
                raise PublicationError(
                    f"immutable artifact collision with different bytes: {relative_path}"
                ) from None
        except OSError as exc:
            raise PublicationError(f"cannot commit immutable artifact {target}: {exc}") from exc
        _sync_directory(target.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _immutable_write_order(item: tuple[str, bytes]) -> tuple[int, str]:
    path, _payload = item
    parts = PurePosixPath(path).parts
    is_primary_snapshot = (
        len(parts) == 3
        and parts[0] in {"frontiers", "selections"}
        and parts[2].endswith(".json")
        and _SHA256_RE.fullmatch(parts[2][:-5]) is not None
    )
    return (0 if is_primary_snapshot else 1, path)


def _replace_mutable(root: Path, relative_path: str, payload: bytes) -> None:
    target = _target(root, relative_path)
    _ensure_parent(root, target)
    status = _lstat(target)
    if status is not None:
        existing = _read_regular(target)
        if existing == payload:
            return
    temporary = _write_temp(root, target, payload)
    try:
        os.replace(temporary, target)
        _sync_directory(target.parent)
    except OSError as exc:
        raise PublicationError(f"cannot replace mutable artifact {target}: {exc}") from exc
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _licensed_sources(
    snapshots: Iterable[FrontierSnapshot],
    *,
    public: bool,
    allowed_licenses: tuple[str, ...],
    authorized_source_ids: tuple[str, ...],
) -> tuple[SourceReference, ...]:
    sources: dict[str, SourceReference] = {}
    for snapshot in snapshots:
        for source in snapshot.sources:
            sources[content_hash(source)] = source
    if public:
        if not sources:
            raise PublicationError("public publication requires cited source provenance")
        allowed = set(allowed_licenses)
        authorized = set(authorized_source_ids)
        denied = sorted(
            {
                source.id
                for source in sources.values()
                if source.license not in allowed and source.id not in authorized
            }
        )
        if denied:
            raise PublicationError(
                "public redistribution is not authorized for sources: " + ", ".join(denied)
            )
    return tuple(sources[digest] for digest in sorted(sources))


def _resolve_frontiers(
    config: ProjectConfig,
    catalogs: Mapping[str, ObservationCatalog],
    requested: Iterable[str] | None,
) -> tuple[str, ...]:
    if requested is None:
        ids = sorted(
            frontier_id
            for frontier_id, definition in config.frontiers.items()
            if definition.workload in catalogs
        )
    else:
        ids = sorted(set(requested))
    if not ids:
        raise PublicationError("no frontiers match the supplied catalogs")
    for frontier_id in ids:
        _portable_id(frontier_id, kind="frontier id")
        definition = config.frontiers.get(frontier_id)
        if definition is None:
            raise PublicationError(f"unknown frontier {frontier_id!r}")
        if definition.workload not in catalogs:
            raise PublicationError(
                f"frontier {frontier_id!r} has no catalog for workload {definition.workload!r}"
            )
    return tuple(ids)


def _resolve_selections(
    config: ProjectConfig,
    frontier_ids: tuple[str, ...],
    requested: Iterable[str] | None,
) -> tuple[str, ...]:
    frontier_set = set(frontier_ids)
    if requested is None:
        ids = sorted(
            selection_id
            for selection_id, definition in config.selections.items()
            if definition.frontier in frontier_set
        )
    else:
        ids = sorted(set(requested))
    for selection_id in ids:
        _portable_id(selection_id, kind="selection id")
        definition = config.selections.get(selection_id)
        if definition is None:
            raise PublicationError(f"unknown selection {selection_id!r}")
        if definition.frontier not in frontier_set:
            raise PublicationError(
                f"selection {selection_id!r} requires unpublished frontier {definition.frontier!r}"
            )
    return tuple(ids)


def _catalog_map(catalog_values: Iterable[ObservationCatalog]) -> dict[str, ObservationCatalog]:
    result: dict[str, ObservationCatalog] = {}
    for catalog in catalog_values:
        workload_id = catalog.workload.id
        if workload_id in result:
            raise PublicationError(f"multiple catalogs supplied for workload {workload_id!r}")
        result[workload_id] = catalog
    if not result:
        raise PublicationError("at least one observation catalog is required")
    return result


def _matching_workload(config: ProjectConfig, catalog: ObservationCatalog) -> None:
    profile = config.workloads.get(catalog.workload.id)
    if profile is None:
        raise PublicationError(f"catalog workload {catalog.workload.id!r} is not configured")
    if profile.version != catalog.workload.version or profile.unit != catalog.workload.unit:
        raise PublicationError(
            f"catalog workload {catalog.workload.id!r} does not match its configured version/unit"
        )


def _history_entry(snapshot: FrontierSnapshot, payload: bytes) -> FrontierHistoryEntry:
    relative = f"frontiers/{snapshot.frontier_id}/{snapshot.snapshot_id}.json"
    return FrontierHistoryEntry(
        snapshot_id=snapshot.snapshot_id,
        generated_at=snapshot.generated_at,
        workload=snapshot.workload,
        config_hash=snapshot.config_hash,
        catalog_hash=snapshot.catalog_hash,
        axis_hash=_axis_hash(snapshot),
        view_hash=_view_hash(snapshot),
        snapshot=_published_file(relative, payload, "application/json"),
    )


def _same_publication(
    previous: PublicationManifest,
    candidate: PublicationManifest,
) -> bool:
    ignored = {"publication_id", "previous_publication_id"}
    return previous.model_dump(mode="json", exclude=ignored) == candidate.model_dump(
        mode="json", exclude=ignored
    )


def publish_project(
    config: ProjectConfig,
    catalog_values: Iterable[ObservationCatalog],
    output_directory: str | Path,
    *,
    project_id: str,
    frontier_ids: Iterable[str] | None = None,
    selection_ids: Iterable[str] | None = None,
    generated_at: datetime | None = None,
    base_url: str | None = None,
    feed_items: int = 20,
    public: bool = False,
    allowed_licenses: Iterable[str] = (),
    authorized_source_ids: Iterable[str] = (),
) -> PublicationResult:
    """Evaluate and commit one coherent static publication set."""

    project_id = _portable_id(project_id, kind="project id")
    if not 1 <= feed_items <= MAX_RSS_ITEMS:
        raise PublicationError(f"feed_items must be between 1 and {MAX_RSS_ITEMS}")
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise PublicationError("generated_at must include a timezone")
    timestamp = timestamp.astimezone(UTC)
    normalized_url = _normalized_base_url(base_url, public=public)
    licenses = tuple(sorted(set(allowed_licenses)))
    source_overrides = tuple(sorted(set(authorized_source_ids)))
    if any(not value for value in (*licenses, *source_overrides)):
        raise PublicationError("license and source authorization values cannot be empty")

    catalogs = _catalog_map(catalog_values)
    for catalog in catalogs.values():
        _matching_workload(config, catalog)
    selected_frontiers = _resolve_frontiers(config, catalogs, frontier_ids)
    selected_selections = _resolve_selections(config, selected_frontiers, selection_ids)

    engine = FrontierEngine()
    frontier_snapshots: dict[str, FrontierSnapshot] = {}
    for frontier_id in selected_frontiers:
        definition = config.frontiers[frontier_id]
        frontier_snapshots[frontier_id] = engine.calculate(
            config,
            catalogs[definition.workload],
            frontier_id,
            generated_at=timestamp,
        )
    selection_snapshots: dict[str, SelectionSnapshot] = {}
    for selection_id in selected_selections:
        selection_definition = config.selections[selection_id]
        selection_snapshots[selection_id] = select_models(
            config,
            frontier_snapshots[selection_definition.frontier],
            selection_id,
        )

    policy = PublicationPolicy(
        public=public,
        allowed_licenses=licenses,
        authorized_source_ids=source_overrides,
    )
    root = _prepare_root(output_directory)

    with _writer_lock(root):
        _validate_existing_tree(root)
        previous = _load_previous_manifest(root, project_id)
        manifest_chain = _validated_manifest_chain(root, previous)
        committed_manifest_snapshots = _manifest_frontier_snapshots(root, manifest_chain)
        if previous is not None and not {
            entry.frontier_id for entry in previous.frontiers
        }.issubset(selected_frontiers):
            raise PublicationError(
                "an existing publication root must refresh every previously published frontier"
            )
        if previous is not None and not {
            entry.selection_id for entry in previous.selections
        }.issubset(selected_selections):
            raise PublicationError(
                "an existing publication root must refresh every previously published selection"
            )
        old_histories = _load_committed_histories(root, previous)
        if previous is not None and timestamp < previous.generated_at:
            raise PublicationError("generated_at cannot precede the current publication timestamp")
        retained_snapshots = [
            historical for snapshots in old_histories.values() for historical in snapshots.values()
        ]
        _licensed_sources(
            [
                *frontier_snapshots.values(),
                *retained_snapshots,
                *committed_manifest_snapshots,
            ],
            public=public,
            allowed_licenses=licenses,
            authorized_source_ids=source_overrides,
        )

        immutable_payloads: dict[str, bytes] = {}
        mutable_payloads: dict[str, bytes] = {}
        published_frontiers: list[PublishedFrontier] = []
        for frontier_id in selected_frontiers:
            snapshot = frontier_snapshots[frontier_id]
            snapshot_path = f"frontiers/{frontier_id}/{snapshot.snapshot_id}.json"
            snapshot_payload = dump_json(snapshot).encode("utf-8")
            immutable_payloads[snapshot_path] = snapshot_payload

            history_snapshots = dict(old_histories.get(frontier_id, {}))
            if any(
                historical.generated_at > timestamp for historical in history_snapshots.values()
            ):
                raise PublicationError(
                    f"generated_at cannot precede existing history for frontier {frontier_id!r}"
                )
            if any(
                historical.generated_at == timestamp
                and historical.snapshot_id != snapshot.snapshot_id
                for historical in history_snapshots.values()
            ):
                raise PublicationError(
                    f"generated_at is already used by a different snapshot for "
                    f"frontier {frontier_id!r}"
                )
            existing = history_snapshots.get(snapshot.snapshot_id)
            if existing is not None and existing != snapshot:
                raise PublicationError(
                    f"snapshot id {snapshot.snapshot_id} maps to conflicting frontier artifacts"
                )
            history_snapshots[snapshot.snapshot_id] = snapshot
            if len(history_snapshots) > MAX_HISTORY_ENTRIES:
                raise PublicationError(
                    f"frontier {frontier_id!r} exceeds the "
                    f"{MAX_HISTORY_ENTRIES}-snapshot history limit"
                )
            payload_by_id: dict[str, bytes] = {}
            for historical in history_snapshots.values():
                path = f"frontiers/{frontier_id}/{historical.snapshot_id}.json"
                payload_by_id[historical.snapshot_id] = (
                    snapshot_payload
                    if historical.snapshot_id == snapshot.snapshot_id
                    else _read_regular(_target(root, path))
                )
            history = FrontierHistory(
                frontier_id=frontier_id,
                entries=tuple(
                    sorted(
                        (
                            _history_entry(historical, payload_by_id[historical.snapshot_id])
                            for historical in history_snapshots.values()
                        ),
                        key=lambda entry: (entry.generated_at, entry.snapshot_id),
                        reverse=True,
                    )
                ),
            )
            history_payload = dump_json(history).encode("utf-8")
            history_alias_path = f"frontiers/{frontier_id}/history.json"
            history_path = f"frontiers/{frontier_id}/history-{_sha256(history_payload)}.json"

            item_links = {
                historical.snapshot_id: link
                for historical in history_snapshots.values()
                if (
                    link := _artifact_url(
                        normalized_url,
                        f"frontiers/{frontier_id}/{historical.snapshot_id}.json",
                    )
                )
                is not None
            }
            latest_path = f"frontiers/{frontier_id}/latest.json"
            feed_alias_path = f"feeds/{frontier_id}.xml"
            csv_alias_path = f"frontiers/{frontier_id}/table.csv"
            table_alias_path = f"frontiers/{frontier_id}/table.txt"
            feed_payload = render_rss_history(
                history_snapshots.values(),
                max_items=feed_items,
                channel_link=_artifact_url(normalized_url, latest_path),
                item_links=item_links,
            ).encode("utf-8")
            csv_payload = render_csv(snapshot).encode("utf-8")
            table_payload = render_table(snapshot).encode("utf-8")
            csv_path = f"frontiers/{frontier_id}/{snapshot.snapshot_id}.csv"
            table_path = f"frontiers/{frontier_id}/{snapshot.snapshot_id}.txt"
            feed_path = f"feeds/{frontier_id}/{_sha256(feed_payload)}.xml"
            immutable_payloads[csv_path] = csv_payload
            immutable_payloads[table_path] = table_payload
            immutable_payloads[history_path] = history_payload
            immutable_payloads[feed_path] = feed_payload
            mutable_payloads[csv_alias_path] = csv_payload
            mutable_payloads[table_alias_path] = table_payload
            mutable_payloads[history_alias_path] = history_payload
            mutable_payloads[feed_alias_path] = feed_payload
            mutable_payloads[latest_path] = snapshot_payload
            published_frontiers.append(
                PublishedFrontier(
                    frontier_id=frontier_id,
                    snapshot_id=snapshot.snapshot_id,
                    snapshot=_published_file(snapshot_path, snapshot_payload, "application/json"),
                    csv=_published_file(csv_path, csv_payload, "text/csv; charset=utf-8"),
                    table=_published_file(table_path, table_payload, "text/plain; charset=utf-8"),
                    history=_published_file(history_path, history_payload, "application/json"),
                    feed=_published_file(feed_path, feed_payload, "application/rss+xml"),
                )
            )

        published_selections: list[PublishedSelection] = []
        for selection_id in selected_selections:
            selection_snapshot = selection_snapshots[selection_id]
            snapshot_path = f"selections/{selection_id}/{selection_snapshot.snapshot_id}.json"
            latest_path = f"selections/{selection_id}/latest.json"
            payload = dump_json(selection_snapshot).encode("utf-8")
            immutable_payloads[snapshot_path] = payload
            mutable_payloads[latest_path] = payload
            published_selections.append(
                PublishedSelection(
                    selection_id=selection_id,
                    snapshot_id=selection_snapshot.snapshot_id,
                    frontier_id=selection_snapshot.frontier_id,
                    frontier_snapshot_id=selection_snapshot.frontier_snapshot_id,
                    snapshot=_published_file(snapshot_path, payload, "application/json"),
                )
            )

        used_workloads = sorted(
            {config.frontiers[frontier_id].workload for frontier_id in selected_frontiers}
        )
        published_catalogs = tuple(
            PublishedCatalog(
                workload=catalogs[workload_id].workload,
                catalog_hash=catalog_hash(catalogs[workload_id]),
            )
            for workload_id in used_workloads
        )
        placeholder = "0" * 64
        candidate = PublicationManifest(
            publication_id=placeholder,
            project_id=project_id,
            previous_publication_id=previous.publication_id if previous else None,
            project_hash=content_hash(config),
            generated_at=timestamp,
            catalogs=published_catalogs,
            policy=policy,
            frontiers=tuple(published_frontiers),
            selections=tuple(published_selections),
        )
        candidate = candidate.model_copy(update={"publication_id": publication_hash(candidate)})
        unchanged = previous is not None and _same_publication(previous, candidate)
        final_manifest = previous if unchanged and previous is not None else candidate
        manifest_payload = dump_json(final_manifest).encode("utf-8")
        publication_path = f"publications/{final_manifest.publication_id}.json"

        for path, payload in (
            *immutable_payloads.items(),
            *mutable_payloads.items(),
            (publication_path, manifest_payload),
            ("latest.json", manifest_payload),
        ):
            if len(payload) > MAX_ARTIFACT_BYTES:
                raise PublicationError(
                    f"artifact {path} exceeds the {MAX_ARTIFACT_BYTES}-byte publication limit"
                )
        if public:
            _reject_unreachable_immutables(
                root,
                manifest_chain,
                candidate_immutable_paths={*immutable_payloads, publication_path},
                candidate_aliases={**mutable_payloads, "latest.json": manifest_payload},
            )

        for path, payload in sorted(immutable_payloads.items(), key=_immutable_write_order):
            _write_immutable(root, path, payload)
        # The immutable manifest is committed only after every file it names.
        _write_immutable(root, publication_path, manifest_payload)
        for path, payload in sorted(mutable_payloads.items()):
            _replace_mutable(root, path, payload)
        # Cross-artifact commit marker: deliberately and unconditionally last.
        _replace_mutable(root, "latest.json", manifest_payload)

    return PublicationResult(
        manifest=final_manifest,
        manifest_path=Path(output_directory) / "latest.json",
        changed=not unchanged,
    )
