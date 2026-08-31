from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from model_skyline.gateway import (
    GATEWAY_ENVELOPE_MEDIA_TYPE,
    GATEWAY_POINTER_PAYLOAD_TYPE,
    DsseSignature,
    GatewayProtocolError,
    GatewaySequenceError,
    GatewayTargetBinding,
    GatewayTrustPolicy,
    InMemoryGatewaySequenceStore,
    StoredGatewayBundle,
    build_gateway_pointer,
    build_stored_gateway_bundle,
    dsse_pae,
    envelope_bytes,
    gateway_key_id,
    parse_dsse_envelope,
    parse_selection_artifact,
    pin_gateway_route,
    pointer_bytes,
    sign_gateway_pointer,
    trusted_gateway_key,
    verify_gateway_bundle,
    verify_gateway_envelope,
)
from model_skyline.gateway_resolver import (
    GatewayFetchResult,
    GatewayResolverError,
    GatewayResolverState,
    GatewayTransportError,
    SignedGatewayResolver,
)
from model_skyline.gateway_store import SqliteGatewayInstallationStore
from model_skyline.models import ObservationCatalog, ProjectConfig
from model_skyline.publisher import publish_project

NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)
ISSUER = "https://control.example/model-skyline"
SOURCE = f"{ISSUER}/gateway/coding-defaults/latest.dsse.json"


@dataclass(frozen=True)
class GatewayMaterials:
    publication: bytes
    selection: bytes
    envelope: bytes
    policy: GatewayTrustPolicy
    key: Ed25519PrivateKey


def _private_key(seed: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes(range(seed, seed + 32)))


def _materials(
    root: Path,
    config: ProjectConfig,
    catalog: ObservationCatalog,
    *,
    sequence: int = 7,
    keys: tuple[Ed25519PrivateKey, ...] | None = None,
    threshold: int = 1,
) -> GatewayMaterials:
    result = publish_project(
        config,
        [catalog],
        root,
        project_id="gateway-demo",
        selection_ids=["coding-agent-defaults"],
        generated_at=NOW,
    )
    publication = (root / "publications" / f"{result.manifest.publication_id}.json").read_bytes()
    published = result.manifest.selections[0]
    selection = (root / published.snapshot.path).read_bytes()
    signing_keys = keys or (_private_key(1),)
    pointer = build_gateway_pointer(
        publication,
        selection,
        issuer=ISSUER,
        audience=["wardwright-prod"],
        channel="coding-defaults",
        sequence=sequence,
        selection_id="coding-agent-defaults",
        issued_at=NOW,
        hard_expires_at=NOW + timedelta(minutes=30),
    )
    snapshot = parse_selection_artifact(selection)
    bindings = tuple(
        GatewayTargetBinding(
            offering=choice.offering,
            target_id=f"target-{index}",
            target_revision=f"{index + 1:064x}",
            capabilities=choice.offering.capabilities,
        )
        for index, choice in enumerate(snapshot.choices)
    )
    policy = GatewayTrustPolicy(
        trust_namespace="gateway-tests",
        issuer=ISSUER,
        audience="wardwright-prod",
        channel="coding-defaults",
        project_id="gateway-demo",
        trusted_keys=tuple(trusted_gateway_key(key.public_key()) for key in signing_keys),
        signature_threshold=threshold,
        expected_selection_id="coding-agent-defaults",
        expected_frontier_id="coding-value",
        expected_workload=pointer.selection.workload,
        target_bindings=bindings,
    )
    return GatewayMaterials(
        publication=publication,
        selection=selection,
        envelope=envelope_bytes(sign_gateway_pointer(pointer, signing_keys)),
        policy=policy,
        key=signing_keys[0],
    )


def _resign_payload(
    material: GatewayMaterials,
    payload: bytes,
) -> bytes:
    original = parse_dsse_envelope(material.envelope)
    signature = material.key.sign(dsse_pae(GATEWAY_POINTER_PAYLOAD_TYPE, payload))
    envelope = original.model_copy(
        update={
            "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": (
                DsseSignature(
                    keyid=gateway_key_id(material.key.public_key()),
                    sig=base64.b64encode(signature).decode("ascii"),
                ),
            ),
        }
    )
    return envelope_bytes(envelope)


def _bundle_for_pointer(
    material: GatewayMaterials,
    *,
    sequence: int,
    hard_expires_at: datetime,
) -> StoredGatewayBundle:
    pointer = build_gateway_pointer(
        material.publication,
        material.selection,
        issuer=ISSUER,
        audience=["wardwright-prod"],
        channel="coding-defaults",
        sequence=sequence,
        selection_id="coding-agent-defaults",
        issued_at=NOW,
        hard_expires_at=hard_expires_at,
    )
    envelope = envelope_bytes(sign_gateway_pointer(pointer, (material.key,)))
    verified = verify_gateway_bundle(
        envelope,
        material.publication,
        material.selection,
        material.policy,
        now=NOW,
    )
    return build_stored_gateway_bundle(
        verified,
        envelope_payload=envelope,
        publication_payload=material.publication,
        selection_payload=material.selection,
        installed_at=NOW,
    )


def test_dsse_bundle_verifies_four_hash_domains_and_pins_ordered_targets(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)

    verified = verify_gateway_bundle(
        material.envelope,
        material.publication,
        material.selection,
        material.policy,
        now=NOW,
    )
    route = pin_gateway_route(verified, now=NOW)

    assert verified.publication.publication_id == verified.pointer.publication.publication_id
    assert verified.selection.snapshot_id == verified.pointer.selection.snapshot_id
    assert verified.checkpoint.payload_sha256 != verified.checkpoint.selection_artifact_sha256
    assert route.payload_sha256 == verified.authenticated_pointer.payload_sha256
    assert [target.target_id for target in route.targets] == [
        f"target-{index}" for index in range(len(verified.selection.choices))
    ]
    assert route.admission_source == "fresh"


def test_dsse_threshold_counts_public_keys_not_unsigned_keyid_hints(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    key_1, key_2 = _private_key(1), _private_key(33)
    material = _materials(
        tmp_path / "site",
        example_config,
        example_catalog,
        keys=(key_1, key_2),
        threshold=2,
    )
    envelope = parse_dsse_envelope(material.envelope)
    duplicate = envelope.model_copy(
        update={
            "signatures": (
                envelope.signatures[0],
                DsseSignature(
                    keyid=envelope.signatures[1].keyid,
                    sig=envelope.signatures[0].sig,
                ),
            )
        }
    )
    with pytest.raises(GatewayProtocolError, match="distinct-key threshold"):
        verify_gateway_envelope(envelope_bytes(duplicate), material.policy, now=NOW)

    one_key_policy = material.policy.model_copy(
        update={
            "trusted_keys": (trusted_gateway_key(key_1.public_key()),),
            "signature_threshold": 1,
        }
    )
    one_signature = sign_gateway_pointer(
        verify_gateway_envelope(material.envelope, material.policy, now=NOW).pointer,
        (key_1,),
    )
    mutated_hint = one_signature.model_copy(
        update={
            "signatures": (
                one_signature.signatures[0].model_copy(
                    update={"keyid": gateway_key_id(key_2.public_key())}
                ),
            )
        }
    )
    authenticated = verify_gateway_envelope(
        envelope_bytes(mutated_hint),
        one_key_policy,
        now=NOW,
    )
    assert authenticated.verified_key_ids == (gateway_key_id(key_1.public_key()),)


def test_signature_raw_digest_and_exact_offering_mapping_fail_closed(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    envelope = parse_dsse_envelope(material.envelope)
    signed_payload = base64.b64decode(envelope.payload)
    tampered_payload = signed_payload.replace(b'"sequence":7', b'"sequence":8')
    tampered_envelope = envelope.model_copy(
        update={"payload": base64.b64encode(tampered_payload).decode("ascii")}
    )
    with pytest.raises(GatewayProtocolError, match="threshold"):
        verify_gateway_envelope(envelope_bytes(tampered_envelope), material.policy, now=NOW)

    with pytest.raises(GatewayProtocolError, match="length"):
        verify_gateway_bundle(
            material.envelope,
            material.publication,
            material.selection + b" ",
            material.policy,
            now=NOW,
        )

    incomplete_policy = material.policy.model_copy(
        update={"target_bindings": material.policy.target_bindings[:-1]}
    )
    with pytest.raises(GatewayProtocolError, match="no exact local target binding"):
        verify_gateway_bundle(
            material.envelope,
            material.publication,
            material.selection,
            incomplete_policy,
            now=NOW,
        )


def test_pointer_parser_rejects_noncanonical_and_duplicate_json_after_authentication(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    authenticated = verify_gateway_envelope(material.envelope, material.policy, now=NOW)
    canonical = pointer_bytes(authenticated.pointer)
    noncanonical = json.dumps(json.loads(canonical), indent=2).encode()
    with pytest.raises(GatewayProtocolError, match="not RFC 8785 canonical"):
        verify_gateway_envelope(_resign_payload(material, noncanonical), material.policy, now=NOW)

    duplicate = canonical.replace(
        b'{"audience":',
        b'{"sequence":7,"audience":',
        1,
    )
    with pytest.raises(GatewayProtocolError, match="duplicate JSON member"):
        verify_gateway_envelope(_resign_payload(material, duplicate), material.policy, now=NOW)


def test_hard_expiry_is_exclusive_and_request_capabilities_only_narrow(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    authenticated = verify_gateway_envelope(material.envelope, material.policy, now=NOW)
    with pytest.raises(GatewayProtocolError, match="hard expiry"):
        verify_gateway_envelope(
            material.envelope,
            material.policy,
            now=authenticated.pointer.hard_expires_at,
        )
    verified = verify_gateway_bundle(
        material.envelope,
        material.publication,
        material.selection,
        material.policy,
        now=NOW,
    )
    with pytest.raises(GatewayProtocolError, match="no signed target"):
        pin_gateway_route(verified, now=NOW, required_capabilities=("vision",))
    with pytest.raises(GatewayProtocolError, match="expiry headroom"):
        pin_gateway_route(verified, now=NOW, minimum_headroom=timedelta(minutes=30))


def test_sequence_store_survives_restart_and_rejects_rollback_and_equivocation(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    verified = verify_gateway_bundle(
        material.envelope,
        material.publication,
        material.selection,
        material.policy,
        now=NOW,
    )
    bundle = build_stored_gateway_bundle(
        verified,
        envelope_payload=material.envelope,
        publication_payload=material.publication,
        selection_payload=material.selection,
        installed_at=NOW,
    )
    database = tmp_path / "state" / "gateway.sqlite3"
    with SqliteGatewayInstallationStore(database) as store:
        store.install(bundle)
        store.install(bundle)
        assert (
            store.current(
                trust_namespace="gateway-tests",
                issuer=ISSUER,
                audience="wardwright-prod",
                channel="coding-defaults",
            )
            == verified.checkpoint
        )
    with SqliteGatewayInstallationStore(database) as store:
        restored = store.load(
            trust_namespace="gateway-tests",
            issuer=ISSUER,
            audience="wardwright-prod",
            channel="coding-defaults",
        )
        assert restored is not None
        rollback = _bundle_for_pointer(
            material,
            sequence=6,
            hard_expires_at=NOW + timedelta(minutes=30),
        )
        with pytest.raises(GatewaySequenceError, match="roll back"):
            store.install(rollback)
        equivocation = _bundle_for_pointer(
            material,
            sequence=7,
            hard_expires_at=NOW + timedelta(minutes=20),
        )
        with pytest.raises(GatewaySequenceError, match="equivocate"):
            store.install(equivocation)


class FakeFetcher:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.source_error: Exception | None = None

    def fetch(
        self,
        url: str,
        *,
        expected_media_type: str,
        maximum_bytes: int,
        timeout_seconds: float,
        etag: str | None = None,
    ) -> GatewayFetchResult:
        del expected_media_type, timeout_seconds, etag
        if url == SOURCE and self.source_error is not None:
            raise self.source_error
        payload = self.responses[url]
        assert len(payload) <= maximum_bytes
        return GatewayFetchResult(payload=payload, etag='"fixture"' if url == SOURCE else None)


def test_signed_resolver_commits_before_activation_and_distinguishes_lkg_from_security(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    pointer = verify_gateway_envelope(material.envelope, material.policy, now=NOW).pointer
    fetcher = FakeFetcher(
        {
            SOURCE: material.envelope,
            f"{ISSUER}/{pointer.publication.file.path}": material.publication,
            f"{ISSUER}/{pointer.selection.file.path}": material.selection,
        }
    )
    clock_value = NOW
    with SqliteGatewayInstallationStore(tmp_path / "resolver.sqlite3") as store:
        resolver = SignedGatewayResolver(
            SOURCE,
            policy=material.policy,
            store=store,
            fetcher=fetcher,
            clock=lambda: clock_value,
        )
        fresh = resolver.resolve()
        assert fresh.admission_source == "fresh"
        assert (
            store.current(
                trust_namespace="gateway-tests",
                issuer=ISSUER,
                audience="wardwright-prod",
                channel="coding-defaults",
            )
            is not None
        )

        fetcher.source_error = GatewayTransportError("offline")
        lkg = resolver.resolve(force_refresh=True)
        assert lkg.admission_source == "last-known-good"
        assert resolver.status().state is GatewayResolverState.DEGRADED

        fetcher.source_error = GatewayProtocolError("invalid signature")
        with pytest.raises(GatewayResolverError, match="failed closed"):
            resolver.resolve(force_refresh=True)
        assert resolver.status().state is GatewayResolverState.BLOCKED


def test_protocol_models_reject_duplicate_targets_and_noncanonical_issuer(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    invalid_targets = material.policy.model_dump(mode="json")
    invalid_targets["target_bindings"] = [
        material.policy.target_bindings[0].model_dump(mode="json"),
        material.policy.target_bindings[0].model_dump(mode="json"),
    ]
    with pytest.raises(ValidationError, match="distinct complete OfferingKeys"):
        GatewayTrustPolicy.model_validate(invalid_targets)
    invalid = material.policy.model_dump(mode="json")
    invalid["issuer"] = f"{ISSUER}/"
    with pytest.raises(ValidationError, match="trailing slash"):
        GatewayTrustPolicy.model_validate(invalid)


def test_in_memory_store_is_explicitly_ephemeral(
    tmp_path: Path,
    example_config: ProjectConfig,
    example_catalog: ObservationCatalog,
) -> None:
    material = _materials(tmp_path / "site", example_config, example_catalog)
    verified = verify_gateway_bundle(
        material.envelope,
        material.publication,
        material.selection,
        material.policy,
        now=NOW,
    )
    store = InMemoryGatewaySequenceStore()
    store.accept(verified.checkpoint)
    assert (
        store.current(
            trust_namespace="gateway-tests",
            issuer=ISSUER,
            audience="wardwright-prod",
            channel="coding-defaults",
        )
        == verified.checkpoint
    )
    assert parse_dsse_envelope(material.envelope).payload_type.endswith("+json")
    assert GATEWAY_ENVELOPE_MEDIA_TYPE.endswith("+dsse")
