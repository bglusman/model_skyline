from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

import pytest

import model_skyline.io as io_module
from model_skyline.io import InputError, load_catalog, load_config, load_quality_reconciliation
from model_skyline.models import OfferingKey, OfferingObservation

EXACT_DECIMAL = "0.12345678901234567890123456789012345678"


def test_json_catalog_loader_preserves_decimal_literals_exactly(tmp_path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        f"""
        {{
          "schema_version": "model-skyline/v1alpha1",
          "workload": {{"id": "w", "version": "1", "unit": "task"}},
          "offerings": [{{
            "offering": {{
              "offering_id": "provider/model@tier",
              "model_id": "model",
              "provider": "provider"
            }},
            "signals": {{
              "quality": {{"value": {EXACT_DECIMAL}, "unit": "ratio"}}
            }},
            "metadata": {{"exact_ratio": {EXACT_DECIMAL}}}
          }}]
        }}
        """,
        encoding="utf-8",
    )

    catalog = load_catalog(path)
    offering = catalog.offerings[0]

    assert offering.signals["quality"].value == Decimal(EXACT_DECIMAL)
    assert offering.metadata["exact_ratio"] == EXACT_DECIMAL


def test_yaml_config_loader_preserves_decimal_literals_exactly(tmp_path) -> None:
    path = tmp_path / "frontier.yaml"
    path.write_text(
        f"""
        schema_version: model-skyline/v1alpha1
        workloads:
          w:
            unit: task
            version: "1"
            harness: harness@1
            cohort: test
            variables:
              exact_ratio: {EXACT_DECIMAL}
            assumptions:
              exact_ratio: {EXACT_DECIMAL}
        metrics:
          cost:
            kind: signal
            signal: cost
            unit: USD
          quality:
            kind: signal
            signal: quality
            unit: ratio
        frontiers:
          f:
            workload: w
            axes:
              - metric: cost
                goal: minimize
                epsilon_absolute: {EXACT_DECIMAL}
              - metric: quality
                goal: maximize
            order_by: cost
        """,
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.workloads["w"].variables["exact_ratio"] == Decimal(EXACT_DECIMAL)
    assert config.workloads["w"].assumptions["exact_ratio"] == EXACT_DECIMAL
    assert config.frontiers["f"].axes[0].epsilon_absolute == Decimal(EXACT_DECIMAL)


def test_programmatic_fractional_metadata_is_canonicalized() -> None:
    offering = OfferingObservation(
        offering=OfferingKey(
            offering_id="provider/model@tier",
            model_id="model",
            provider="provider",
        ),
        signals={},
        metadata={"ratio": 0.1, "nested": [Decimal("1.2300")]},
    )

    assert offering.metadata == {"ratio": "0.1", "nested": ["1.23"]}


def _valid_quality_reconciliation_json() -> str:
    return '{"schema_version":"model-skyline/quality-reconciliation/v1alpha1","entries":[]}'


def test_quality_loader_accepts_valid_regular_file(tmp_path) -> None:
    path = tmp_path / "reconciliation.json"
    path.write_text(_valid_quality_reconciliation_json(), encoding="utf-8")

    reconciliation = load_quality_reconciliation(path)

    assert reconciliation.entries == ()


def test_quality_loader_rejects_oversize_input_before_parsing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "oversize.json"
    path.write_bytes(b"{" + (b" " * 64))
    monkeypatch.setattr(io_module, "MAX_QUALITY_ARTIFACT_BYTES", 64)

    with pytest.raises(InputError, match="exceeds the 64-byte input limit"):
        load_quality_reconciliation(path)


def test_quality_loader_rejects_flat_container_before_json_allocation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "flat-array.json"
    path.write_bytes(b"[" + (b"0," * 10_000) + b"0]")
    monkeypatch.setattr(io_module, "MAX_QUALITY_JSON_STRUCTURAL_TOKENS", 1_000)

    def unexpected_json_load(*_args, **_kwargs):
        pytest.fail("json.loads must not run after the structural limit is exceeded")

    monkeypatch.setattr(io_module.json, "loads", unexpected_json_load)

    with pytest.raises(InputError, match="structure exceeds the 1000-token limit"):
        load_quality_reconciliation(path)


def test_quality_loader_rejects_deep_nesting_before_json_allocation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "deep-array.json"
    depth = io_module.MAX_QUALITY_JSON_NESTING_DEPTH + 1
    path.write_bytes((b"[" * depth) + b"0" + (b"]" * depth))

    def unexpected_json_load(*_args, **_kwargs):
        pytest.fail("json.loads must not run after the nesting limit is exceeded")

    monkeypatch.setattr(io_module.json, "loads", unexpected_json_load)

    with pytest.raises(InputError, match="nesting exceeds the 64-level limit"):
        load_quality_reconciliation(path)


def test_quality_loader_rejects_long_numeric_token_before_decimal_allocation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "long-number.json"
    path.write_bytes(
        b'{"schema_version":"model-skyline/quality-reconciliation/v1alpha1",'
        b'"entries":[],"number":'
        + (b"1" * (io_module.MAX_QUALITY_JSON_NUMBER_CHARACTERS + 1))
        + b"}"
    )

    def unexpected_json_load(*_args, **_kwargs):
        pytest.fail("json.loads must not run after the numeric-token limit is exceeded")

    monkeypatch.setattr(io_module.json, "loads", unexpected_json_load)

    with pytest.raises(InputError, match="numeric token exceeds the 1024-character limit"):
        load_quality_reconciliation(path)


def test_quality_json_preflight_ignores_punctuation_and_escapes_inside_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(io_module, "MAX_QUALITY_JSON_STRUCTURAL_TOKENS", 0)

    io_module._preflight_json_structure(
        rb'"quoted \" punctuation {[,:]} and escaped backslash \\ stays opaque"'
    )


def test_quality_loader_rejects_duplicate_keys_without_echoing_unbounded_key(tmp_path) -> None:
    key = "sensitive-" + ("x" * 10_000)
    path = tmp_path / "duplicate.json"
    path.write_text(f'{{"{key}":1,"{key}":2}}', encoding="utf-8")

    with pytest.raises(InputError, match="duplicate JSON key") as captured:
        load_quality_reconciliation(path)

    message = str(captured.value)
    assert "length=10010" in message
    assert "sha256=" in message
    assert len(message) < 300


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        (b"[" * 2_000) + b"0" + (b"]" * 2_000),
    ],
    ids=("invalid-utf8", "excessive-json-depth"),
)
def test_quality_loader_normalizes_decode_and_recursion_failures(tmp_path, payload: bytes) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)

    with pytest.raises(InputError, match="cannot parse quality artifact JSON"):
        load_quality_reconciliation(path)


@pytest.mark.skipif(not getattr(os, "O_NOFOLLOW", 0), reason="O_NOFOLLOW unavailable")
def test_quality_loader_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.json"
    target.write_text(_valid_quality_reconciliation_json(), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(InputError, match="cannot open quality artifact"):
        load_quality_reconciliation(link)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation unavailable")
def test_quality_loader_rejects_fifo_without_blocking(tmp_path) -> None:
    path = tmp_path / "quality.fifo"
    os.mkfifo(path)

    with pytest.raises(InputError, match="not a regular file"):
        load_quality_reconciliation(path)


def test_quality_loader_rejects_file_changed_during_read(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "moving.json"
    path.write_text(_valid_quality_reconciliation_json(), encoding="utf-8")
    real_fstat = io_module.os.fstat
    calls = 0

    def changed_fstat(descriptor: int) -> os.stat_result | SimpleNamespace:
        nonlocal calls
        calls += 1
        observed = real_fstat(descriptor)
        if calls == 1:
            return observed
        return SimpleNamespace(
            st_mode=observed.st_mode,
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns + 1,
            st_ctime_ns=observed.st_ctime_ns,
        )

    monkeypatch.setattr(io_module.os, "fstat", changed_fstat)

    with pytest.raises(InputError, match="changed while it was being read"):
        load_quality_reconciliation(path)
