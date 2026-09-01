#!/usr/bin/env python3
"""Small stateful OpenAI-compatible server used only by the local E2E test."""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

MAX_BODY_BYTES: Final = 1024 * 1024
CONTROL_TOKEN: Final = "modelskyline-e2e-control-only"
BACKEND_KEYS: Final = {
    "a": "modelskyline-e2e-provider-a-only",
    "b": "modelskyline-e2e-provider-b-only",
}

_lock = threading.Lock()
_status = {"a": HTTPStatus.OK, "b": HTTPStatus.OK}
_requests = {"a": 0, "b": 0}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ModelSkylineFakeOpenAI/1"

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, _format: str, *_args: object) -> None:
        # Request bodies and authorization headers must not reach container logs.
        return

    def _reply(self, status: HTTPStatus, value: Any) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid length"}})
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid body"}})
            return None
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid json"}})
            return None
        if not isinstance(value, dict):
            self._reply(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid object"}})
            return None
        return value

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._reply(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/state":
            with _lock:
                state = {
                    backend: {"requests": _requests[backend], "status": int(_status[backend])}
                    for backend in sorted(BACKEND_KEYS)
                }
            self._reply(HTTPStatus.OK, state)
            return
        self._reply(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) == 2 and parts[0] == "control" and parts[1] in BACKEND_KEYS:
            self._control(parts[1])
            return
        if (
            len(parts) == 4
            and parts[0] in BACKEND_KEYS
            and parts[1:] == ["v1", "chat", "completions"]
        ):
            self._completion(parts[0])
            return
        self._reply(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def _control(self, backend: str) -> None:
        if self.headers.get("X-Control-Token") != CONTROL_TOKEN:
            self._reply(HTTPStatus.FORBIDDEN, {"error": {"message": "forbidden"}})
            return
        body = self._body()
        if body is None:
            return
        status = body.get("status")
        if isinstance(status, bool) or status not in (200, 503):
            self._reply(HTTPStatus.BAD_REQUEST, {"error": {"message": "invalid status"}})
            return
        with _lock:
            _status[backend] = HTTPStatus(status)
        self._reply(HTTPStatus.OK, {"backend": backend, "status": status})

    def _completion(self, backend: str) -> None:
        expected = f"Bearer {BACKEND_KEYS[backend]}"
        if self.headers.get("Authorization") != expected:
            self._reply(HTTPStatus.UNAUTHORIZED, {"error": {"message": "bad credential"}})
            return
        body = self._body()
        if body is None:
            return
        if not isinstance(body.get("messages"), list):
            self._reply(HTTPStatus.BAD_REQUEST, {"error": {"message": "missing messages"}})
            return
        with _lock:
            _requests[backend] += 1
            status = _status[backend]
            request_number = _requests[backend]
        if status != HTTPStatus.OK:
            self._reply(
                status,
                {
                    "error": {
                        "message": f"synthetic backend-{backend} failure",
                        "type": "server_error",
                    }
                },
            )
            return
        self._reply(
            HTTPStatus.OK,
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {"content": f"backend-{backend}", "role": "assistant"},
                    }
                ],
                "created": int(time.time()),
                "id": f"chatcmpl-{backend}-{request_number}",
                "model": f"fake-{backend}",
                "object": "chat.completion",
                "usage": {"completion_tokens": 1, "prompt_tokens": 1, "total_tokens": 2},
            },
        )


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
