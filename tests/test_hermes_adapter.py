from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from model_skyline.adapters.hermes import (
    HERMES_ADAPTER_VERSION,
    HERMES_AGENT_COMMIT,
    HERMES_AGENT_COMMITS,
    HERMES_AGENT_LICENSE,
    HERMES_AGENT_VERSION,
    HERMES_ROUTE_IDENTITY_SOURCE_URL,
    HERMES_SESSION_SCHEMA_SOURCE_URL,
    HERMES_SESSION_SCHEMA_VERSION,
    HERMES_SESSION_STORAGE_SOURCE_URL,
    HERMES_USAGE_NORMALIZATION_SOURCE_URL,
    HERMES_USAGE_REPORT_SOURCE_URL,
    MAX_HERMES_SESSION_MAPPING_BYTES,
    MAX_HERMES_STATE_DATABASE_BYTES,
    HermesAdapterError,
    HermesRouteMapping,
    HermesSessionMapping,
    _canonical_hermes_base_url,
    import_hermes_session,
    import_hermes_usage_report,
    load_hermes_session_mapping,
)
from model_skyline.canonical import POLICY_DECIMAL_CONTEXT
from model_skyline.models import OfferingKey, WorkloadReference
from model_skyline.trace_producers import trusted_trace_producer
from model_skyline.traces import aggregate_traces

FIXTURES = Path(__file__).parent / "fixtures"
IDENTITY_KEY = b"synthetic-test-key-material"
SESSION_ID = "synthetic-hermes-session-01"
WORKLOAD = WorkloadReference(
    id="agentic-coding-session",
    version="1.0.0",
    unit="coding_session",
)
ROUTE = HermesRouteMapping(
    offering=OfferingKey(
        offering_id="synthetic-provider/synthetic-model@synthetic-tier",
        model_id="synthetic-model",
        provider="synthetic-provider",
        endpoint="https://synthetic-provider.invalid/v1",
        billing_mode="synthetic-direct",
        service_tier="synthetic-tier",
        agent_harness="hermes-agent",
    ),
    model="synthetic-model",
    billing_provider="synthetic-provider",
    billing_base_url="https://synthetic-provider.invalid/v1",
    billing_mode="synthetic-direct",
    usage_report_single_route_attested=True,
    service_tier_fulfilled_attested=True,
    route_details_attested=False,
)
MAPPING = HermesSessionMapping(
    session_id=SESSION_ID,
    hermes_version=HERMES_AGENT_VERSION,
    workload=WORKLOAD,
    route=ROUTE,
    work_unit_success=Decimal(0),
)


def _mapping_with_billing_mode(billing_mode: str | None) -> HermesSessionMapping:
    route_payload = ROUTE.model_dump(mode="python")
    route_payload["offering"]["billing_mode"] = billing_mode
    route_payload["billing_mode"] = billing_mode
    route = HermesRouteMapping.model_validate(route_payload)
    return MAPPING.model_copy(update={"route": route})


def _state_db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    script = (FIXTURES / "hermes_state_v26_synthetic.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


def _execute(path: Path, statement: str, parameters: tuple[object, ...] = ()) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


def _remove_usage_table_constraints(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            ALTER TABLE session_model_usage RENAME TO constrained_session_model_usage;
            CREATE TABLE session_model_usage AS
                SELECT * FROM constrained_session_model_usage;
            DROP TABLE constrained_session_model_usage;
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_reviewed_hermes_contract_is_pinned_to_primary_sources() -> None:
    assert HERMES_AGENT_COMMIT == "4f22543509d1b91dc45bcb369447126c5eb14fb7"
    assert HERMES_AGENT_VERSION == "0.20.6"
    assert HERMES_SESSION_SCHEMA_VERSION == 26
    assert HERMES_AGENT_LICENSE == "MIT"
    assert HERMES_ADAPTER_VERSION == "2"
    assert dict(HERMES_AGENT_COMMITS) == {
        "0.20.6": "4f22543509d1b91dc45bcb369447126c5eb14fb7",
        "0.21.0": "29112bef099274229cadff79cdff7bf7b99c4b77",
    }
    for url in (
        HERMES_USAGE_REPORT_SOURCE_URL,
        HERMES_USAGE_NORMALIZATION_SOURCE_URL,
        HERMES_SESSION_SCHEMA_SOURCE_URL,
        HERMES_SESSION_STORAGE_SOURCE_URL,
        HERMES_ROUTE_IDENTITY_SOURCE_URL,
    ):
        assert url.startswith(
            f"https://github.com/NousResearch/hermes-agent/blob/{HERMES_AGENT_COMMIT}/"
        )

    producer_key_tail = (
        "nousresearch/hermes-agent",
        HERMES_AGENT_VERSION,
        HERMES_AGENT_COMMIT,
        None,
        None,
    )
    legacy_producer = trusted_trace_producer(
        ("model-skyline/hermes-agent-aggregate", "1", *producer_key_tail)
    )
    current_producer = trusted_trace_producer(
        ("model-skyline/hermes-agent-aggregate", HERMES_ADAPTER_VERSION, *producer_key_tail)
    )
    assert legacy_producer is not None
    assert current_producer is not None
    assert legacy_producer.source.id.endswith(":adapter-1")
    assert current_producer.source.id.endswith(":adapter-2")

    version_021_commit = HERMES_AGENT_COMMITS["0.21.0"]
    version_021_producer = trusted_trace_producer(
        (
            "model-skyline/hermes-agent-aggregate",
            HERMES_ADAPTER_VERSION,
            "nousresearch/hermes-agent",
            "0.21.0",
            version_021_commit,
            None,
            None,
        )
    )
    assert version_021_producer is not None
    assert version_021_producer.source.version == version_021_commit
    assert version_021_commit in str(version_021_producer.source.url)
    assert version_021_commit in str(version_021_producer.source.terms_url)
    assert "SQLite" in (version_021_producer.source.methodology or "")
    assert "usage-report import is intentionally unsupported" in (
        version_021_producer.source.methodology or ""
    )
    assert (
        trusted_trace_producer(
            (
                "model-skyline/hermes-agent-aggregate",
                "1",
                "nousresearch/hermes-agent",
                "0.21.0",
                version_021_commit,
                None,
                None,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("raw_url", "canonical_url"),
    [
        ("HTTPS://SYNTHETIC-PROVIDER.INVALID:443/v1/", "https://synthetic-provider.invalid/v1"),
        ("https://synthetic-provider.invalid/v1//", "https://synthetic-provider.invalid/v1/"),
        (
            "https://synthetic-provider.invalid:8443/v1/",
            "https://synthetic-provider.invalid:8443/v1",
        ),
    ],
)
def test_hermes_route_url_canonicalization_is_narrow(
    raw_url: str,
    canonical_url: str,
) -> None:
    assert _canonical_hermes_base_url(raw_url) == canonical_url


def test_usage_report_import_preserves_aggregate_semantics_without_payloads() -> None:
    observed_at = datetime(2026, 8, 30, 18, 0, tzinfo=UTC)

    trace = import_hermes_usage_report(
        FIXTURES / "hermes_usage_synthetic.json",
        mapping=MAPPING,
        observed_at=observed_at,
        identity_key=IDENTITY_KEY,
    )

    assert trace.timestamp == observed_at
    assert trace.observation_unit == "work_unit"
    assert trace.model_request_count == 3
    assert trace.work_unit_success == Decimal(0)
    assert trace.input_uncached_tokens == Decimal(1200)
    assert trace.input_cache_read_tokens == Decimal(800)
    assert trace.input_cache_write_tokens == Decimal(200)
    assert trace.output_total_tokens == Decimal(350)
    assert trace.reasoning_tokens == Decimal(50)
    assert trace.output_tokens == Decimal(300)
    assert trace.tool_calls is None
    assert trace.estimated_total_cost_usd == Decimal("0.0125")

    public_json = trace.model_dump_json()
    assert SESSION_ID not in public_json
    assert "synthetic-provider" in public_json  # only the operator-reviewed offering id
    for sentinel in (
        "UNMAPPED_UPSTREAM_MODEL_SENTINEL",
        "UNMAPPED_UPSTREAM_PROVIDER_SENTINEL",
        "PRIVATE_FAILURE_PAYLOAD_SENTINEL",
        "PRIVATE_PROMPT_PAYLOAD_SENTINEL",
        "PRIVATE_TOOL_PAYLOAD_SENTINEL",
        "PRIVATE_WORKSPACE_PATH_SENTINEL",
        "PRIVATE_ENVIRONMENT_SENTINEL",
    ):
        assert sentinel not in public_json


def test_usage_report_import_does_not_implicitly_expand_to_hermes_021() -> None:
    mapping = MAPPING.model_copy(update={"hermes_version": "0.21.0"})

    with pytest.raises(HermesAdapterError, match="supports exactly Agent 0.20.6"):
        import_hermes_usage_report(
            FIXTURES / "hermes_usage_synthetic.json",
            mapping=mapping,
            observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )


@pytest.mark.parametrize(
    ("cost_status", "cost_source", "amount", "expected_basis"),
    [
        ("unknown", "none", 0, "unknown"),
        ("included", "none", 0, "included"),
    ],
)
def test_usage_report_distinguishes_unknown_from_included_zero_marginal_cost(
    tmp_path: Path,
    cost_status: str,
    cost_source: str,
    amount: int,
    expected_basis: str,
) -> None:
    payload = json.loads((FIXTURES / "hermes_usage_synthetic.json").read_text(encoding="utf-8"))
    payload.update(
        {
            "cost_status": cost_status,
            "cost_source": cost_source,
            "estimated_cost_usd": amount,
        }
    )
    path = tmp_path / f"{cost_status}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    trace = import_hermes_usage_report(
        path,
        mapping=MAPPING,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        identity_key=IDENTITY_KEY,
    )

    assert trace.estimated_total_cost_usd is None
    assert trace.provider_reported_total_cost_usd is None
    assert trace.adapter_version == HERMES_ADAPTER_VERSION
    if expected_basis == "included":
        assert trace.provider_marginal_cost_usd == Decimal(0)
    else:
        assert trace.provider_marginal_cost_usd is None


def test_usage_report_rejects_contradictory_cost_state_and_route_attestations(
    tmp_path: Path,
) -> None:
    payload = json.loads((FIXTURES / "hermes_usage_synthetic.json").read_text(encoding="utf-8"))
    payload.update({"cost_status": "actual", "cost_source": "official_docs_snapshot"})
    path = tmp_path / "contradictory-cost.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HermesAdapterError, match="provider-reported source"):
        import_hermes_usage_report(
            path,
            mapping=MAPPING,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )

    unattested_route = ROUTE.model_copy(update={"usage_report_single_route_attested": False})
    with pytest.raises(HermesAdapterError, match="fallback and auxiliary"):
        import_hermes_usage_report(
            FIXTURES / "hermes_usage_synthetic.json",
            mapping=MAPPING.model_copy(update={"route": unattested_route}),
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )

    unattested_tier = ROUTE.model_copy(update={"service_tier_fulfilled_attested": False})
    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping"):
        import_hermes_usage_report(
            FIXTURES / "hermes_usage_synthetic.json",
            mapping=MAPPING.model_copy(update={"route": unattested_tier}),
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )


def test_sqlite_session_import_is_read_only_and_never_reads_private_columns(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    digest_before = hashlib.sha256(path.read_bytes()).digest()

    trace = import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    assert hashlib.sha256(path.read_bytes()).digest() == digest_before
    assert not path.with_name(f"{path.name}-journal").exists()
    assert trace.timestamp == datetime.fromtimestamp(1788105600, tz=UTC)
    assert trace.observation_unit == "work_unit"
    assert trace.model_request_count == 3
    assert trace.input_uncached_tokens == Decimal(1200)
    assert trace.input_cache_read_tokens == Decimal(800)
    assert trace.input_cache_write_tokens == Decimal(200)
    assert trace.output_total_tokens == Decimal(350)
    assert trace.reasoning_tokens == Decimal(50)
    assert trace.output_tokens == Decimal(300)
    assert trace.tool_calls == Decimal(7)
    assert trace.provider_reported_total_cost_usd == Decimal("0.0105")
    assert trace.estimated_total_cost_usd is None

    public_json = trace.model_dump_json()
    assert SESSION_ID not in public_json
    for sentinel in (
        "PRIVATE_SYSTEM_PROMPT_SENTINEL",
        "PRIVATE_MESSAGE_PAYLOAD_SENTINEL",
        "PRIVATE_TOOL_PAYLOAD_SENTINEL",
        "PRIVATE_WORKSPACE_PATH_SENTINEL",
    ):
        assert sentinel not in public_json


def test_sqlite_session_import_uses_exact_reviewed_release_from_mapping(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    mapping = MAPPING.model_copy(update={"hermes_version": "0.21.0"})

    trace = import_hermes_session(path, mapping=mapping, identity_key=IDENTITY_KEY)

    assert trace.upstream_version == "0.21.0"
    assert trace.upstream_commit == HERMES_AGENT_COMMITS["0.21.0"]
    assert (
        trusted_trace_producer(
            (
                trace.adapter_id,
                trace.adapter_version,
                trace.upstream_system,
                trace.upstream_version,
                trace.upstream_commit,
                trace.collector_id,
                trace.collector_version,
            )
        )
        is not None
    )


def test_constructed_mapping_cannot_bypass_exact_release_registry(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    mapping = MAPPING.model_copy(update={"hermes_version": "private-unsupported-version"})

    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping"):
        import_hermes_session(path, mapping=mapping, identity_key=IDENTITY_KEY)

    non_string = MAPPING.model_copy(update={"hermes_version": ["0.21.0"]})
    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping"):
        import_hermes_session(path, mapping=non_string, identity_key=IDENTITY_KEY)


def test_public_importers_revalidate_copied_mapping_without_echoing_values(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    secret = "sk-" + "proj-private-copied-offering"
    offering = ROUTE.offering.model_copy(update={"offering_id": secret})
    route = ROUTE.model_copy(update={"offering": offering})
    mapping = MAPPING.model_copy(update={"route": route})

    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping") as error:
        import_hermes_session(path, mapping=mapping, identity_key=IDENTITY_KEY)
    assert secret not in str(error.value)

    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping") as error:
        import_hermes_usage_report(
            FIXTURES / "hermes_usage_synthetic.json",
            mapping=mapping,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )
    assert secret not in str(error.value)


def test_sqlite_snapshot_includes_committed_wal_without_modifying_database_pages(
    tmp_path: Path,
) -> None:
    path = _state_db(tmp_path)
    writer = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            """
            INSERT INTO session_model_usage (
                session_id, model, billing_provider, billing_base_url, billing_mode,
                task, api_call_count, input_tokens, output_tokens, cache_read_tokens,
                cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd,
                cost_status, cost_source
            ) VALUES (?, 'synthetic-model', 'synthetic-provider',
                      'https://synthetic-provider.invalid/v1', 'synthetic-direct',
                      'vision', 1, 100, 10, 20, 5, 2, 0.002, 0,
                      NULL, NULL)
            """,
            (SESSION_ID,),
        )
        writer.commit()
        wal_path = path.with_name(f"{path.name}-wal")
        assert wal_path.stat().st_size > 0
        source_digests = {
            source.name: hashlib.sha256(source.read_bytes()).digest() for source in (path, wal_path)
        }

        trace = import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)

        assert trace.model_request_count == 4
        assert trace.input_uncached_tokens == 1300
        assert trace.input_cache_read_tokens == 820
        assert trace.input_cache_write_tokens == 205
        assert trace.output_total_tokens == 360
        assert trace.reasoning_tokens == 52
        assert trace.estimated_total_cost_usd == Decimal("0.0125")
        assert source_digests == {
            source.name: hashlib.sha256(source.read_bytes()).digest() for source in (path, wal_path)
        }
    finally:
        writer.close()


def test_sqlite_snapshot_rejects_symlinks_special_files_and_oversized_input(
    tmp_path: Path,
) -> None:
    real_path = _state_db(tmp_path)
    symlink_path = tmp_path / "linked.db"
    symlink_path.symlink_to(real_path)
    with pytest.raises(HermesAdapterError, match="cannot open"):
        import_hermes_session(symlink_path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    fifo_path = tmp_path / "state-fifo.db"
    os.mkfifo(fifo_path)
    with pytest.raises(HermesAdapterError, match="regular file"):
        import_hermes_session(fifo_path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    oversized_path = tmp_path / "oversized.db"
    with oversized_path.open("wb") as stream:
        stream.truncate(MAX_HERMES_STATE_DATABASE_BYTES + 1)
    with pytest.raises(HermesAdapterError, match="byte limit"):
        import_hermes_session(oversized_path, mapping=MAPPING, identity_key=IDENTITY_KEY)


def test_sqlite_snapshot_rejects_unsafe_companion_and_malformed_database(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    unsafe_wal = path.with_name(f"{path.name}-wal")
    unsafe_wal.symlink_to(path)
    with pytest.raises(HermesAdapterError, match="companion file is unsafe"):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    malformed = tmp_path / "malformed.db"
    malformed.write_bytes(b"not a SQLite database")
    with pytest.raises(HermesAdapterError, match="cannot snapshot"):
        import_hermes_session(malformed, mapping=MAPPING, identity_key=IDENTITY_KEY)


def test_session_aggregate_preserves_request_count_without_inventing_attempts(
    tmp_path: Path,
) -> None:
    state_path = _state_db(tmp_path)
    trace = import_hermes_session(state_path, mapping=MAPPING, identity_key=IDENTITY_KEY)
    trace_path = tmp_path / "hermes-trace.jsonl"
    trace_path.write_text(
        json.dumps(trace.model_dump(mode="json"), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    summary = aggregate_traces(trace_path, workload=WORKLOAD)
    signals = summary.offerings[MAPPING.route.offering.offering_id]

    assert signals["request_count_per_work_unit"].value == Decimal(3)
    assert "attempt_count_per_work_unit" not in signals
    with localcontext(POLICY_DECIMAL_CONTEXT):
        expected_cache_hit_rate = Decimal(800) / Decimal(2200)
    assert signals["observed_cache_hit_rate"].value == expected_cache_hit_rate


def test_identity_key_controls_stable_non_reversible_trace_ids(tmp_path: Path) -> None:
    path = _state_db(tmp_path)

    first = import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)
    again = import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)
    rotated = import_hermes_session(
        path,
        mapping=MAPPING,
        identity_key=b"different-synthetic-key",
    )

    assert first.work_unit_id == again.work_unit_id
    assert first.request_id == again.request_id
    assert first.work_unit_id != rotated.work_unit_id
    assert SESSION_ID not in first.work_unit_id


def test_session_cost_falls_back_to_hermes_estimate_without_float_arithmetic(
    tmp_path: Path,
) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        "UPDATE sessions SET actual_cost_usd = 0, estimated_cost_usd = ?, "
        "cost_status = 'estimated', cost_source = 'official_docs_snapshot' WHERE id = ?",
        ("0.123456789012", SESSION_ID),
    )
    _execute(
        path,
        "UPDATE session_model_usage SET actual_cost_usd = 0, estimated_cost_usd = ?, "
        "cost_status = 'estimated', cost_source = 'official_docs_snapshot' "
        "WHERE session_id = ?",
        ("0.123456789012", SESSION_ID),
    )

    trace = import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    assert trace.estimated_total_cost_usd == Decimal("0.123456789012")
    assert trace.provider_reported_total_cost_usd is None


def test_sqlite_import_includes_same_route_auxiliary_usage_and_cost(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        """
        INSERT INTO session_model_usage (
            session_id, model, billing_provider, billing_base_url, billing_mode,
            task, api_call_count, input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd,
            cost_status, cost_source
        ) VALUES (?, 'synthetic-model', 'synthetic-provider',
                  'https://synthetic-provider.invalid/v1', 'synthetic-direct',
                  'vision', 1, 100, 10, 20, 5, 2, 0.002, 0,
                  NULL, NULL)
        """,
        (SESSION_ID,),
    )

    trace = import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    assert trace.model_request_count == 4
    assert trace.input_uncached_tokens == 1300
    assert trace.input_cache_read_tokens == 820
    assert trace.input_cache_write_tokens == 205
    assert trace.output_total_tokens == 360
    assert trace.reasoning_tokens == 52
    assert trace.estimated_total_cost_usd == Decimal("0.0125")
    assert trace.provider_reported_total_cost_usd is None


def test_sqlite_import_includes_auxiliary_usage_when_billing_mode_is_absent(
    tmp_path: Path,
) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        "UPDATE session_model_usage SET billing_mode = '' WHERE session_id = ?",
        (SESSION_ID,),
    )
    _execute(
        path,
        """
        INSERT INTO session_model_usage (
            session_id, model, billing_provider, billing_base_url, billing_mode,
            task, api_call_count, input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd,
            cost_status, cost_source
        ) VALUES (?, 'synthetic-model', 'synthetic-provider',
                  'https://synthetic-provider.invalid/v1/', '',
                  'title_generation', 1, 100, 10, 20, 5, 2, 0.002, 0,
                  NULL, NULL)
        """,
        (SESSION_ID,),
    )
    mapping = _mapping_with_billing_mode(None)

    trace = import_hermes_session(path, mapping=mapping, identity_key=IDENTITY_KEY)

    assert mapping.route.billing_mode is None
    assert mapping.route.offering.billing_mode is None
    assert trace.model_request_count == 4
    assert trace.input_uncached_tokens == 1300
    assert trace.input_cache_read_tokens == 820
    assert trace.input_cache_write_tokens == 205
    assert trace.output_total_tokens == 360
    assert trace.reasoning_tokens == 52
    assert trace.estimated_total_cost_usd == Decimal("0.0125")
    assert trace.provider_reported_total_cost_usd is None


def test_session_with_mixed_absent_and_reported_billing_modes_is_rejected(
    tmp_path: Path,
) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        "UPDATE session_model_usage SET billing_mode = '' WHERE session_id = ?",
        (SESSION_ID,),
    )
    _execute(
        path,
        """
        INSERT INTO session_model_usage (
            session_id, model, billing_provider, billing_base_url, billing_mode,
            task, api_call_count, input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd
        ) VALUES (?, 'synthetic-model', 'synthetic-provider',
                  'https://synthetic-provider.invalid/v1', 'synthetic-direct',
                  'title_generation', 1, 1, 1, 0, 0, 0, 0, 0)
        """,
        (SESSION_ID,),
    )

    with pytest.raises(HermesAdapterError, match="multiple billing routes"):
        import_hermes_session(
            path,
            mapping=_mapping_with_billing_mode(None),
            identity_key=IDENTITY_KEY,
        )


@pytest.mark.parametrize(
    ("ledger_mode", "reviewed_mode"),
    [
        ("", "synthetic-direct"),
        ("synthetic-direct", None),
    ],
)
def test_session_billing_mode_must_exactly_match_the_reviewed_route(
    tmp_path: Path,
    ledger_mode: str,
    reviewed_mode: str | None,
) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        "UPDATE session_model_usage SET billing_mode = ? WHERE session_id = ?",
        (ledger_mode, SESSION_ID),
    )

    with pytest.raises(HermesAdapterError, match="does not match the reviewed offering"):
        import_hermes_session(
            path,
            mapping=_mapping_with_billing_mode(reviewed_mode),
            identity_key=IDENTITY_KEY,
        )


def test_session_rejects_non_text_billing_mode_in_lookalike_schema(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    _remove_usage_table_constraints(path)
    _execute(
        path,
        "UPDATE session_model_usage SET billing_mode = NULL WHERE session_id = ?",
        (SESSION_ID,),
    )

    with pytest.raises(HermesAdapterError, match="billing_mode must be text"):
        import_hermes_session(
            path,
            mapping=_mapping_with_billing_mode("None"),
            identity_key=IDENTITY_KEY,
        )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE session_model_usage SET model = NULL WHERE session_id = ?",
            "model must be text",
        ),
        (
            "UPDATE session_model_usage SET billing_provider = NULL WHERE session_id = ?",
            "billing_provider must be text",
        ),
        (
            "UPDATE session_model_usage SET task = NULL WHERE session_id = ?",
            "task must be text",
        ),
    ],
)
def test_session_rejects_non_text_ledger_identity_in_lookalike_schema(
    tmp_path: Path,
    statement: str,
    message: str,
) -> None:
    path = _state_db(tmp_path)
    _remove_usage_table_constraints(path)
    _execute(path, statement, (SESSION_ID,))

    with pytest.raises(HermesAdapterError, match=message):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "https://synthetic-provider.invalid/v1?",
        "https://synthetic-provider.invalid/v1#",
        "https://synthetic-provider.invalid/v1 ",
        "https:\\synthetic-provider.invalid\\v1",
    ],
)
def test_session_rejects_unsafe_ledger_base_url(tmp_path: Path, unsafe_url: str) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        "UPDATE session_model_usage SET billing_base_url = ? WHERE session_id = ?",
        (unsafe_url, SESSION_ID),
    )

    with pytest.raises(HermesAdapterError, match="not a safe route URL"):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)


def test_session_with_multiple_billing_routes_is_rejected(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    _execute(
        path,
        """
        INSERT INTO session_model_usage (
            session_id, model, billing_provider, billing_base_url, billing_mode,
            task, api_call_count, input_tokens, output_tokens, cache_read_tokens,
            cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd
        ) VALUES (?, 'other-model', 'other-provider', 'https://other.invalid/v1',
                  'synthetic-direct', '', 1, 1, 1, 0, 0, 0, 0, 0)
        """,
        (SESSION_ID,),
    )

    with pytest.raises(HermesAdapterError, match="multiple billing routes"):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)


def test_active_or_unattributed_session_is_rejected(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    _execute(path, "UPDATE sessions SET ended_at = NULL WHERE id = ?", (SESSION_ID,))
    with pytest.raises(HermesAdapterError, match="active"):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)

    _execute(path, "UPDATE sessions SET ended_at = 1788105600 WHERE id = ?", (SESSION_ID,))
    _execute(path, "DELETE FROM session_model_usage WHERE session_id = ?", (SESSION_ID,))
    with pytest.raises(HermesAdapterError, match="no authoritative model-usage ledger"):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)


def test_unknown_session_and_schema_fail_closed_without_echoing_session_id(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    missing = MAPPING.model_copy(update={"session_id": "private-missing-session"})
    with pytest.raises(HermesAdapterError, match="not found") as exc_info:
        import_hermes_session(path, mapping=missing, identity_key=IDENTITY_KEY)
    assert "private-missing-session" not in str(exc_info.value)

    _execute(path, "UPDATE schema_version SET version = 27")
    with pytest.raises(HermesAdapterError, match="expected exactly version 26"):
        import_hermes_session(path, mapping=MAPPING, identity_key=IDENTITY_KEY)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("input_tokens", -1, "non-negative safe integer"),
        ("api_calls", True, "integer or null"),
        ("reasoning_tokens", 351, "cannot exceed"),
        ("total_tokens", 9999, "disagrees"),
    ],
)
def test_usage_report_rejects_invalid_aggregate_meters(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = json.loads((FIXTURES / "hermes_usage_synthetic.json").read_text(encoding="utf-8"))
    payload[field] = value
    path = tmp_path / "usage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HermesAdapterError, match=message):
        import_hermes_usage_report(
            path,
            mapping=MAPPING,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )


def test_usage_report_rejects_duplicate_keys_mismatch_and_naive_time(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    secret = "sk-" + "proj-secret-shaped-duplicate-key"
    duplicate.write_text(
        '{"session_id":"one","' + secret + '":1,"' + secret + '":2}',
        encoding="utf-8",
    )
    with pytest.raises(HermesAdapterError, match="invalid Hermes usage report JSON") as captured:
        import_hermes_usage_report(
            duplicate,
            mapping=MAPPING,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )
    assert secret not in str(captured.value)

    mismatch = MAPPING.model_copy(update={"session_id": "other-session"})
    with pytest.raises(HermesAdapterError, match="does not match"):
        import_hermes_usage_report(
            FIXTURES / "hermes_usage_synthetic.json",
            mapping=mismatch,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            identity_key=IDENTITY_KEY,
        )

    with pytest.raises(HermesAdapterError, match="timezone"):
        import_hermes_usage_report(
            FIXTURES / "hermes_usage_synthetic.json",
            mapping=MAPPING,
            observed_at=datetime(2026, 8, 30),
            identity_key=IDENTITY_KEY,
        )


def test_private_mapping_loader_is_bounded_strict_and_value_neutral(tmp_path: Path) -> None:
    valid = tmp_path / "mapping.json"
    valid.write_text(MAPPING.model_dump_json(), encoding="utf-8")
    assert load_hermes_session_mapping(valid) == MAPPING

    secret = "sk-" + "proj-private-mapping-value"
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"session_id":"' + secret + '","session_id":"again"}',
        encoding="utf-8",
    )
    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping JSON") as error:
        load_hermes_session_mapping(duplicate)
    assert secret not in str(error.value)

    invalid = tmp_path / "invalid.json"
    payload = MAPPING.model_dump(mode="json")
    payload["session_id"] = secret
    payload["hermes_version"] = "0.21.1"
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HermesAdapterError, match="invalid Hermes session mapping") as error:
        load_hermes_session_mapping(invalid)
    assert secret not in str(error.value)

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as stream:
        stream.truncate(MAX_HERMES_SESSION_MAPPING_BYTES + 1)
    with pytest.raises(HermesAdapterError, match="exceeds"):
        load_hermes_session_mapping(oversized)

    alias = tmp_path / "alias.json"
    alias.symlink_to(valid)
    with pytest.raises(HermesAdapterError, match="cannot read"):
        load_hermes_session_mapping(alias)


def test_short_identity_key_is_rejected_without_importing(tmp_path: Path) -> None:
    path = _state_db(tmp_path)
    with pytest.raises(HermesAdapterError, match="at least 16 bytes"):
        import_hermes_session(path, mapping=MAPPING, identity_key=b"too-short")


@pytest.mark.parametrize(
    "mutation",
    [
        {"hermes_version": "0.20.5"},
        {"hermes_version": "0.21.1"},
        {
            "workload": WorkloadReference(
                id="../private-workload",
                version="1",
                unit="session",
            )
        },
    ],
)
def test_mapping_requires_reviewed_version_and_content_free_public_metadata(
    mutation: dict[str, object],
) -> None:
    payload = MAPPING.model_dump(mode="python")
    payload.update(mutation)
    with pytest.raises(ValidationError):
        HermesSessionMapping.model_validate(payload)


@pytest.mark.parametrize(
    "offering_id",
    [
        "https://provider.invalid/model",
        "provider/" + "sk-" + "proj-" + "syntheticcredentialvalue",
    ],
)
def test_route_mapping_rejects_unsafe_public_offering_ids(offering_id: str) -> None:
    payload = ROUTE.model_dump(mode="python")
    payload["offering"] = ROUTE.offering.model_copy(update={"offering_id": offering_id})
    with pytest.raises(ValidationError):
        HermesRouteMapping.model_validate(payload)


def test_route_mapping_does_not_rewrite_operator_endpoint_identity() -> None:
    payload = ROUTE.model_dump(mode="python")
    payload["offering"] = ROUTE.offering.model_copy(
        update={"endpoint": f"{ROUTE.billing_base_url}/"}
    )
    with pytest.raises(ValidationError, match="base URL must match the offering endpoint"):
        HermesRouteMapping.model_validate(payload)
