from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from model_skyline.gateway import (
    GatewaySequenceCheckpoint,
    GatewayTrustPolicy,
    StoredGatewayBundle,
    build_gateway_pointer,
    build_stored_gateway_bundle,
    envelope_bytes,
    sign_gateway_pointer,
    verify_gateway_bundle,
)
from model_skyline.gateway_resolver import (
    GatewayFetchResult,
    GatewayResolverError,
    GatewayResolverState,
    GatewayTransportError,
    SignedGatewayResolver,
)
from model_skyline.gateway_store import GatewayStoreError, SqliteGatewayInstallationStore

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)
CONFORMANCE = Path(__file__).parents[1] / "conformance" / "gateway-pointer" / "v1alpha1"
SOURCE = "https://control.example/model-skyline/gateway/coding-defaults/latest.dsse.json"


def _policy() -> GatewayTrustPolicy:
    return GatewayTrustPolicy.model_validate_json(
        (CONFORMANCE / "valid" / "trust-policy.json").read_bytes()
    )


def _artifact_bytes() -> tuple[bytes, bytes]:
    return (
        (CONFORMANCE / "artifacts" / "publication.json").read_bytes(),
        (CONFORMANCE / "artifacts" / "selection.json").read_bytes(),
    )


def _signing_key() -> Ed25519PrivateKey:
    seed = bytes.fromhex((CONFORMANCE / "keys" / "key-1.test-seed.hex").read_text().strip())
    return Ed25519PrivateKey.from_private_bytes(seed)


def _envelope(
    sequence: int,
    *,
    not_before: datetime = NOW,
    issued_at: datetime = NOW,
    hard_expires_at: datetime = NOW + timedelta(minutes=30),
) -> bytes:
    publication, selection = _artifact_bytes()
    pointer = build_gateway_pointer(
        publication,
        selection,
        issuer="https://control.example/model-skyline",
        audience=["wardwright-prod", "wardwright-canary"],
        channel="coding-defaults",
        sequence=sequence,
        selection_id="coding-agent-defaults",
        issued_at=issued_at,
        not_before=not_before,
        hard_expires_at=hard_expires_at,
        required_capabilities=["tools", "structured_output"],
    )
    return envelope_bytes(sign_gateway_pointer(pointer, (_signing_key(),)))


def _bundle(sequence: int) -> StoredGatewayBundle:
    publication, selection = _artifact_bytes()
    envelope = _envelope(sequence)
    verified = verify_gateway_bundle(
        envelope,
        publication,
        selection,
        _policy(),
        now=NOW,
    )
    return build_stored_gateway_bundle(
        verified,
        envelope_payload=envelope,
        publication_payload=publication,
        selection_payload=selection,
        installed_at=NOW,
    )


def _update_checkpoint(database: Path, **changes: object) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT checkpoint_json FROM gateway_installations").fetchone()
        assert row is not None
        checkpoint = json.loads(bytes(row[0]))
        checkpoint.update(changes)
        assignments = ["checkpoint_json = ?"]
        values: list[object] = [json.dumps(checkpoint).encode("utf-8")]
        if "sequence" in changes:
            assignments.append("sequence = ?")
            values.append(changes["sequence"])
        if "channel" in changes:
            assignments.append("channel = ?")
            values.append(changes["channel"])
        connection.execute(
            f"UPDATE gateway_installations SET {', '.join(assignments)}",  # noqa: S608
            values,
        )


class OfflineFetcher:
    def fetch(
        self,
        url: str,
        *,
        expected_media_type: str,
        maximum_bytes: int,
        timeout_seconds: float,
        etag: str | None = None,
    ) -> GatewayFetchResult:
        del url, expected_media_type, maximum_bytes, timeout_seconds, etag
        raise GatewayTransportError("offline for regression test")


class EnvelopeFetcher:
    def __init__(self, envelope: bytes) -> None:
        self.envelope = envelope

    def fetch(
        self,
        url: str,
        *,
        expected_media_type: str,
        maximum_bytes: int,
        timeout_seconds: float,
        etag: str | None = None,
    ) -> GatewayFetchResult:
        del url, expected_media_type, maximum_bytes, timeout_seconds, etag
        return GatewayFetchResult(payload=self.envelope, etag='"attack"')


class AdvancingEnvelopeFetcher(EnvelopeFetcher):
    def __init__(
        self,
        envelope: bytes,
        clock_state: dict[str, datetime],
        advanced_time: datetime,
    ) -> None:
        super().__init__(envelope)
        self.clock_state = clock_state
        self.advanced_time = advanced_time

    def fetch(
        self,
        url: str,
        *,
        expected_media_type: str,
        maximum_bytes: int,
        timeout_seconds: float,
        etag: str | None = None,
    ) -> GatewayFetchResult:
        self.clock_state["now"] = self.advanced_time
        return super().fetch(
            url,
            expected_media_type=expected_media_type,
            maximum_bytes=maximum_bytes,
            timeout_seconds=timeout_seconds,
            etag=etag,
        )


class AdvancingLoadStore:
    def __init__(
        self,
        store: SqliteGatewayInstallationStore,
        clock_state: dict[str, datetime],
        advanced_time: datetime,
    ) -> None:
        self.store = store
        self.clock_state = clock_state
        self.advanced_time = advanced_time
        self.load_count = 0

    def load(self, **identity: str) -> StoredGatewayBundle | None:
        bundle = self.store.load(**identity)
        self.load_count += 1
        if self.load_count >= 2:
            self.clock_state["now"] = self.advanced_time
        return bundle

    def current(self, **identity: str) -> GatewaySequenceCheckpoint | None:
        return self.store.current(**identity)

    def install(self, bundle: StoredGatewayBundle) -> None:
        self.store.install(bundle)


@pytest.mark.parametrize(
    ("changes", "load_channel"),
    [
        ({"sequence": 1}, "coding-defaults"),
        ({"channel": "tampered-channel"}, "tampered-channel"),
    ],
)
def test_store_rejects_checkpoint_fields_not_bound_to_signed_pointer(
    tmp_path: Path,
    changes: dict[str, object],
    load_channel: str,
) -> None:
    database = tmp_path / "state" / "gateway.sqlite3"
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
    _update_checkpoint(database, **changes)

    with (
        SqliteGatewayInstallationStore(database) as store,
        pytest.raises(GatewayStoreError, match="signed pointer"),
    ):
        store.load(
            trust_namespace="gateway-conformance",
            issuer="https://control.example/model-skyline",
            audience="wardwright-prod",
            channel=load_channel,
        )


def test_resolver_rejects_locally_derived_checkpoint_corruption(tmp_path: Path) -> None:
    database = tmp_path / "state" / "gateway.sqlite3"
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
    _update_checkpoint(database, target_bindings_sha256="0" * 64)

    with (
        SqliteGatewayInstallationStore(database) as store,
        pytest.raises(GatewayStoreError, match="target binding digest is corrupt"),
    ):
        SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=store,
            fetcher=OfflineFetcher(),
            clock=lambda: NOW,
        )


def test_resolver_adopts_concurrently_installed_generation_before_admission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "gateway.sqlite3"
    with (
        SqliteGatewayInstallationStore(database) as first_store,
        SqliteGatewayInstallationStore(database) as second_store,
    ):
        first_store.install(_bundle(7))
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=first_store,
            fetcher=OfflineFetcher(),
            refresh_interval=timedelta(hours=1),
            clock=lambda: NOW,
        )

        first = resolver.resolve()
        second = resolver.resolve()
        assert first.sequence == second.sequence == 7
        assert first.admission_source == second.admission_source == "last-known-good"

        second_store.install(_bundle(8))
        advanced = resolver.resolve()

    assert advanced.sequence == 8
    assert advanced.admission_source == "fresh"


def test_higher_signed_sequence_recovers_after_local_target_revision_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "gateway.sqlite3"
    original_policy = _policy()
    bindings = list(original_policy.target_bindings)
    bindings[0] = bindings[0].model_copy(update={"target_revision": "f" * 64})
    revised_policy = original_policy.model_copy(update={"target_bindings": tuple(bindings)})

    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))

    with SqliteGatewayInstallationStore(database) as store:
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=revised_policy,
            store=store,
            fetcher=EnvelopeFetcher(_envelope(8)),
            clock=lambda: NOW,
        )
        route = resolver.resolve()
        checkpoint = store.current(
            trust_namespace="gateway-conformance",
            issuer="https://control.example/model-skyline",
            audience="wardwright-prod",
            channel="coding-defaults",
        )

    assert route.sequence == 8
    assert route.targets[0].target_revision == "f" * 64
    assert checkpoint is not None
    assert checkpoint.sequence == 8


def test_store_rejects_dangling_final_symlink(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "gateway.sqlite3"
    target = state / "missing-target.sqlite3"
    database.symlink_to(target)

    with pytest.raises(GatewayStoreError, match="symbolic link"):
        SqliteGatewayInstallationStore(database)
    assert not target.exists()


def test_store_files_remain_private_under_permissive_umask(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    database = state / "gateway.sqlite3"
    previous_umask = os.umask(0)
    store: SqliteGatewayInstallationStore | None = None
    try:
        store = SqliteGatewayInstallationStore(database)
        store.install(_bundle(7))
        paths = [database, Path(f"{database}-wal"), Path(f"{database}-shm")]
        existing_modes = [stat.S_IMODE(path.stat().st_mode) for path in paths if path.exists()]
        assert existing_modes
        assert all(mode == 0o600 for mode in existing_modes)
    finally:
        if store is not None:
            store.close()
        os.umask(previous_umask)


@pytest.mark.parametrize("sequence", [6, 7])
def test_future_dated_rollback_or_equivocation_blocks_instead_of_using_lkg(
    tmp_path: Path,
    sequence: int,
) -> None:
    database = tmp_path / f"state-{sequence}" / "gateway.sqlite3"
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=store,
            fetcher=EnvelopeFetcher(_envelope(sequence, not_before=NOW + timedelta(minutes=1))),
            clock=lambda: NOW,
        )

        with pytest.raises(GatewayResolverError, match="failed closed"):
            resolver.resolve(force_refresh=True)
        assert resolver.status().state == GatewayResolverState.BLOCKED

        resolver.fetcher = OfflineFetcher()
        with pytest.raises(GatewayResolverError, match="remain blocked"):
            resolver.resolve(force_refresh=True)
        assert resolver.status().state == GatewayResolverState.BLOCKED


@pytest.mark.parametrize("followup", ["rollback", "malformed"])
def test_expired_refresh_keeps_subsequent_admissions_blocked(
    tmp_path: Path,
    followup: str,
) -> None:
    database = tmp_path / "state-expired" / "gateway.sqlite3"
    expired = _envelope(8, hard_expires_at=NOW + timedelta(minutes=1))
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=store,
            fetcher=EnvelopeFetcher(expired),
            clock=lambda: NOW + timedelta(minutes=2),
            allow_unexpired_lkg_after_security_error=True,
        )

        with pytest.raises(GatewayResolverError, match="hard expiry"):
            resolver.resolve(force_refresh=True)
        resolver.fetcher = EnvelopeFetcher(_envelope(6) if followup == "rollback" else b"not-json")
        with pytest.raises(GatewayResolverError, match="remain blocked"):
            resolver.resolve(force_refresh=True)
        status = resolver.status()
        assert status.state == GatewayResolverState.BLOCKED
        assert status.last_error_class == "expired"


def test_network_delay_cannot_install_or_admit_a_now_expired_generation(tmp_path: Path) -> None:
    database = tmp_path / "state-delayed" / "gateway.sqlite3"
    clock_state = {"now": NOW}
    candidate = _envelope(8, hard_expires_at=NOW + timedelta(minutes=1))
    fetcher = AdvancingEnvelopeFetcher(candidate, clock_state, NOW + timedelta(minutes=2))
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=store,
            fetcher=fetcher,
            clock=lambda: clock_state["now"],
        )

        with pytest.raises(GatewayResolverError, match="hard expiry"):
            resolver.resolve(force_refresh=True)
        checkpoint = store.current(
            trust_namespace="gateway-conformance",
            issuer="https://control.example/model-skyline",
            audience="wardwright-prod",
            channel="coding-defaults",
        )

    assert checkpoint is not None
    assert checkpoint.sequence == 7


def test_durable_sync_delay_cannot_admit_a_now_expired_generation(tmp_path: Path) -> None:
    database = tmp_path / "state-delayed-store" / "gateway.sqlite3"
    clock_state = {"now": NOW}
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
        advancing_store = AdvancingLoadStore(
            store,
            clock_state,
            NOW + timedelta(minutes=31),
        )
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=advancing_store,
            fetcher=OfflineFetcher(),
            clock=lambda: clock_state["now"],
        )

        with pytest.raises(GatewayResolverError, match="expiry"):
            resolver.resolve(force_refresh=True)


def test_resolver_fails_closed_if_its_wall_clock_moves_backward(tmp_path: Path) -> None:
    database = tmp_path / "state-clock" / "gateway.sqlite3"
    clock_state = {"now": NOW}
    with SqliteGatewayInstallationStore(database) as store:
        store.install(_bundle(7))
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=_policy(),
            store=store,
            fetcher=OfflineFetcher(),
            clock=lambda: clock_state["now"],
        )
        assert resolver.resolve().sequence == 7

        clock_state["now"] = NOW - timedelta(seconds=1)
        with pytest.raises(GatewayResolverError, match="clock moved backward"):
            resolver.resolve()


@pytest.mark.parametrize("escape", ["%2e%2e", "%2F", "%5c"])
def test_pointer_source_rejects_percent_encoded_path_separators(
    tmp_path: Path,
    escape: str,
) -> None:
    database = tmp_path / f"state-{escape}" / "gateway.sqlite3"
    with (
        SqliteGatewayInstallationStore(database) as store,
        pytest.raises(ValueError, match="non-canonical path"),
    ):
        SignedGatewayResolver(
            f"https://control.example/model-skyline/{escape}/latest.dsse.json",
            policy=_policy(),
            store=store,
            clock=lambda: NOW,
        )
