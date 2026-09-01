from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

import model_skyline.arc_feed_monitor as arc_feed_monitor
from model_skyline.adapters.arc_agi import ARC_AGI_HF_DATASET_ID, ARC_AGI_HF_REVISION
from model_skyline.arc_feed_monitor import (
    ARC_AGI_HF_HEAD_API,
    MAX_HEAD_METADATA_BYTES,
    ArcAgiFeedMonitorError,
    ArcAgiFeedState,
    inspect_arc_agi_feed,
)

NOW = datetime(2026, 8, 31, 23, tzinfo=UTC)
PINNED_LAST_MODIFIED = "2026-06-04T16:45:03.000Z"


def _metadata(
    *,
    revision: str = ARC_AGI_HF_REVISION,
    last_modified: str = PINNED_LAST_MODIFIED,
    **overrides: Any,
) -> bytes:
    value: dict[str, Any] = {
        "id": ARC_AGI_HF_DATASET_ID,
        "author": "arcprize",
        "sha": revision,
        "lastModified": last_modified,
        "private": False,
        "gated": False,
        "disabled": False,
        # The API may expose paths, but the monitor neither interprets nor
        # returns them. This sentinel makes accidental publication detectable.
        "siblings": [{"rfilename": "sensitive-model-label/attempt_001.json"}],
    }
    value.update(overrides)
    return json.dumps(value, separators=(",", ":")).encode()


def _response(
    body: bytes,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    selected_headers = {"content-type": "application/json"}
    if headers is not None:
        selected_headers.update(headers)
    return httpx.Response(
        status_code,
        headers=selected_headers,
        stream=httpx.ByteStream(body),
    )


def _inspect_with(handler: httpx.MockTransport) -> Any:
    with httpx.Client(transport=handler, follow_redirects=False) as client:
        return inspect_arc_agi_feed(retrieved_at=NOW, client=client)


def test_pinned_head_yields_compact_publication_safe_status() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == ARC_AGI_HF_HEAD_API
        assert request.method == "GET"
        assert request.headers["accept"] == "application/json"
        assert request.headers["accept-encoding"] == "identity"
        return _response(_metadata())

    status = _inspect_with(httpx.MockTransport(handler))

    assert len(requests) == 1
    assert status.state is ArcAgiFeedState.PINNED
    assert status.review_required is False
    document = status.document()
    assert document["action"] == "none"
    assert document["different_head_policy"] == "no_automatic_semantic_reuse"
    rendered = json.dumps(document)
    assert "sensitive-model-label" not in rendered
    assert "siblings" not in rendered
    assert "raw" not in rendered


def test_different_head_requires_manual_review_without_fetching_files() -> None:
    changed_revision = "1" * 40
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return _response(_metadata(revision=changed_revision))

    status = _inspect_with(httpx.MockTransport(handler))

    assert requests == [ARC_AGI_HF_HEAD_API]
    assert status.observed_revision == changed_revision
    assert status.pinned_revision == ARC_AGI_HF_REVISION
    assert status.state is ArcAgiFeedState.REVIEW_REQUIRED
    assert status.review_required is True
    assert status.document()["action"] == "manual_adapter_review"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            _response(
                b"",
                status_code=302,
                headers={"location": "https://arcprize.org/do-not-fetch"},
            ),
            "redirected",
        ),
        (
            _response(_metadata(), headers={"content-encoding": "gzip"}),
            "compressed content",
        ),
        (
            _response(_metadata(), headers={"content-type": "text/html"}),
            "unexpected media type",
        ),
        (
            _response(_metadata(), headers={"content-length": "1, 1"}),
            "invalid Content-Length",
        ),
        (
            _response(
                b"{}",
                headers={"content-length": str(MAX_HEAD_METADATA_BYTES + 1)},
            ),
            "byte limit",
        ),
    ],
)
def test_rejects_unsafe_http_responses(
    response: httpx.Response,
    message: str,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    with pytest.raises(ArcAgiFeedMonitorError, match=message):
        _inspect_with(httpx.MockTransport(handler))
    assert calls == 1


def test_rejects_stream_larger_than_limit() -> None:
    response = _response(b" " * (MAX_HEAD_METADATA_BYTES + 1))

    with pytest.raises(ArcAgiFeedMonitorError, match="byte limit"):
        _inspect_with(httpx.MockTransport(lambda _request: response))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"sha":"' + ARC_AGI_HF_REVISION.encode() + b'","sha":"' + b"1" * 40 + b'"}',
        b'{"id":"arcprize/arc_agi_v2_public_eval","downloads":' + b"1" * 1_025 + b"}",
        b"[1,2,3]",
    ],
)
def test_rejects_ambiguous_or_resource_abusive_json(raw: bytes) -> None:
    with pytest.raises(ArcAgiFeedMonitorError):
        _inspect_with(httpx.MockTransport(lambda _request: _response(raw)))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "attacker/dataset"}, "another dataset"),
        ({"author": "attacker"}, "another dataset"),
        ({"sha": "main"}, "revision is invalid"),
        ({"private": True}, "no longer public"),
        ({"gated": "auto"}, "no longer public"),
        ({"disabled": True}, "is disabled"),
        ({"lastModified": "2030-01-01T00:00:00Z"}, "in the future"),
        ({"lastModified": "2026-06-04"}, "include a timezone"),
    ],
)
def test_rejects_invalid_or_unsafe_head_identity(
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ArcAgiFeedMonitorError, match=message):
        _inspect_with(httpx.MockTransport(lambda _request: _response(_metadata(**overrides))))


def test_collapses_transport_errors_without_leaking_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream detail", request=request)

    with pytest.raises(
        ArcAgiFeedMonitorError,
        match="^cannot fetch ARC-AGI head metadata$",
    ):
        _inspect_with(httpx.MockTransport(handler))


@pytest.mark.parametrize("timeout", [True, 0, -1, 61, float("inf"), float("nan")])
def test_rejects_invalid_timeouts(timeout: Any) -> None:
    with pytest.raises(ArcAgiFeedMonitorError, match="timeout_seconds"):
        inspect_arc_agi_feed(retrieved_at=NOW, timeout_seconds=timeout)


def test_live_check_owns_the_hardened_client(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = object()
    calls: list[dict[str, Any]] = []

    def fake_inspect(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(arc_feed_monitor, "inspect_arc_agi_feed", fake_inspect)

    actual = arc_feed_monitor.check_arc_agi_feed_live(
        retrieved_at=NOW,
        timeout_seconds=12,
    )

    assert actual is expected
    assert calls == [{"retrieved_at": NOW, "timeout_seconds": 12}]
