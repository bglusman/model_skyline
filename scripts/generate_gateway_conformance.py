#!/usr/bin/env python3
"""Regenerate deterministic v1alpha1 gateway protocol conformance vectors."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from model_skyline.canonical import canonical_bytes
from model_skyline.gateway import (
    GATEWAY_POINTER_PAYLOAD_TYPE,
    DsseEnvelope,
    DsseSignature,
    GatewayFileReference,
    GatewayTargetBinding,
    GatewayTrustPolicy,
    build_gateway_pointer,
    dsse_pae,
    envelope_bytes,
    gateway_key_id,
    parse_selection_artifact,
    pin_gateway_route,
    pointer_bytes,
    sign_gateway_pointer,
    trusted_gateway_key,
    verify_gateway_bundle,
)
from model_skyline.io import load_catalog, load_config
from model_skyline.publisher import publish_project

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "conformance" / "gateway-pointer" / "v1alpha1"
OUTPUT = Path(os.environ.get("MODEL_SKYLINE_GATEWAY_CONFORMANCE_OUTPUT", str(DEFAULT_OUTPUT)))
NOW = datetime(2026, 8, 29, 19, tzinfo=UTC)
ISSUER = "https://control.example/model-skyline"


def _key(start: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes(range(start, start + 32)))


def _write(path: Path, payload: bytes, *, newline: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload + (b"\n" if newline else b""))


def _json_bytes(value: Any, *, pretty: bool = True) -> bytes:
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return canonical_bytes(value)


def _arbitrary_envelope(
    payload: bytes,
    payload_type: str,
    signatures: list[tuple[str, bytes]],
) -> bytes:
    value = {
        "payload": base64.b64encode(payload).decode("ascii"),
        "payloadType": payload_type,
        "signatures": [
            {"keyid": keyid, "sig": base64.b64encode(signature).decode("ascii")}
            for keyid, signature in signatures
        ],
    }
    return canonical_bytes(value)


def main() -> None:
    key_1, key_2 = _key(1), _key(33)
    fixture = ROOT / "examples" / "coding-session"
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        site = Path(temporary) / "site"
        result = publish_project(
            load_config(fixture / "frontier.yaml"),
            [load_catalog(fixture / "observations.json")],
            site,
            project_id="gateway-demo",
            selection_ids=["coding-agent-defaults"],
            generated_at=NOW,
            base_url=ISSUER,
            public=True,
            allowed_licenses=["CC0-1.0"],
        )
        publication = (
            site / "publications" / f"{result.manifest.publication_id}.json"
        ).read_bytes()
        published = result.manifest.selections[0]
        selection = (site / published.snapshot.path).read_bytes()

    pointer = build_gateway_pointer(
        publication,
        selection,
        issuer=ISSUER,
        audience=["wardwright-prod", "wardwright-canary"],
        channel="coding-defaults",
        sequence=7,
        selection_id="coding-agent-defaults",
        issued_at=NOW,
        hard_expires_at=NOW + timedelta(minutes=30),
        required_capabilities=["tools", "structured_output"],
    )
    payload = pointer_bytes(pointer)
    pae = dsse_pae(GATEWAY_POINTER_PAYLOAD_TYPE, payload)
    envelope_1 = sign_gateway_pointer(pointer, (key_1,))
    envelope_2 = sign_gateway_pointer(pointer, (key_1, key_2))
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
    trusted_keys = (
        trusted_gateway_key(key_1.public_key()),
        trusted_gateway_key(key_2.public_key()),
    )
    policy = GatewayTrustPolicy(
        trust_namespace="gateway-conformance",
        issuer=ISSUER,
        audience="wardwright-prod",
        channel="coding-defaults",
        project_id="gateway-demo",
        trusted_keys=trusted_keys,
        signature_threshold=1,
        expected_selection_id="coding-agent-defaults",
        expected_frontier_id="coding-value",
        expected_workload=pointer.selection.workload,
        required_capabilities=pointer.selection.required_capabilities,
        target_bindings=bindings,
    )
    threshold_policy = policy.model_copy(update={"signature_threshold": 2})
    verified = verify_gateway_bundle(
        envelope_bytes(envelope_1),
        publication,
        selection,
        policy,
        now=NOW,
    )
    route = pin_gateway_route(verified, now=NOW)

    _write(OUTPUT / "artifacts" / "publication.json", publication)
    _write(OUTPUT / "artifacts" / "selection.json", selection)
    _write(
        OUTPUT / "keys" / "key-1.public.jwk.json",
        _json_bytes(trusted_keys[0].public_jwk.model_dump(mode="json")),
    )
    _write(
        OUTPUT / "keys" / "key-2.public.jwk.json",
        _json_bytes(trusted_keys[1].public_jwk.model_dump(mode="json")),
    )
    _write(
        OUTPUT / "keys" / "key-1.test-seed.hex", bytes(range(1, 33)).hex().encode(), newline=True
    )
    _write(
        OUTPUT / "keys" / "key-2.test-seed.hex",
        bytes(range(33, 65)).hex().encode(),
        newline=True,
    )
    _write(OUTPUT / "valid" / "payload.json", payload)
    jwk_thumbprint_input = canonical_bytes(trusted_keys[0].public_jwk.model_dump(mode="json"))
    selection_hash_input = snapshot.model_dump(mode="json", exclude={"snapshot_id"})
    for choice in (selection_hash_input["default"], *selection_hash_input["fallbacks"]):
        offering = choice["offering"]
        if offering.get("billing_mode") is None:
            offering.pop("billing_mode", None)
    publication_hash_input = result.manifest.model_dump(mode="json", exclude={"publication_id"})
    _write(OUTPUT / "intermediate" / "jwk-thumbprint-input.json", jwk_thumbprint_input)
    _write(OUTPUT / "intermediate" / "pointer.json", payload)
    _write(OUTPUT / "intermediate" / "dsse-pae.bin", pae)
    _write(
        OUTPUT / "intermediate" / "selection-hash-input.json",
        canonical_bytes(selection_hash_input),
    )
    _write(
        OUTPUT / "intermediate" / "publication-hash-input.json",
        canonical_bytes(publication_hash_input),
    )
    _write(OUTPUT / "valid" / "envelope.dsse.json", envelope_bytes(envelope_1))
    _write(OUTPUT / "valid" / "threshold-2-of-2.dsse.json", envelope_bytes(envelope_2))
    _write(OUTPUT / "valid" / "rotation-same-payload.dsse.json", envelope_bytes(envelope_2))
    _write(
        OUTPUT / "valid" / "trust-policy.json",
        _json_bytes(policy.model_dump(mode="json")),
    )
    _write(
        OUTPUT / "valid" / "trust-policy-threshold-2.json",
        _json_bytes(threshold_policy.model_dump(mode="json")),
    )
    _write(
        OUTPUT / "valid" / "pinned-route.json",
        _json_bytes(route.model_dump(mode="json")),
    )

    signature_1 = key_1.sign(pae)
    expected = {
        "fixture_version": "model-skyline/gateway-conformance/v1alpha1",
        "verification_time": NOW.isoformat().replace("+00:00", "Z"),
        "payload_type": GATEWAY_POINTER_PAYLOAD_TYPE,
        "payload_length": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_base64": base64.b64encode(payload).decode("ascii"),
        "pae_length": len(pae),
        "pae_sha256": hashlib.sha256(pae).hexdigest(),
        "jwk_thumbprint_input_sha256": hashlib.sha256(jwk_thumbprint_input).hexdigest(),
        "selection_hash_input_sha256": hashlib.sha256(
            canonical_bytes(selection_hash_input)
        ).hexdigest(),
        "publication_hash_input_sha256": hashlib.sha256(
            canonical_bytes(publication_hash_input)
        ).hexdigest(),
        "key_1_keyid": gateway_key_id(key_1.public_key()),
        "key_1_signature_hex": signature_1.hex(),
        "key_1_signature_base64": base64.b64encode(signature_1).decode("ascii"),
        "publication_length": len(publication),
        "publication_raw_sha256": hashlib.sha256(publication).hexdigest(),
        "publication_id": result.manifest.publication_id,
        "selection_length": len(selection),
        "selection_raw_sha256": hashlib.sha256(selection).hexdigest(),
        "selection_snapshot_id": snapshot.snapshot_id,
        "checkpoint": verified.checkpoint.model_dump(mode="json"),
        "ordered_target_ids": [target.target_id for target in route.targets],
    }
    _write(OUTPUT / "valid" / "expected.json", _json_bytes(expected))

    bit_flip = bytearray(payload)
    bit_flip[-2] ^= 1
    _write(
        OUTPUT / "invalid" / "payload-bit-flip.dsse.json",
        envelope_bytes(
            envelope_1.model_copy(update={"payload": base64.b64encode(bit_flip).decode()})
        ),
    )
    wrong_type = "application/vnd.model-skyline.frontier.v1+json"
    _write(
        OUTPUT / "invalid" / "wrong-payload-type.dsse.json",
        _arbitrary_envelope(
            payload,
            wrong_type,
            [(gateway_key_id(key_1.public_key()), key_1.sign(dsse_pae(wrong_type, payload)))],
        ),
    )
    duplicate_payload = payload.replace(
        b'{"audience":',
        b'{"sequence":7,"audience":',
        1,
    )
    _write(
        OUTPUT / "invalid" / "duplicate-json-member.dsse.json",
        _arbitrary_envelope(
            duplicate_payload,
            GATEWAY_POINTER_PAYLOAD_TYPE,
            [
                (
                    gateway_key_id(key_1.public_key()),
                    key_1.sign(dsse_pae(GATEWAY_POINTER_PAYLOAD_TYPE, duplicate_payload)),
                )
            ],
        ),
    )
    _write(
        OUTPUT / "invalid" / "duplicate-key-threshold.dsse.json",
        envelope_bytes(
            DsseEnvelope(
                payloadType=GATEWAY_POINTER_PAYLOAD_TYPE,
                payload=envelope_1.payload,
                signatures=(
                    envelope_1.signatures[0],
                    DsseSignature(
                        keyid=gateway_key_id(key_2.public_key()),
                        sig=envelope_1.signatures[0].sig,
                    ),
                ),
            )
        ),
    )
    unsorted_audience = pointer.model_copy(update={"audience": tuple(reversed(pointer.audience))})
    _write(
        OUTPUT / "invalid" / "unsorted-audience.dsse.json",
        envelope_bytes(sign_gateway_pointer(unsorted_audience, (key_1,))),
    )
    unsorted_capabilities = pointer.model_copy(
        update={
            "selection": pointer.selection.model_copy(
                update={
                    "required_capabilities": tuple(
                        reversed(pointer.selection.required_capabilities)
                    )
                }
            )
        }
    )
    _write(
        OUTPUT / "invalid" / "unsorted-required-capabilities.dsse.json",
        envelope_bytes(sign_gateway_pointer(unsorted_capabilities, (key_1,))),
    )
    raw_digest_swap = pointer.model_copy(
        update={
            "selection": pointer.selection.model_copy(
                update={
                    "file": GatewayFileReference(
                        path=pointer.selection.file.path,
                        length=pointer.selection.file.length,
                        sha256=pointer.selection.snapshot_id,
                        media_type="application/json",
                    )
                }
            )
        }
    )
    _write(
        OUTPUT / "invalid" / "raw-digest-vs-snapshot-id-swap.dsse.json",
        envelope_bytes(sign_gateway_pointer(raw_digest_swap, (key_1,))),
    )
    rollback = build_gateway_pointer(
        publication,
        selection,
        issuer=ISSUER,
        audience=["wardwright-prod", "wardwright-canary"],
        channel="coding-defaults",
        sequence=6,
        selection_id="coding-agent-defaults",
        issued_at=NOW,
        hard_expires_at=NOW + timedelta(minutes=30),
        required_capabilities=["tools", "structured_output"],
    )
    _write(
        OUTPUT / "invalid" / "rollback.dsse.json",
        envelope_bytes(sign_gateway_pointer(rollback, (key_1,))),
    )
    equivocation = pointer.model_copy(update={"hard_expires_at": NOW + timedelta(minutes=20)})
    _write(
        OUTPUT / "invalid" / "same-sequence-different-payload.dsse.json",
        envelope_bytes(sign_gateway_pointer(equivocation, (key_1,))),
    )
    expiring = pointer.model_copy(update={"hard_expires_at": NOW + timedelta(minutes=1)})
    _write(
        OUTPUT / "invalid" / "expired.dsse.json",
        envelope_bytes(sign_gateway_pointer(expiring, (key_1,))),
    )


if __name__ == "__main__":
    main()
