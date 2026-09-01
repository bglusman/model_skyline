from __future__ import annotations

import json

import httpx
import pytest

from model_skyline_litellm.api import AdminAPIError, LiteLLMAdminClient


def _client(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
    *,
    max_response_bytes: int = 1024,
) -> LiteLLMAdminClient:
    monkeypatch.setenv("LITELLM_MASTER_KEY", "private-admin-token")
    return LiteLLMAdminClient(
        "http://127.0.0.1:14000",
        allow_local_http=True,
        max_response_bytes=max_response_bytes,
        transport=handler,
    )


def test_model_catalog_uses_admin_token_but_never_returns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/model/info"
        assert request.headers["authorization"] == "Bearer private-admin-token"
        return httpx.Response(200, json={"data": []})

    with _client(monkeypatch, httpx.MockTransport(handle)) as client:
        assert client.list_models() == ()


def test_pinned_pristine_database_response_is_the_only_accepted_500_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact = {
        "detail": {
            "error": (
                "LLM Model List not loaded in. Make sure you passed models in your config.yaml "
                "or on the LiteLLM Admin UI. - https://docs.litellm.ai/docs/proxy/configs"
            )
        }
    }
    responses = iter(
        [
            httpx.Response(500, json=exact),
            httpx.Response(500, json={"detail": {"error": "almost the pinned message"}}),
        ]
    )

    with _client(
        monkeypatch,
        httpx.MockTransport(lambda _request: next(responses)),
    ) as client:
        assert client.list_models() == ()
        with pytest.raises(AdminAPIError, match="HTTP status 500"):
            client.list_models()


def test_create_and_alias_calls_use_only_the_reviewed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/get/config/callbacks":
            return httpx.Response(200, json={"router_settings": {"model_group_alias": {}}})
        return httpx.Response(200, json={"ok": True})

    with _client(monkeypatch, httpx.MockTransport(handle)) as client:
        client.create_model({"model_name": "managed"})
        client.get_runtime_config()
        client.update_aliases(
            {
                "skyline/coding": "managed",
                "hidden": {"model": "hidden-group", "hidden": True},
            }
        )

    assert calls == [
        ("POST", "/model/new", {"model_name": "managed"}),
        ("GET", "/get/config/callbacks", None),
        (
            "POST",
            "/config/update",
            {
                "router_settings": {
                    "model_group_alias": {
                        "skyline/coding": "managed",
                        "hidden": {"model": "hidden-group", "hidden": True},
                    }
                }
            },
        ),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com?token=value",
    ],
)
def test_rejects_unsafe_management_origins(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITELLM_MASTER_KEY", "safe-test-token")
    with pytest.raises(ValueError, match="origin|HTTPS"):
        LiteLLMAdminClient(url)


def test_rejects_redirect_status_and_oversized_or_duplicate_json_without_body_leaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            httpx.Response(302, headers={"location": "https://private.invalid/secret"}),
            httpx.Response(500, text="private-admin-token and private body"),
            httpx.Response(200, content=b"x" * 65),
            httpx.Response(200, content=b'{"data":[],"data":[]}'),
        ]
    )

    def handle(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    with _client(
        monkeypatch,
        httpx.MockTransport(handle),
        max_response_bytes=64,
    ) as client:
        captured: list[str] = []
        for _ in range(4):
            with pytest.raises(AdminAPIError) as error:
                client.list_models()
            captured.append(str(error.value))

    combined = " ".join(captured)
    assert "private-admin-token" not in combined
    assert "private body" not in combined
    assert "private.invalid" not in combined


def test_requires_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    with pytest.raises(ValueError, match="environment"):
        LiteLLMAdminClient("https://gateway.example")
