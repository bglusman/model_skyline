"""Durable anti-rollback and last-known-good storage for gateway selections."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from model_skyline.gateway import (
    MAX_GATEWAY_ARTIFACT_BYTES,
    MAX_GATEWAY_ENVELOPE_BYTES,
    MAX_GATEWAY_RUNTIME_CANDIDATES,
    BoundGatewayTarget,
    GatewayProtocolError,
    GatewaySequenceCheckpoint,
    GatewaySequenceError,
    StoredGatewayBundle,
    dsse_payload_bytes,
    parse_dsse_envelope,
    parse_gateway_pointer,
    target_bindings_bytes,
)

STORE_SCHEMA_VERSION = 2
MAX_GATEWAY_CHECKPOINT_BYTES = 16 * 1024
MAX_GATEWAY_SQLITE_VALUE_BYTES = 32 * 1024 * 1024
_TARGET_BINDINGS_ADAPTER = TypeAdapter(tuple[BoundGatewayTarget, ...])


class GatewayStoreError(GatewayProtocolError):
    """Durable gateway state is unavailable, corrupt, or inconsistent."""


def _identity(
    *,
    trust_namespace: str,
    issuer: str,
    audience: str,
    channel: str,
) -> tuple[str, str, str, str]:
    return trust_namespace, issuer, audience, channel


def _checkpoint_identity(checkpoint: GatewaySequenceCheckpoint) -> tuple[str, str, str, str]:
    return _identity(
        trust_namespace=checkpoint.trust_namespace,
        issuer=checkpoint.issuer,
        audience=checkpoint.audience,
        channel=checkpoint.channel,
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _check_candidate_against_current(
    candidate: GatewaySequenceCheckpoint,
    current: GatewaySequenceCheckpoint | None,
) -> None:
    if current is None:
        return
    if _checkpoint_identity(candidate) != _checkpoint_identity(current):
        raise GatewaySequenceError("gateway checkpoint identity changed during installation")
    if candidate.sequence < current.sequence:
        raise GatewaySequenceError("gateway pointer sequence would roll back")
    if candidate.sequence == current.sequence:
        comparable = (
            "payload_sha256",
            "publication_artifact_sha256",
            "selection_artifact_sha256",
            "selection_snapshot_id",
            "target_bindings_sha256",
            "hard_expires_at",
        )
        if any(getattr(candidate, field) != getattr(current, field) for field in comparable):
            raise GatewaySequenceError("gateway pointer sequence would equivocate")


def _validate_bundle_bytes(bundle: StoredGatewayBundle) -> None:
    checkpoint = bundle.checkpoint
    if len(bundle.envelope_payload) > MAX_GATEWAY_ENVELOPE_BYTES:
        raise GatewayStoreError("stored DSSE envelope exceeds its byte limit")
    if len(bundle.publication_payload) > MAX_GATEWAY_ARTIFACT_BYTES:
        raise GatewayStoreError("stored publication exceeds its byte limit")
    if len(bundle.selection_payload) > MAX_GATEWAY_ARTIFACT_BYTES:
        raise GatewayStoreError("stored selection exceeds its byte limit")
    if not 1 <= len(bundle.target_bindings_payload) <= MAX_GATEWAY_ARTIFACT_BYTES:
        raise GatewayStoreError("stored target bindings exceed their byte limit")
    envelope = parse_dsse_envelope(bundle.envelope_payload)
    pointer_payload = dsse_payload_bytes(envelope)
    pointer = parse_gateway_pointer(pointer_payload)
    if _digest(pointer_payload) != checkpoint.payload_sha256:
        raise GatewayStoreError("stored envelope pointer digest is corrupt")
    if _digest(bundle.publication_payload) != checkpoint.publication_artifact_sha256:
        raise GatewayStoreError("stored publication digest is corrupt")
    if _digest(bundle.selection_payload) != checkpoint.selection_artifact_sha256:
        raise GatewayStoreError("stored selection digest is corrupt")
    try:
        targets = _TARGET_BINDINGS_ADAPTER.validate_json(bundle.target_bindings_payload)
    except ValidationError as exc:
        raise GatewayStoreError("stored target bindings are corrupt") from exc
    if not 1 <= len(targets) <= MAX_GATEWAY_RUNTIME_CANDIDATES:
        raise GatewayStoreError("stored target binding count is invalid")
    if target_bindings_bytes(targets) != bundle.target_bindings_payload:
        raise GatewayStoreError("stored target bindings are not canonical")
    if _digest(bundle.target_bindings_payload) != checkpoint.target_bindings_sha256:
        raise GatewayStoreError("stored target binding digest is corrupt")
    signed_checkpoint_fields = (
        (checkpoint.issuer, pointer.issuer),
        (checkpoint.channel, pointer.channel),
        (checkpoint.sequence, pointer.sequence),
        (checkpoint.publication_artifact_sha256, pointer.publication.file.sha256),
        (checkpoint.selection_artifact_sha256, pointer.selection.file.sha256),
        (checkpoint.selection_snapshot_id, pointer.selection.snapshot_id),
        (checkpoint.hard_expires_at, pointer.hard_expires_at),
    )
    if any(stored != signed for stored, signed in signed_checkpoint_fields):
        raise GatewayStoreError("stored checkpoint does not match its signed pointer")
    if checkpoint.audience not in pointer.audience:
        raise GatewayStoreError("stored checkpoint audience is not signed by its pointer")
    if bundle.installed_at.tzinfo is None:
        raise GatewayStoreError("stored installation time must include a timezone")


class SqliteGatewayInstallationStore:
    """SQLite-backed, cross-process atomic gateway installation store.

    ``BEGIN IMMEDIATE`` serializes competing installers.  The checkpoint and
    exact envelope/publication/selection bytes are committed in one transaction;
    callers publish an in-memory route only after ``install`` returns.
    """

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        requested = Path(path)
        # ``Path.exists`` follows links and is false for a dangling final link.
        # Inspect the directory entry itself before doing anything that SQLite
        # could follow.
        try:
            requested_status = requested.lstat()
        except FileNotFoundError:
            requested_status = None
        if requested_status is not None and stat.S_ISLNK(requested_status.st_mode):
            raise GatewayStoreError("gateway state database cannot be a symbolic link")
        parent_existed = requested.parent.exists()
        requested.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_existed:
            os.chmod(requested.parent, 0o700)
        self.path = requested.parent.resolve() / requested.name
        self._lock = threading.RLock()
        self._validate_private_parent()
        created = self._prepare_database_file()
        before = self._file_identity(self.path)
        self._validate_existing_sidecars()
        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=timeout_seconds,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.setlimit(
                sqlite3.SQLITE_LIMIT_LENGTH,
                MAX_GATEWAY_SQLITE_VALUE_BYTES,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._configure_wal(timeout_seconds)
            self._initialize()
            after = self._file_identity(self.path)
            if before != after:
                raise GatewayStoreError("gateway state database changed during initialization")
            self._secure_sidecars()
            if created:
                self._fsync_parent()
        except Exception as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            if isinstance(exc, GatewayStoreError):
                raise
            if not isinstance(exc, (OSError, sqlite3.Error)):
                raise
            raise GatewayStoreError(f"cannot initialize gateway state database: {exc}") from exc

    def _configure_wal(self, timeout_seconds: float) -> None:
        """Enable WAL with a bounded retry for concurrent first-open races."""

        deadline = time.monotonic() + timeout_seconds
        delay = 0.001
        while True:
            try:
                row = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()
                mode = None if row is None else str(row[0]).lower()
                if mode != "wal":
                    raise GatewayStoreError(f"unsupported SQLite journal mode {mode!r}")
                return
            except sqlite3.OperationalError as exc:
                error_code = getattr(exc, "sqlite_errorcode", None)
                retryable = error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or (
                    "locked" in str(exc).lower()
                )
                remaining = deadline - time.monotonic()
                if not retryable or remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.05)

    @staticmethod
    def _owner_matches_process(status: os.stat_result) -> bool:
        return not hasattr(os, "geteuid") or status.st_uid == os.geteuid()

    def _validate_private_parent(self) -> None:
        try:
            status = self.path.parent.stat()
        except OSError as exc:
            raise GatewayStoreError(f"cannot inspect gateway state directory: {exc}") from exc
        if not stat.S_ISDIR(status.st_mode):
            raise GatewayStoreError("gateway state parent must be a directory")
        if not self._owner_matches_process(status):
            raise GatewayStoreError("gateway state directory must be owned by this process user")
        if stat.S_IMODE(status.st_mode) & 0o077:
            raise GatewayStoreError(
                "gateway state directory must not grant group or other permissions"
            )

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int]:
        try:
            status = path.lstat()
        except OSError as exc:
            raise GatewayStoreError(f"cannot inspect gateway state database: {exc}") from exc
        if stat.S_ISLNK(status.st_mode):
            raise GatewayStoreError("gateway state database cannot be a symbolic link")
        if not stat.S_ISREG(status.st_mode):
            raise GatewayStoreError("gateway state database must be a regular file")
        return status.st_dev, status.st_ino

    def _prepare_database_file(self) -> bool:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            self._validate_database_file()
            return False
        except OSError as exc:
            raise GatewayStoreError(
                f"cannot securely create gateway state database: {exc}"
            ) from exc
        try:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True

    def _validate_database_file(self) -> None:
        try:
            status = self.path.lstat()
        except OSError as exc:
            raise GatewayStoreError(f"cannot inspect gateway state database: {exc}") from exc
        if stat.S_ISLNK(status.st_mode):
            raise GatewayStoreError("gateway state database cannot be a symbolic link")
        if not stat.S_ISREG(status.st_mode):
            raise GatewayStoreError("gateway state database must be a regular file")
        if not self._owner_matches_process(status):
            raise GatewayStoreError("gateway state database must be owned by this process user")
        mode = stat.S_IMODE(status.st_mode)
        if mode & 0o077 or mode & 0o600 != 0o600:
            raise GatewayStoreError("gateway state database must have mode 0600")
        if status.st_nlink != 1:
            raise GatewayStoreError("gateway state database cannot have multiple hard links")

    def _validate_existing_sidecars(self) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            try:
                status = sidecar.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise GatewayStoreError(f"cannot inspect gateway SQLite sidecar: {exc}") from exc
            if (
                not stat.S_ISREG(status.st_mode)
                or stat.S_ISLNK(status.st_mode)
                or not self._owner_matches_process(status)
                or status.st_nlink != 1
            ):
                raise GatewayStoreError("gateway SQLite sidecar is not a safe regular file")

    def _secure_sidecars(self) -> None:
        self._validate_existing_sidecars()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.path}{suffix}")
            try:
                os.chmod(sidecar, 0o600, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise GatewayStoreError(f"cannot secure gateway SQLite sidecar: {exc}") from exc

    def _fsync_parent(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _initialize(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
            CREATE TABLE IF NOT EXISTS gateway_store_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            )
            """
            )
            self._connection.execute(
                """
            CREATE TABLE IF NOT EXISTS gateway_installations (
                trust_namespace TEXT NOT NULL,
                issuer TEXT NOT NULL,
                audience TEXT NOT NULL,
                channel TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                checkpoint_json BLOB NOT NULL,
                envelope_payload BLOB NOT NULL,
                publication_payload BLOB NOT NULL,
                selection_payload BLOB NOT NULL,
                target_bindings_payload BLOB NOT NULL,
                installed_at TEXT NOT NULL,
                CHECK (length(checkpoint_json) BETWEEN 1 AND 16384),
                CHECK (length(envelope_payload) BETWEEN 1 AND 65536),
                CHECK (length(publication_payload) BETWEEN 1 AND 10485760),
                CHECK (length(selection_payload) BETWEEN 1 AND 10485760),
                CHECK (length(target_bindings_payload) BETWEEN 1 AND 10485760),
                PRIMARY KEY (trust_namespace, issuer, audience, channel)
            ) WITHOUT ROWID
            """
            )
            self._connection.execute(
                """
                INSERT INTO gateway_store_meta(singleton, schema_version) VALUES (1, ?)
                ON CONFLICT(singleton) DO NOTHING
                """,
                (STORE_SCHEMA_VERSION,),
            )
            row = self._connection.execute(
                "SELECT schema_version FROM gateway_store_meta WHERE singleton = 1"
            ).fetchone()
            if row is None or row["schema_version"] != STORE_SCHEMA_VERSION:
                version = None if row is None else row["schema_version"]
                raise GatewayStoreError(f"unsupported gateway store schema {version!r}")
            self._connection.execute("COMMIT")
        except Exception:
            with suppress(sqlite3.Error):
                self._connection.execute("ROLLBACK")
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SqliteGatewayInstallationStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _row_bundle(row: sqlite3.Row) -> StoredGatewayBundle:
        try:
            if len(bytes(row["checkpoint_json"])) > MAX_GATEWAY_CHECKPOINT_BYTES:
                raise GatewayStoreError("stored checkpoint JSON exceeds its byte limit")
            checkpoint = GatewaySequenceCheckpoint.model_validate_json(
                bytes(row["checkpoint_json"])
            )
            installed_at = datetime.fromisoformat(str(row["installed_at"]).replace("Z", "+00:00"))
            if installed_at.tzinfo is None:
                raise GatewayStoreError("stored installation time must include a timezone")
            bundle = StoredGatewayBundle(
                checkpoint=checkpoint,
                envelope_payload=bytes(row["envelope_payload"]),
                publication_payload=bytes(row["publication_payload"]),
                selection_payload=bytes(row["selection_payload"]),
                target_bindings_payload=bytes(row["target_bindings_payload"]),
                installed_at=installed_at.astimezone(UTC),
            )
            database_identity = (
                str(row["trust_namespace"]),
                str(row["issuer"]),
                str(row["audience"]),
                str(row["channel"]),
            )
            if _checkpoint_identity(checkpoint) != database_identity:
                raise GatewayStoreError("stored checkpoint identity does not match its key")
            if checkpoint.sequence != row["sequence"]:
                raise GatewayStoreError("stored checkpoint sequence does not match its index")
            _validate_bundle_bytes(bundle)
            return bundle
        except (TypeError, ValueError, ValidationError) as exc:
            if isinstance(exc, GatewayStoreError):
                raise
            raise GatewayStoreError("stored gateway installation is corrupt") from exc

    def _row(self, identity: tuple[str, str, str, str]) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT trust_namespace, issuer, audience, channel, sequence,
                       checkpoint_json, envelope_payload, publication_payload,
                       selection_payload, target_bindings_payload, installed_at
                FROM gateway_installations
                WHERE trust_namespace = ? AND issuer = ? AND audience = ? AND channel = ?
                """,
                identity,
            ).fetchone(),
        )

    def load(
        self,
        *,
        trust_namespace: str,
        issuer: str,
        audience: str,
        channel: str,
    ) -> StoredGatewayBundle | None:
        identity = _identity(
            trust_namespace=trust_namespace,
            issuer=issuer,
            audience=audience,
            channel=channel,
        )
        with self._lock:
            try:
                row = self._row(identity)
                return None if row is None else self._row_bundle(row)
            except sqlite3.Error as exc:
                raise GatewayStoreError(f"cannot load gateway installation: {exc}") from exc

    def current(
        self,
        *,
        trust_namespace: str,
        issuer: str,
        audience: str,
        channel: str,
    ) -> GatewaySequenceCheckpoint | None:
        bundle = self.load(
            trust_namespace=trust_namespace,
            issuer=issuer,
            audience=audience,
            channel=channel,
        )
        return None if bundle is None else bundle.checkpoint

    def install(self, bundle: StoredGatewayBundle) -> None:
        _validate_bundle_bytes(bundle)
        if bundle.installed_at.tzinfo is None:
            raise GatewayStoreError("stored installation time must include a timezone")
        checkpoint = bundle.checkpoint
        identity = _checkpoint_identity(checkpoint)
        checkpoint_json = checkpoint.model_dump_json().encode("utf-8")
        installed_at = bundle.installed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._row(identity)
                current = None if row is None else self._row_bundle(row).checkpoint
                _check_candidate_against_current(checkpoint, current)
                self._connection.execute(
                    """
                    INSERT INTO gateway_installations(
                        trust_namespace, issuer, audience, channel, sequence,
                        checkpoint_json, envelope_payload, publication_payload,
                        selection_payload, target_bindings_payload, installed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trust_namespace, issuer, audience, channel) DO UPDATE SET
                        sequence = excluded.sequence,
                        checkpoint_json = excluded.checkpoint_json,
                        envelope_payload = excluded.envelope_payload,
                        publication_payload = excluded.publication_payload,
                        selection_payload = excluded.selection_payload,
                        target_bindings_payload = excluded.target_bindings_payload,
                        installed_at = excluded.installed_at
                    """,
                    (
                        *identity,
                        checkpoint.sequence,
                        checkpoint_json,
                        bundle.envelope_payload,
                        bundle.publication_payload,
                        bundle.selection_payload,
                        bundle.target_bindings_payload,
                        installed_at,
                    ),
                )
                self._connection.execute("COMMIT")
                self._secure_sidecars()
            except Exception as exc:
                with suppress(sqlite3.Error):
                    self._connection.execute("ROLLBACK")
                if isinstance(exc, GatewayProtocolError):
                    raise
                raise GatewayStoreError(f"cannot install gateway selection: {exc}") from exc

    def diagnostic_counts(self) -> dict[str, int]:
        """Return bounded, content-free state counts for health reporting."""

        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT COUNT(*) AS installations FROM gateway_installations"
                ).fetchone()
            except sqlite3.Error as exc:
                raise GatewayStoreError(f"cannot inspect gateway state: {exc}") from exc
        return {"installations": int(row["installations"] if row is not None else 0)}


def sqlite_store_schema_version() -> int:
    return STORE_SCHEMA_VERSION
