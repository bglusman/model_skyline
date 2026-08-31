from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from model_skyline.canonical import content_hash
from model_skyline.engine import FrontierEngine
from model_skyline.models import ObservationCatalog, ProjectConfig
from model_skyline.resolver import DynamicResolver, ResolverError
from model_skyline.selection import select_models, selection_hash, selection_hash_matches

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)


def test_default_resolver_loader_preserves_decimal_literals(tmp_path) -> None:
    path = tmp_path / "decimal.json"
    exact = "0.123456789012345678901234567890123456789"
    path.write_text(f'{{"value": {exact}}}', encoding="utf-8")

    payload, etag = DynamicResolver._load(
        str(path),
        None,
        1,
        allow_local_file=True,
    )

    assert payload is not None
    assert payload["value"] == Decimal(exact)
    assert etag is None


def test_builtin_resolver_requires_explicit_local_file_opt_in(tmp_path) -> None:
    path = tmp_path / "selection.json"

    with pytest.raises(ValueError, match="allow_local_file"):
        DynamicResolver(path, expected_selection_id="selection")

    resolver = DynamicResolver(
        path,
        expected_selection_id="selection",
        allow_local_file=True,
    )
    assert resolver.source == str(path)


def test_builtin_loader_rejects_oversized_local_artifacts(tmp_path) -> None:
    path = tmp_path / "selection.json"
    path.write_bytes(b'{"oversized":"value"}')

    with pytest.raises(ResolverError, match="exceeds 8 bytes"):
        DynamicResolver._load(
            str(path),
            None,
            1,
            allow_local_file=True,
            max_artifact_bytes=8,
        )


def test_builtin_loader_bounds_decompressed_remote_body(monkeypatch) -> None:
    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            pass

        def iter_bytes(self):
            yield b'{"first":'
            yield b'"second"}'

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "model_skyline.resolver.httpx.stream",
        lambda *args, **kwargs: Stream(),
    )

    with pytest.raises(ResolverError, match="exceeds 12 bytes"):
        DynamicResolver._load(
            "https://trusted.example/selection.json",
            None,
            1,
            allowed_hosts={"trusted.example"},
            max_artifact_bytes=12,
        )


def test_builtin_resolver_enforces_an_exact_host_allowlist() -> None:
    with pytest.raises(ValueError, match="is not allowed"):
        DynamicResolver(
            "https://other.example/selection.json",
            expected_selection_id="selection",
            allowed_hosts={"trusted.example"},
        )

    resolver = DynamicResolver(
        "https://TRUSTED.example/selection.json",
        expected_selection_id="selection",
        allowed_hosts={"trusted.example."},
    )
    assert resolver.allowed_hosts == frozenset({"trusted.example"})


def test_builtin_resolver_rejects_http_by_default() -> None:
    with pytest.raises(ValueError, match="plain HTTP"):
        DynamicResolver(
            "http://example.test/selection.json",
            expected_selection_id="selection",
        )


@pytest.mark.parametrize(
    "source",
    (
        "https://example.test/selection.json?token=do-not-log",
        "https://example.test/selection.json#token=do-not-log",
    ),
)
def test_builtin_resolver_rejects_query_and_fragment_without_echoing_them(source: str) -> None:
    with pytest.raises(ValueError, match="query or fragment") as error:
        DynamicResolver(source, expected_selection_id="selection")

    assert "do-not-log" not in str(error.value)


def test_builtin_loader_sanitizes_remote_http_errors(monkeypatch) -> None:
    secret_path = "https://trusted.example/do-not-log/selection.json"
    request = httpx.Request("GET", secret_path)
    response = httpx.Response(503, request=request)

    class Response:
        status_code = 503
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                f"upstream failed for {secret_path}",
                request=request,
                response=response,
            )

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "model_skyline.resolver.httpx.stream",
        lambda *args, **kwargs: Stream(),
    )

    with pytest.raises(ResolverError, match="HTTP status 503") as error:
        DynamicResolver._load(
            secret_path,
            None,
            1,
            allowed_hosts={"trusted.example"},
        )

    assert "do-not-log" not in str(error.value)
    assert error.value.__cause__ is None


def test_resolver_sanitizes_rejected_artifact_values() -> None:
    secret = "sk-do-not-log-this-rejected-value"
    resolver = DynamicResolver(
        "memory://selection",
        expected_selection_id="selection",
        loader=lambda *_args: (
            {
                "kind": "selection",
                "selection_id": "selection",
                "operator_secret": secret,
            },
            None,
        ),
        clock=lambda: NOW,
    )

    with pytest.raises(ResolverError, match="does not match its declared kind") as error:
        resolver.resolve()

    assert secret not in str(error.value)
    assert error.value.__cause__ is None


def test_builtin_local_loader_rejects_symlinks_and_fifos(tmp_path) -> None:
    target = tmp_path / "selection-target.json"
    target.write_text('{"value": 1}', encoding="utf-8")
    symlink = tmp_path / "selection-link.json"
    symlink.symlink_to(target)

    with pytest.raises(ResolverError, match="securely open"):
        DynamicResolver._load(str(symlink), None, 1, allow_local_file=True)

    if not hasattr(os, "mkfifo"):
        return
    fifo = tmp_path / "selection.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ResolverError, match="not a regular file"):
        DynamicResolver._load(str(fifo), None, 1, allow_local_file=True)


def test_builtin_local_loader_rejects_identity_change(tmp_path, monkeypatch) -> None:
    path = tmp_path / "selection.json"
    path.write_text('{"value": 1}', encoding="utf-8")
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        status = original_fstat(descriptor)
        if calls == 1:
            return status
        return SimpleNamespace(
            st_mode=status.st_mode,
            st_dev=status.st_dev,
            st_ino=status.st_ino,
            st_size=status.st_size,
            st_mtime_ns=status.st_mtime_ns + 1,
            st_ctime_ns=status.st_ctime_ns,
        )

    monkeypatch.setattr("model_skyline.resolver.os.fstat", changing_fstat)

    with pytest.raises(ResolverError, match="changed while it was being read"):
        DynamicResolver._load(str(path), None, 1, allow_local_file=True)


@pytest.mark.parametrize(
    "payload",
    (
        b'{"sensitive-key": 1, "sensitive-key": 2}',
        b'{"value":' + b"[" * 65 + b"0" + b"]" * 65 + b"}",
    ),
)
def test_builtin_loader_rejects_ambiguous_or_deep_json_without_echoing_input(
    tmp_path,
    payload: bytes,
) -> None:
    path = tmp_path / "selection.json"
    path.write_bytes(payload)

    with pytest.raises(ResolverError, match="structural limits") as error:
        DynamicResolver._load(str(path), None, 1, allow_local_file=True)

    assert "sensitive-key" not in str(error.value)


def _selection(config: ProjectConfig, catalog: ObservationCatalog):
    frontier = FrontierEngine().calculate(config, catalog, "coding-value", generated_at=NOW)
    return select_models(config, frontier, "coding-agent-defaults")


def _rehash(snapshot, **updates):
    provisional = snapshot.model_copy(update={**updates, "snapshot_id": "pending"})
    return provisional.model_copy(update={"snapshot_id": selection_hash(provisional)})


def test_selection_is_quality_ordered_and_hash_bound(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = _selection(example_config, example_catalog)

    assert snapshot.default.offering.provider == "qualityworks"
    assert [item.offering.provider for item in snapshot.fallbacks] == [
        "balancedai",
        "fastcloud",
    ]
    assert snapshot.snapshot_id == selection_hash(snapshot)
    assert snapshot.frontier_snapshot_id


def test_resolver_accepts_pre_field_and_v040_null_billing_hashes(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = _selection(example_config, example_catalog)
    pre_field_payload = snapshot.model_dump(mode="json")
    for choice in (pre_field_payload["default"], *pre_field_payload["fallbacks"]):
        if choice["offering"]["billing_mode"] is None:
            choice["offering"].pop("billing_mode")

    restored = type(snapshot).model_validate(pre_field_payload)
    assert selection_hash_matches(restored)
    resolver = DynamicResolver(
        "https://example.test/selection.json",
        expected_selection_id="coding-agent-defaults",
        loader=lambda *_args: (pre_field_payload, None),
        clock=lambda: NOW,
    )
    assert resolver.resolve().snapshot_id == snapshot.snapshot_id

    explicit_null_hash = content_hash(snapshot.model_dump(mode="json", exclude={"snapshot_id"}))
    assert explicit_null_hash != snapshot.snapshot_id
    assert selection_hash_matches(snapshot.model_copy(update={"snapshot_id": explicit_null_hash}))


def test_resolver_uses_bounded_last_known_good(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = _selection(example_config, example_catalog)
    calls = 0
    now = [NOW + timedelta(minutes=30)]

    def loader(
        source: str, etag: str | None, timeout: float
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return snapshot.model_dump(mode="json"), '"v1"'
        raise OSError("temporary outage")

    resolver = DynamicResolver(
        "https://example.test/selection.json",
        expected_selection_id="coding-agent-defaults",
        expected_frontier_id="coding-value",
        expected_workload_id="coding-session-v1",
        expected_workload_version="1.0.0",
        refresh_interval=timedelta(0),
        stale_if_error=timedelta(hours=1),
        loader=loader,
        clock=lambda: now[0],
    )

    assert resolver.resolve().snapshot_id == snapshot.snapshot_id
    now[0] = NOW + timedelta(hours=1, minutes=30)
    assert resolver.resolve(force_refresh=True).snapshot_id == snapshot.snapshot_id
    now[0] = NOW + timedelta(hours=2, minutes=1)
    with pytest.raises(ResolverError, match="refresh"):
        resolver.resolve(force_refresh=True)


def test_resolver_rejects_tampered_snapshot(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    payload = _selection(example_config, example_catalog).model_dump(mode="json")
    payload["default"]["offering"]["model_id"] = "tampered"

    def loader(
        source: str, etag: str | None, timeout: float
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        return payload, None

    resolver = DynamicResolver(
        "memory://selection",
        expected_selection_id="coding-agent-defaults",
        loader=loader,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    with pytest.raises(ResolverError, match="hash mismatch"):
        resolver.resolve()


def test_resolver_rejects_valid_hash_for_wrong_selection(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    wrong = _rehash(_selection(example_config, example_catalog), selection_id="other-policy")

    resolver = DynamicResolver(
        "memory://selection",
        expected_selection_id="coding-agent-defaults",
        loader=lambda source, etag, timeout: (wrong.model_dump(mode="json"), None),
        clock=lambda: NOW + timedelta(minutes=1),
    )

    with pytest.raises(ResolverError, match="identity mismatch") as error:
        resolver.resolve()
    assert "other-policy" not in str(error.value)


def test_resolver_rejects_future_manifest_without_cached_fallback(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = _selection(example_config, example_catalog)
    future = _rehash(
        snapshot,
        generated_at=NOW + timedelta(minutes=10),
        valid_until=NOW + timedelta(hours=1),
    )
    resolver = DynamicResolver(
        "memory://selection",
        expected_selection_id="coding-agent-defaults",
        loader=lambda source, etag, timeout: (future.model_dump(mode="json"), None),
        clock=lambda: NOW,
    )

    with pytest.raises(ResolverError, match="future"):
        resolver.resolve()


def test_resolver_returns_defensive_copies(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = _selection(example_config, example_catalog)
    resolver = DynamicResolver(
        "memory://selection",
        expected_selection_id="coding-agent-defaults",
        refresh_interval=timedelta(hours=1),
        loader=lambda source, etag, timeout: (snapshot.model_dump(mode="json"), None),
        clock=lambda: NOW + timedelta(minutes=1),
    )

    first = resolver.resolve()
    first.default.axes.clear()
    second = resolver.resolve()

    assert second.default.axes
    assert second.snapshot_id == snapshot.snapshot_id


def test_resolver_rejects_ordinary_rollback_and_same_generation_equivocation(
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    snapshot = _selection(example_config, example_catalog)
    rollback = _rehash(
        snapshot,
        generated_at=snapshot.generated_at - timedelta(minutes=1),
    )
    equivocation = _rehash(
        snapshot,
        valid_until=snapshot.valid_until + timedelta(minutes=1),
    )

    for candidate, message in (
        (rollback, "roll back"),
        (equivocation, "equivocate"),
    ):
        payloads = iter((snapshot, candidate))
        resolver = DynamicResolver(
            "memory://selection",
            expected_selection_id="coding-agent-defaults",
            refresh_interval=timedelta(0),
            loader=lambda *_args, stream=payloads: (
                next(stream).model_dump(mode="json"),
                None,
            ),
            clock=lambda: NOW + timedelta(minutes=1),
        )
        assert resolver.resolve().snapshot_id == snapshot.snapshot_id
        with pytest.raises(ResolverError, match=message):
            resolver.resolve(force_refresh=True)
