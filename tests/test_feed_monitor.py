from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import model_skyline.feed_monitor as feed_monitor
from model_skyline.adapters.swe_bench import (
    SweBenchCapture,
    SweBenchSourceIdentityMode,
    normalize_swe_bench_bytes,
)
from model_skyline.cli import app
from model_skyline.feed_monitor import (
    FeedMonitorError,
    SweBenchFeedChange,
    SweBenchFeedStatus,
    classify_swe_bench_change,
    inspect_swe_bench_feed,
)

NOW = datetime(2026, 8, 31, 22, tzinfo=UTC)


def _details(*, resolved_count: int, task_prefix: str = "task") -> dict[str, Any]:
    return {
        f"{task_prefix}-{index:03d}": {
            "api_calls": 2,
            "cost": 0.1,
            "resolved": index < resolved_count,
        }
        for index in range(500)
    }


def _row(
    suffix: str,
    *,
    resolved_count: int = 400,
    task_prefix: str = "task",
    warning: str | None = None,
) -> dict[str, Any]:
    return {
        "agent": "mini-SWE-agent",
        "agent_org": "Synthetic SWE-agent",
        "checked": None,
        "cost": 50,
        "date": "2026-08-30",
        "folder": f"20260830_mini-v2.0.0_{suffix}",
        "instance_calls": 2,
        "instance_cost": 0.1,
        "logo": [],
        "logs": None,
        "mini-swe-agent_version": "2.0.0",
        "model_display": f"Synthetic Model {suffix}",
        "model_org": "Synthetic Org",
        "model_release_date": 20260801,
        "name": f"Synthetic Model {suffix} (high)",
        "os_model": False,
        "os_system": True,
        "per_instance_details": _details(
            resolved_count=resolved_count,
            task_prefix=task_prefix,
        ),
        "reasoning_effort": "high",
        "resolved": resolved_count / 5,
        "site": "https://example.invalid",
        "tags": [
            f"Model: synthetic-model-{suffix}",
            "Org: Synthetic Org",
            "System: Attempts - 1",
            "Mini: 2.0.0",
        ],
        "trajs": None,
        "trajs_docent": False,
        "warning": warning,
    }


def _raw(*rows: dict[str, Any], unrelated: str = "first") -> bytes:
    return json.dumps(
        {
            "leaderboards": [
                {"name": "Verified", "results": [{"revision": unrelated}]},
                {"name": "bash-only", "results": list(rows)},
            ]
        },
        separators=(",", ":"),
    ).encode()


def _capture(raw: bytes) -> SweBenchCapture:
    return normalize_swe_bench_bytes(
        raw,
        retrieved_at=NOW,
        source_locator="operator-local-capture:test",
        upstream_revision="test",
        source_identity_mode=SweBenchSourceIdentityMode.OFFICIAL_SEMANTIC,
    )


def test_classifies_narrowest_changed_identity_boundary() -> None:
    pinned = _capture(_raw(_row("alpha")))

    assert (
        classify_swe_bench_change(
            pinned,
            _capture(_raw(_row("alpha"), unrelated="second")),
            raw_bytes_equal=False,
        )
        is SweBenchFeedChange.RAW_ONLY
    )
    assert (
        classify_swe_bench_change(
            pinned,
            _capture(_raw(_row("alpha", resolved_count=399))),
            raw_bytes_equal=False,
        )
        is SweBenchFeedChange.RESULT
    )
    assert (
        classify_swe_bench_change(
            pinned,
            _capture(_raw(_row("alpha", warning="changed"))),
            raw_bytes_equal=False,
        )
        is SweBenchFeedChange.SUBJECT
    )
    assert (
        classify_swe_bench_change(
            pinned,
            _capture(_raw(_row("alpha"), _row("beta"))),
            raw_bytes_equal=False,
        )
        is SweBenchFeedChange.SUBJECT_SET
    )
    assert (
        classify_swe_bench_change(
            pinned,
            _capture(_raw(_row("alpha", task_prefix="different"))),
            raw_bytes_equal=False,
        )
        is SweBenchFeedChange.SOURCE
    )


def test_inspects_latest_revision_without_retaining_raw_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw(_row("alpha"))
    digest = sha256(raw).hexdigest()
    revision = "1" * 40
    monkeypatch.setattr(feed_monitor, "SWE_BENCH_WEBSITE_SHA256", digest)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == "Bearer workflow-secret"
            return httpx.Response(
                200,
                stream=httpx.ByteStream(json.dumps([{"sha": revision}]).encode()),
            )
        assert str(request.url) == feed_monitor.SWE_BENCH_RAW_AT_REVISION.format(revision=revision)
        assert "authorization" not in request.headers
        return httpx.Response(200, stream=httpx.ByteStream(raw))

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Accept-Encoding": "identity"},
    ) as client:
        status = inspect_swe_bench_feed(
            github_token="workflow-secret",
            retrieved_at=NOW,
            client=client,
        )

    assert status.change is SweBenchFeedChange.NONE
    assert status.semantic_change is False
    assert status.raw_sha256 == digest
    assert status.rows_seen == 1
    assert status.valid_rows == 1
    assert "model_display" not in status.document()


def test_rejects_duplicate_revision_response_keys() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b'[{"sha":"' + b"1" * 40 + b'","sha":"bad"}]'),
        )

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"Accept-Encoding": "identity"},
        ) as client,
        pytest.raises(FeedMonitorError, match="invalid JSON"),
    ):
        inspect_swe_bench_feed(retrieved_at=NOW, client=client)


def test_cli_renders_status_before_exit_three_on_semantic_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = SweBenchFeedStatus(
        latest_file_revision="1" * 40,
        retrieved_at=NOW,
        raw_sha256="2" * 64,
        pinned_raw_sha256="3" * 64,
        source_identity_sha256="4" * 64,
        change=SweBenchFeedChange.RESULT,
        rows_seen=13,
        valid_rows=11,
        invalid_rows=2,
        invalid_reason_counts=(("missing_per_instance_details", 2),),
    )
    monkeypatch.setattr("model_skyline.cli.inspect_swe_bench_feed", lambda **_kwargs: status)

    strict = CliRunner().invoke(app, ["check-swe-bench-feed"])
    report_only = CliRunner().invoke(app, ["check-swe-bench-feed", "--report-only"])

    assert strict.exit_code == 3
    assert '"change": "result_changed"' in strict.output
    assert report_only.exit_code == 0
    assert '"action": "review_and_repin"' in report_only.output
