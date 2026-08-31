from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from model_skyline.gateway import (
    GatewayProtocolError,
    GatewayTrustPolicy,
    InMemoryGatewayInstallationStore,
    parse_dsse_envelope,
    parse_gateway_sequence_checkpoint,
    parse_gateway_trust_policy,
)
from model_skyline.gateway_resolver import SignedGatewayResolver

ROOT = Path(__file__).parents[1]
CONFORMANCE = ROOT / "conformance" / "gateway-pointer" / "v1alpha1"
ISSUER = "https://control.example/model-skyline"


def _policy() -> GatewayTrustPolicy:
    return GatewayTrustPolicy.model_validate_json(
        (CONFORMANCE / "valid" / "trust-policy.json").read_bytes()
    )


@pytest.mark.parametrize("include_alias", [False, True])
def test_dsse_parser_rejects_python_field_name_on_the_wire(include_alias: bool) -> None:
    envelope = json.loads((CONFORMANCE / "valid" / "envelope.dsse.json").read_bytes())
    payload_type = envelope.pop("payloadType")
    envelope["payload_type"] = payload_type
    if include_alias:
        envelope["payloadType"] = payload_type

    with pytest.raises(GatewayProtocolError, match="does not match"):
        parse_dsse_envelope(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/admin.dsse.json",
        "%2F..%2Fadmin.dsse.json",
        "%5cadmin.dsse.json",
    ],
)
def test_gateway_source_rejects_percent_encoded_path_confusion(path: str) -> None:
    with pytest.raises(ValueError, match="non-canonical path"):
        SignedGatewayResolver(
            f"{ISSUER}/{path}",
            policy=_policy(),
            store=InMemoryGatewayInstallationStore(),
        )


@pytest.mark.parametrize(
    "issuer",
    [
        "https://control.example/model@skyline",
        "https://control.example/model%2Fskyline",
        "https://control.example/model\\skyline",
        "https://control.example/model/../skyline",
        "https://control.example/model//skyline",
    ],
)
def test_gateway_issuer_runtime_rejects_noncanonical_path(issuer: str) -> None:
    policy = _policy().model_dump(mode="json")
    policy["issuer"] = issuer

    with pytest.raises(
        ValidationError, match="cannot contain @|non-canonical path|normalized form"
    ):
        GatewayTrustPolicy.model_validate(policy)


def test_security_policy_and_checkpoint_parsers_reject_duplicate_members() -> None:
    policy = (
        (CONFORMANCE / "valid" / "trust-policy.json")
        .read_bytes()
        .replace(
            b'"signature_threshold": 1,',
            b'"signature_threshold": 2,\n  "signature_threshold": 1,',
            1,
        )
    )
    with pytest.raises(GatewayProtocolError, match="duplicate JSON member"):
        parse_gateway_trust_policy(policy)

    expected = json.loads((CONFORMANCE / "valid" / "expected.json").read_bytes())["checkpoint"]
    wrapper = b'{"checkpoint":{},"checkpoint":' + json.dumps(expected).encode() + b"}"
    with pytest.raises(GatewayProtocolError, match="duplicate JSON member"):
        parse_gateway_sequence_checkpoint(wrapper)
