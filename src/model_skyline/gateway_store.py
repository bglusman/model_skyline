"""Durable anti-rollback and last-known-good storage for gateway selections."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from model_skyline.gateway import (
    MAX_GATEWAY_ARTIFACT_BYTES,
    MAX_GATEWAY_ENVELOPE_BYTES,
    GatewayProtocolError,
    GatewaySequenceCheckpoint,
    GatewaySequenceError,
    StoredGatewayBundle,
    dsse_payload_bytes,
    parse_dsse_envelope,
)

STORE_SCHEMA_VERSION = 1


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
    envelope = parse_dsse_envelope(bundle.envelope_payload)
    if _digest(dsse_payload_bytes(envelope)) != checkpoint.payload_sha256:
        raise GatewayStoreError("stored envelope pointer digest is corrupt")
    if _digest(bundle.publication_payload) != checkpoint.publication_artifact_sha256:
        raise GatewayStoreError("stored publication digest is corrupt")
    if _digest(bundle.selection_payload) != checkpoint.selection_artifact_sha256:
        raise GatewayStoreError("stored selection digest is corrupt")
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
        if requested.exists() and requested.is_symlink():
            raise GatewayStoreError("gateway state database cannot be a symbolic link")
        requested.parent.mkdir(parents=True, exist_ok=True)
        self.path = requested.parent.resolve() / requested.name
        self._lock = threading.RLock()
        existed = self.path.exists()
        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=timeout_seconds,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize()
            if not existed:
                os.chmod(self.path, 0o600)
                self._fsync_parent()
        except (OSError, sqlite3.Error) as exc:
            raise GatewayStoreError(f"cannot initialize gateway state database: {exc}") from exc

    def _fsync_parent(self) -> None:
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS gateway_store_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version INTEGER NOT NULL
            );
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
                installed_at TEXT NOT NULL,
                PRIMARY KEY (trust_namespace, issuer, audience, channel)
            ) WITHOUT ROWID;
            COMMIT;
            """
        )
        row = self._connection.execute(
            "SELECT schema_version FROM gateway_store_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO gateway_store_meta(singleton, schema_version) VALUES (1, ?)",
                (STORE_SCHEMA_VERSION,),
            )
        elif row["schema_version"] != STORE_SCHEMA_VERSION:
            raise GatewayStoreError(f"unsupported gateway store schema {row['schema_version']!r}")

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
            checkpoint = GatewaySequenceCheckpoint.model_validate_json(
                bytes(row["checkpoint_json"])
            )
            installed_at = datetime.fromisoformat(str(row["installed_at"]).replace("Z", "+00:00"))
            bundle = StoredGatewayBundle(
                checkpoint=checkpoint,
                envelope_payload=bytes(row["envelope_payload"]),
                publication_payload=bytes(row["publication_payload"]),
                selection_payload=bytes(row["selection_payload"]),
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
                       selection_payload, installed_at
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
                        selection_payload, installed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trust_namespace, issuer, audience, channel) DO UPDATE SET
                        sequence = excluded.sequence,
                        checkpoint_json = excluded.checkpoint_json,
                        envelope_payload = excluded.envelope_payload,
                        publication_payload = excluded.publication_payload,
                        selection_payload = excluded.selection_payload,
                        installed_at = excluded.installed_at
                    """,
                    (
                        *identity,
                        checkpoint.sequence,
                        checkpoint_json,
                        bundle.envelope_payload,
                        bundle.publication_payload,
                        bundle.selection_payload,
                        installed_at,
                    ),
                )
                self._connection.execute("COMMIT")
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
