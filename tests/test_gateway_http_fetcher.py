from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from model_skyline.gateway import GatewayProtocolError
from model_skyline.gateway_resolver import (
    GatewayTransportError,
    HttpxGatewayFetcher,
)

URL = "https://control.example/model-skyline/gateway/default.dsse.json"
MEDIA_TYPE = "application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+dsse"


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.chunks)


def _fetch(response: httpx.Response, *, maximum_bytes: int = 64) -> object:
    fetcher = HttpxGatewayFetcher(transport=httpx.MockTransport(lambda _request: response))
    return fetcher.fetch(
        URL,
        expected_media_type=MEDIA_TYPE,
        maximum_bytes=maximum_bytes,
        timeout_seconds=1,
        etag='"prior"',
    )


def test_http_fetcher_requests_exact_identity_bytes_and_returns_strong_etag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == MEDIA_TYPE
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["if-none-match"] == '"prior"'
        return httpx.Response(
            200,
            headers={"content-type": f"{MEDIA_TYPE}; charset=utf-8", "etag": '"next"'},
            stream=ChunkStream(b"exact"),
        )

    result = HttpxGatewayFetcher(transport=httpx.MockTransport(handler)).fetch(
        URL,
        expected_media_type=MEDIA_TYPE,
        maximum_bytes=64,
        timeout_seconds=1,
        etag='"prior"',
    )

    assert result.payload == b"exact"
    assert result.etag == '"next"'


def test_http_fetcher_handles_not_modified_without_new_bytes() -> None:
    result = _fetch(httpx.Response(304))

    assert result.payload is None
    assert result.etag == '"prior"'


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(302, headers={"location": "https://elsewhere.example"}), "redirect"),
        (
            httpx.Response(
                200,
                headers={"content-type": MEDIA_TYPE, "content-encoding": "gzip"},
                stream=ChunkStream(b"compressed"),
            ),
            "compressed",
        ),
        (
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}"),
            "media type",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": MEDIA_TYPE, "content-length": "65"},
                stream=ChunkStream(b"short"),
            ),
            "byte limit",
        ),
        (
            httpx.Response(
                206,
                headers={"content-type": MEDIA_TYPE, "content-range": "bytes 0-1/3"},
                content=b"ab",
            ),
            "partial",
        ),
    ],
)
def test_http_fetcher_rejects_ambiguous_or_transformed_responses(
    response: httpx.Response,
    message: str,
) -> None:
    with pytest.raises(GatewayProtocolError, match=message):
        _fetch(response)


def test_http_fetcher_enforces_streamed_limit_without_content_length() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": MEDIA_TYPE},
        stream=ChunkStream(b"a" * 40, b"b" * 25),
    )

    with pytest.raises(GatewayProtocolError, match="byte limit"):
        _fetch(response)


def test_http_fetcher_classifies_retryable_status_as_transport() -> None:
    with pytest.raises(GatewayTransportError, match="retryable HTTP 503"):
        _fetch(httpx.Response(503))
