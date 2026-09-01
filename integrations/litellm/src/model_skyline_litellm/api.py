from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_ADMIN_TOKEN_ENV = "LITELLM_MASTER_KEY"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_ADMIN_RESPONSE_BYTES = 2 * 1024 * 1024
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_EMPTY_MODEL_CATALOG = {
    "detail": {
        "error": (
            "LLM Model List not loaded in. Make sure you passed models in your config.yaml "
            "or on the LiteLLM Admin UI. - https://docs.litellm.ai/docs/proxy/configs"
        )
    }
}

AliasValue = str | Mapping[str, Any]


class AdminAPIError(RuntimeError):
    """A content-free LiteLLM management failure."""


def _origin(base_url: str, *, allow_local_http: bool) -> str:
    parsed = urlsplit(base_url)
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("LiteLLM origin has an invalid host or port") from exc
    if (
        hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("LiteLLM base URL must be an origin without userinfo or path data")
    secure = parsed.scheme == "https"
    permitted_loopback = (
        parsed.scheme == "http" and allow_local_http and hostname.casefold() in _LOCAL_HOSTS
    )
    if not secure and not permitted_loopback:
        raise ValueError("LiteLLM management requires HTTPS or explicit loopback HTTP")
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    authority = f"{host}:{port}" if port is not None else host
    return f"{parsed.scheme}://{authority}"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-standard JSON number")


def _decode_object(body: bytes | bytearray) -> Mapping[str, Any]:
    try:
        decoded = json.loads(
            body,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise AdminAPIError("LiteLLM management response is not bounded JSON") from None
    if not isinstance(decoded, Mapping):
        raise AdminAPIError("LiteLLM management response must be a JSON object")
    return decoded


class LiteLLMAdminClient:
    """Narrow raw-HTTP client for the pinned LiteLLM management surface."""

    def __init__(
        self,
        base_url: str,
        *,
        token_env: str = DEFAULT_ADMIN_TOKEN_ENV,
        allow_local_http: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = MAX_ADMIN_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token_env or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in token_env
        ):
            raise ValueError("token environment variable name is invalid")
        token = os.environ.get(token_env)
        if token is None or not token:
            raise ValueError(f"LiteLLM admin token is required in environment variable {token_env}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError("max_response_bytes must be a positive integer")
        self._max_response_bytes = max_response_bytes
        self._client = httpx.Client(
            base_url=_origin(base_url, allow_local_http=allow_local_http),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> LiteLLMAdminClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            with self._client.stream(method, path, json=payload) as response:
                if response.is_redirect:
                    raise AdminAPIError("LiteLLM management request returned a redirect")
                status_code = response.status_code
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_size = int(declared)
                    except ValueError:
                        raise AdminAPIError(
                            "LiteLLM management response has invalid Content-Length"
                        ) from None
                    if declared_size < 0 or declared_size > self._max_response_bytes:
                        raise AdminAPIError("LiteLLM management response exceeds the size limit")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        raise AdminAPIError("LiteLLM management response exceeds the size limit")
        except AdminAPIError:
            raise
        except httpx.HTTPError:
            raise AdminAPIError("LiteLLM management request failed") from None
        if not 200 <= status_code < 300:
            # Pinned LiteLLM v1.98.0 represents a pristine DB as this exact
            # 500 response instead of {"data": []}. Accept no near-matches;
            # the first staged row makes subsequent reads use the normal shape.
            if status_code == 500 and method == "GET" and path == "/model/info":
                try:
                    if _decode_object(body) == _EMPTY_MODEL_CATALOG:
                        return {"data": []}
                except AdminAPIError:
                    pass
            raise AdminAPIError(f"LiteLLM management request returned HTTP status {status_code}")
        return _decode_object(body)

    def list_models(self) -> tuple[Mapping[str, Any], ...]:
        response = self._request_json("GET", "/model/info")
        data = response.get("data")
        if not isinstance(data, list) or any(not isinstance(item, Mapping) for item in data):
            raise AdminAPIError("LiteLLM model catalog response has an invalid shape")
        return tuple(data)

    def create_model(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request_json("POST", "/model/new", payload=payload)

    def get_runtime_config(self) -> Mapping[str, Any]:
        return self._request_json("GET", "/get/config/callbacks")

    def update_aliases(self, aliases: Mapping[str, AliasValue]) -> Mapping[str, Any]:
        if any(
            not isinstance(key, str) or not isinstance(value, (str, Mapping))
            for key, value in aliases.items()
        ):
            raise ValueError("LiteLLM aliases have an invalid shape")
        return self._request_json(
            "POST",
            "/config/update",
            payload={"router_settings": {"model_group_alias": dict(aliases)}},
        )
