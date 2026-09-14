from __future__ import annotations

import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from model_skyline.gateway import GatewayTrustPolicy, pin_gateway_route, verify_gateway_bundle
from model_skyline.gateway_compatibility import (
    MAX_GATEWAY_COMPATIBILITY_BYTES,
    GatewayCompatibilityError,
    GatewayConsumerCompatibility,
    build_gateway_consumer_compatibility,
    load_gateway_consumer_compatibility,
    parse_gateway_consumer_compatibility,
    verify_gateway_consumer_compatibility,
)

ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE = ROOT / "conformance" / "gateway-pointer" / "v1alpha1"
MANIFEST = CONFORMANCE / "compatibility.json"


def _resource_path(manifest: GatewayConsumerCompatibility, resource_id: str) -> Path:
    resource = next(
        resource for resource in manifest.resources if resource.resource_id == resource_id
    )
    return ROOT / resource.path


def test_committed_compatibility_manifest_indexes_exact_generated_resources() -> None:
    manifest = load_gateway_consumer_compatibility(MANIFEST)
    schema = json.loads(
        (ROOT / "schemas" / "gateway-consumer-compatibility.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest.model_dump(mode="json"))
    verify_gateway_consumer_compatibility(manifest, content_root=ROOT)
    rebuilt = build_gateway_consumer_compatibility(
        schema_root=ROOT / "schemas",
        conformance_root=CONFORMANCE,
    )

    assert rebuilt == manifest
    assert manifest.features.supported_selection_kinds == ("selection",)
    assert manifest.features.supports_quality_gated_selection is False
    assert manifest.features.permits_remote_routing_material is False
    assert manifest.resource_set_sha256 == (
        "57deb53cfcab90ddc040af9b01b0b7c8845baa8801739416f83899aac6412fa5"
    )


def test_compatibility_manifest_drives_real_bundle_verification_end_to_end() -> None:
    manifest = load_gateway_consumer_compatibility(MANIFEST)
    expected = json.loads(_resource_path(manifest, "valid.expected").read_text(encoding="utf-8"))
    policy = GatewayTrustPolicy.model_validate_json(
        _resource_path(manifest, "valid.trust-policy").read_bytes()
    )
    verified = verify_gateway_bundle(
        _resource_path(manifest, "valid.envelope").read_bytes(),
        _resource_path(manifest, "artifact.publication").read_bytes(),
        _resource_path(manifest, "artifact.selection").read_bytes(),
        policy,
        now=datetime.fromisoformat(expected["verification_time"].replace("Z", "+00:00")),
    )
    route = pin_gateway_route(verified, now=verified.pointer.not_before)

    assert verified.checkpoint.model_dump(mode="json") == expected["checkpoint"]
    assert [target.target_id for target in route.targets] == expected["ordered_target_ids"]
    assert route.selection_snapshot_id == expected["selection_snapshot_id"]


def test_compatibility_verifier_rejects_tampered_indexed_bytes(tmp_path: Path) -> None:
    manifest = load_gateway_consumer_compatibility(MANIFEST)
    shutil.copytree(ROOT / "schemas", tmp_path / "schemas")
    shutil.copytree(ROOT / "conformance", tmp_path / "conformance")
    selection = _resource_path(manifest, "artifact.selection").relative_to(ROOT)
    target = tmp_path / selection
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(GatewayCompatibilityError, match="length mismatch"):
        verify_gateway_consumer_compatibility(manifest, content_root=tmp_path)


def test_compatibility_parser_rejects_duplicate_json_members() -> None:
    raw = MANIFEST.read_bytes()
    sentinel = "hostile-duplicate-member-do-not-log-7821"
    duplicate = ('{"' + sentinel + '":1,"' + sentinel + '":2,').encode("utf-8") + raw[1:]

    with pytest.raises(GatewayCompatibilityError, match="strict JSON") as caught:
        parse_gateway_consumer_compatibility(duplicate)

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert sentinel not in "".join(traceback.format_exception(caught.value))


def test_compatibility_parser_preflights_excessive_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (b"[" * 33) + (b"]" * 33)

    def unexpected_decode(*args: object, **kwargs: object) -> object:
        pytest.fail("json.loads must not run after the depth limit is exceeded")

    monkeypatch.setattr("model_skyline.gateway_compatibility.json.loads", unexpected_decode)
    with pytest.raises(GatewayCompatibilityError, match="32-level JSON depth limit"):
        parse_gateway_consumer_compatibility(payload)


def test_compatibility_parser_preflights_excessive_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"[" + (b"0," * 100_000) + b"0]"

    def unexpected_decode(*args: object, **kwargs: object) -> object:
        pytest.fail("json.loads must not run after the structure limit is exceeded")

    monkeypatch.setattr("model_skyline.gateway_compatibility.json.loads", unexpected_decode)
    with pytest.raises(GatewayCompatibilityError, match="100000-token JSON structure limit"):
        parse_gateway_consumer_compatibility(payload)


@pytest.mark.parametrize("failure", [RecursionError(), MemoryError()])
def test_compatibility_parser_sanitizes_decoder_resource_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    def failed_decode(*args: object, **kwargs: object) -> object:
        raise failure

    monkeypatch.setattr("model_skyline.gateway_compatibility.json.loads", failed_decode)
    with pytest.raises(GatewayCompatibilityError, match="not strict JSON"):
        parse_gateway_consumer_compatibility(b"{}")


def test_compatibility_parser_rejects_nonfinite_and_oversized_json() -> None:
    with pytest.raises(GatewayCompatibilityError, match="not strict JSON"):
        parse_gateway_consumer_compatibility(b'{"value":NaN}')
    with pytest.raises(GatewayCompatibilityError, match="byte limit"):
        parse_gateway_consumer_compatibility(b" " * (MAX_GATEWAY_COMPATIBILITY_BYTES + 1))


def test_compatibility_file_errors_do_not_disclose_caller_paths(tmp_path: Path) -> None:
    secret_path = tmp_path / "hostile-caller-path-do-not-log-7821.json"

    with pytest.raises(GatewayCompatibilityError) as caught:
        load_gateway_consumer_compatibility(secret_path)

    assert "do-not-log-7821" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_compatibility_profile_is_fail_closed_and_contains_no_secret_material() -> None:
    manifest = load_gateway_consumer_compatibility(MANIFEST)
    payload = manifest.model_dump(mode="json")
    resource_paths = [resource.path for resource in manifest.resources]

    assert not any("test-seed" in path for path in resource_paths)
    assert not any("private" in path for path in resource_paths)
    assert not any("credential" in path for path in resource_paths)
    assert all("http://" not in path and "https://" not in path for path in resource_paths)

    payload["features"]["permits_remote_routing_material"] = True
    with pytest.raises(ValidationError):
        GatewayConsumerCompatibility.model_validate(payload)

    payload = manifest.model_dump(mode="json")
    payload["resources"] = list(reversed(payload["resources"]))
    with pytest.raises(ValidationError, match="sorted by resource_id"):
        GatewayConsumerCompatibility.model_validate(payload)
