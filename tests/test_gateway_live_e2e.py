from __future__ import annotations

import hashlib
import ipaddress
import os
import ssl
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from model_skyline.gateway import (
    GATEWAY_ENVELOPE_MEDIA_TYPE,
    GatewaySelectionPointer,
    GatewayTargetBinding,
    GatewayTrustPolicy,
    PinnedGatewayRoute,
    build_gateway_pointer,
    envelope_bytes,
    parse_selection_artifact,
    sign_gateway_pointer,
    trusted_gateway_key,
)
from model_skyline.gateway_resolver import (
    GatewayResolverState,
    HttpxGatewayFetcher,
    SignedGatewayResolver,
)
from model_skyline.gateway_store import SqliteGatewayInstallationStore
from model_skyline.models import ObservationCatalog, ProjectConfig
from model_skyline.publisher import publish_project

GENERATED_AT_10 = datetime(2026, 8, 31, 12, tzinfo=UTC)
GENERATED_AT_11 = GENERATED_AT_10 + timedelta(minutes=5)
ISSUER_PATH = "/model-skyline"
SOURCE_PATH = f"{ISSUER_PATH}/channels/coding-defaults.dsse.json"


@dataclass(frozen=True, slots=True)
class _Request:
    path: str
    accept: str | None
    accept_encoding: str | None
    if_none_match: str | None
    status: int


@dataclass(slots=True)
class _OriginState:
    site: Path
    envelope: bytes | None = None
    etag: str | None = None
    requests: list[_Request] = field(default_factory=list)

    def activate(self, envelope: bytes) -> str:
        etag = f'"{hashlib.sha256(envelope).hexdigest()}"'
        self.envelope = envelope
        self.etag = etag
        return etag


class _GatewayOrigin(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: _OriginState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), _GatewayRequestHandler)


class _GatewayRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        state = cast(_GatewayOrigin, self.server).state
        status = 200
        etag: str | None = None

        if self.path == SOURCE_PATH:
            envelope = state.envelope
            etag = state.etag
            if envelope is None or etag is None:
                status, media_type, payload = 503, "text/plain", b"generation unavailable"
            elif self.headers.get("If-None-Match") == etag:
                status, media_type, payload = 304, GATEWAY_ENVELOPE_MEDIA_TYPE, b""
            else:
                media_type, payload = GATEWAY_ENVELOPE_MEDIA_TYPE, envelope
        elif self.path.startswith(f"{ISSUER_PATH}/"):
            relative = self.path.removeprefix(f"{ISSUER_PATH}/")
            site = state.site.resolve()
            target = (site / relative).resolve()
            if target.parent != site and site not in target.parents:
                status, media_type, payload = 403, "text/plain", b"forbidden"
            elif not target.is_file():
                status, media_type, payload = 404, "text/plain", b"not found"
            else:
                media_type, payload = "application/json", target.read_bytes()
                etag = f'"{hashlib.sha256(payload).hexdigest()}"'
        else:
            status, media_type, payload = 404, "text/plain", b"not found"

        state.requests.append(
            _Request(
                path=self.path,
                accept=self.headers.get("Accept"),
                accept_encoding=self.headers.get("Accept-Encoding"),
                if_none_match=self.headers.get("If-None-Match"),
                status=status,
            )
        )
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(payload)))
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@dataclass(frozen=True, slots=True)
class _TlsMaterial:
    authority: Path
    certificate: Path
    private_key: Path


def _write_tls_material(root: Path) -> _TlsMaterial:
    not_before = datetime(2020, 1, 1, tzinfo=UTC)
    not_after = datetime(2099, 1, 1, tzinfo=UTC)
    authority_key = generate_private_key(public_exponent=65537, key_size=2048)
    authority_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "ModelSkyline loopback test authority")]
    )
    authority_certificate = (
        x509.CertificateBuilder()
        .subject_name(authority_name)
        .issuer_name(authority_name)
        .public_key(authority_key.public_key())
        .serial_number(1)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(authority_key, hashes.SHA256())
    )

    server_key = generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    server_certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(authority_name)
        .public_key(server_key.public_key())
        .serial_number(2)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(authority_key, hashes.SHA256())
    )

    authority_path = root / "loopback-ca.pem"
    certificate_path = root / "loopback-server.pem"
    private_key_path = root / "loopback-server-key.pem"
    authority_path.write_bytes(authority_certificate.public_bytes(serialization.Encoding.PEM))
    certificate_path.write_bytes(server_certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    if os.name == "posix":
        os.chmod(private_key_path, 0o600)
    return _TlsMaterial(
        authority=authority_path,
        certificate=certificate_path,
        private_key=private_key_path,
    )


@contextmanager
def _serve_tls(state: _OriginState, material: _TlsMaterial) -> Iterator[_GatewayOrigin]:
    server = _GatewayOrigin(state)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(material.certificate, material.private_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("loopback gateway test server did not stop")


@dataclass(frozen=True, slots=True)
class _Generation:
    pointer: GatewaySelectionPointer
    publication: bytes
    selection: bytes
    envelope: bytes


def _publish_generation(
    *,
    config: ProjectConfig,
    catalog: ObservationCatalog,
    site: Path,
    issuer: str,
    sequence: int,
    generated_at: datetime,
    signing_key: Ed25519PrivateKey,
) -> _Generation:
    result = publish_project(
        config,
        [catalog],
        site,
        project_id="gateway-demo",
        frontier_ids=["coding-value"],
        selection_ids=["coding-agent-defaults"],
        generated_at=generated_at,
        base_url=issuer,
    )
    publication = (site / "publications" / f"{result.manifest.publication_id}.json").read_bytes()
    selection = (site / result.manifest.selections[0].snapshot.path).read_bytes()
    pointer = build_gateway_pointer(
        publication,
        selection,
        issuer=issuer,
        audience=["loopback-e2e"],
        channel="coding-defaults",
        sequence=sequence,
        selection_id="coding-agent-defaults",
        required_capabilities=["structured_output", "tools"],
        issued_at=generated_at,
        hard_expires_at=generated_at + timedelta(minutes=30),
    )
    envelope = envelope_bytes(sign_gateway_pointer(pointer, (signing_key,)))
    return _Generation(
        pointer=pointer,
        publication=publication,
        selection=selection,
        envelope=envelope,
    )


def _assert_private_store_files(database: Path) -> None:
    if os.name != "posix":
        return
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_live_signed_gateway_rotation_and_offline_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    # pytest's macOS temporary root may be spelled through the /var symlink,
    # while publisher roots intentionally reject every symlink component.
    root = tmp_path.resolve(strict=True)
    site = root / "site"
    state = _OriginState(site=site)
    tls_material = _write_tls_material(root)
    signing_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    database = root / "private-state" / "gateway-state.sqlite3"
    clock = {"now": GENERATED_AT_10 + timedelta(minutes=1)}

    for variable in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_material.authority))

    work_unit_b: PinnedGatewayRoute
    source: str
    policy: GatewayTrustPolicy
    generation_11: _Generation
    with _serve_tls(state, tls_material) as server:
        port = int(server.server_address[1])
        issuer = f"https://127.0.0.1:{port}{ISSUER_PATH}"
        source = f"https://127.0.0.1:{port}{SOURCE_PATH}"

        generation_10 = _publish_generation(
            config=example_config,
            catalog=example_catalog,
            site=site,
            issuer=issuer,
            sequence=10,
            generated_at=GENERATED_AT_10,
            signing_key=signing_key,
        )
        etag_10 = state.activate(generation_10.envelope)
        selection_10 = parse_selection_artifact(generation_10.selection)
        bindings = tuple(
            GatewayTargetBinding(
                offering=choice.offering,
                target_id=f"runtime-target-{index}",
                target_revision=hashlib.sha256(f"target-revision-{index}".encode()).hexdigest(),
                capabilities=choice.offering.capabilities,
            )
            for index, choice in enumerate(selection_10.choices)
        )
        policy = GatewayTrustPolicy(
            trust_namespace="loopback-e2e",
            issuer=issuer,
            audience="loopback-e2e",
            channel="coding-defaults",
            project_id="gateway-demo",
            trusted_keys=(trusted_gateway_key(signing_key.public_key()),),
            expected_selection_id="coding-agent-defaults",
            expected_frontier_id="coding-value",
            expected_workload=generation_10.pointer.selection.workload,
            required_capabilities=("structured_output", "tools"),
            target_bindings=bindings,
            minimum_sequence=10,
        )

        with SqliteGatewayInstallationStore(database) as store:
            resolver = SignedGatewayResolver(
                source,
                policy=policy,
                store=store,
                clock=lambda: clock["now"],
                timeout_seconds=1,
            )
            assert isinstance(resolver.fetcher, HttpxGatewayFetcher)
            work_unit_a = resolver.resolve()
            pinned_a = work_unit_a.model_dump(mode="json")
            assert work_unit_a.sequence == 10
            assert work_unit_a.admission_source == "fresh"
            assert resolver.status().state is GatewayResolverState.READY

            generation_11 = _publish_generation(
                config=example_config,
                catalog=example_catalog,
                site=site,
                issuer=issuer,
                sequence=11,
                generated_at=GENERATED_AT_11,
                signing_key=signing_key,
            )
            etag_11 = state.activate(generation_11.envelope)
            assert etag_11 != etag_10
            clock["now"] = GENERATED_AT_11 + timedelta(minutes=1)
            work_unit_b = resolver.resolve(force_refresh=True)

            assert work_unit_a.model_dump(mode="json") == pinned_a
            assert work_unit_a.sequence == 10
            assert work_unit_b.sequence == 11
            assert work_unit_b.admission_source == "fresh"
            assert work_unit_b.payload_sha256 != work_unit_a.payload_sha256
            assert work_unit_b.selection_snapshot_id != work_unit_a.selection_snapshot_id
            assert resolver.status().state is GatewayResolverState.READY

            repeated = resolver.resolve(force_refresh=True)
            assert repeated.sequence == 11
            assert repeated.payload_sha256 == work_unit_b.payload_sha256
            checkpoint = store.current(
                trust_namespace=policy.trust_namespace,
                issuer=policy.issuer,
                audience=policy.audience,
                channel=policy.channel,
            )
            assert checkpoint is not None
            assert checkpoint.sequence == 11
            _assert_private_store_files(database)

        pointer_requests = [request for request in state.requests if request.path == SOURCE_PATH]
        assert [request.status for request in pointer_requests] == [200, 200, 304]
        assert [request.if_none_match for request in pointer_requests] == [
            None,
            etag_10,
            etag_11,
        ]
        assert all(request.accept == GATEWAY_ENVELOPE_MEDIA_TYPE for request in pointer_requests)
        artifact_requests = [request for request in state.requests if request.path != SOURCE_PATH]
        assert len(artifact_requests) == 4
        assert all(request.status == 200 for request in artifact_requests)
        assert all(request.accept == "application/json" for request in artifact_requests)
        assert all(request.accept_encoding == "identity" for request in state.requests)

    # The listening socket is now closed. Construction re-verifies the exact
    # installed bytes, and the failed refresh may admit only that unexpired LKG.
    clock["now"] = GENERATED_AT_11 + timedelta(minutes=2)
    with SqliteGatewayInstallationStore(database) as restarted_store:
        restarted = SignedGatewayResolver(
            source,
            policy=policy,
            store=restarted_store,
            clock=lambda: clock["now"],
            timeout_seconds=0.5,
        )
        assert isinstance(restarted.fetcher, HttpxGatewayFetcher)
        after_restart = restarted.resolve(force_refresh=True)
        status = restarted.status()
        durable = restarted_store.current(
            trust_namespace=policy.trust_namespace,
            issuer=policy.issuer,
            audience=policy.audience,
            channel=policy.channel,
        )
        assert durable is not None
        assert durable.sequence == after_restart.sequence == 11
        assert after_restart.payload_sha256 == work_unit_b.payload_sha256
        assert after_restart.selection_snapshot_id == work_unit_b.selection_snapshot_id
        assert after_restart.admission_source == "last-known-good"
        assert status.state is GatewayResolverState.DEGRADED
        assert status.last_error_class == "transport"
        _assert_private_store_files(database)

    _assert_private_store_files(database)
    assert generation_10.publication != generation_11.publication
    assert generation_10.selection != generation_11.selection
