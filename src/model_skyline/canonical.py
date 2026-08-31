from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Context
from typing import Any

import rfc8785

POLICY_DECIMAL_CONTEXT = Context(prec=34)
HASH_ALGORITHM = "sha256-rfc8785-v1"


def _materialize_json(value: Any) -> Any:
    """Convert immutable JSON mappings/sequences to encoder-native containers."""

    if isinstance(value, Mapping):
        return {key: _materialize_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_materialize_json(child) for child in value]
    return value


def canonical_bytes(value: Any) -> bytes:
    """Encode JSON-compatible data with RFC 8785 canonicalization.

    Public models first use their JSON-mode dump, which serializes every
    Decimal as a non-exponent fixed-point string. This avoids binary-float
    conversion in every language that verifies an artifact hash.
    """

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    value = _materialize_json(value)
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical JSON data: {exc}") from exc


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
