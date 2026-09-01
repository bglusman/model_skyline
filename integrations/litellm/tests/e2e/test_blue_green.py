from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from model_skyline.io import load_selection_snapshot

from model_skyline_litellm.api import LiteLLMAdminClient
from model_skyline_litellm.models import IntegrationConfig, ProjectionPlan
from model_skyline_litellm.project import project_selection
from model_skyline_litellm.reconcile import activate, stage, verify_staged

INTEGRATION_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = INTEGRATION_ROOT / "compose.yaml"
FIXTURES = INTEGRATION_ROOT / "tests" / "fixtures"
PROXY_ORIGIN = "http://127.0.0.1:14000"
FAKE_ORIGIN = "http://127.0.0.1:18080"
MASTER_KEY = "modelskyline-e2e-master-only"
CONTROL_TOKEN = "modelskyline-e2e-control-only"

pytestmark = pytest.mark.skipif(
    os.environ.get("MODELSKYLINE_RUN_LITELLM_E2E") != "1",
    reason="set MODELSKYLINE_RUN_LITELLM_E2E=1 to run the pinned Docker integration",
)


def _compose(*arguments: str, timeout: float = 240) -> None:
    subprocess.run(
        ["docker", "compose", "--file", str(COMPOSE_FILE), *arguments],
        check=True,
        timeout=timeout,
    )


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    response = client.request(method, path, json=payload)
    if not 200 <= response.status_code < 300:
        raise AssertionError(f"synthetic E2E request returned HTTP {response.status_code}")
    value = json.loads(response.content)
    if not isinstance(value, Mapping):
        raise AssertionError("synthetic E2E response is not a JSON object")
    return value


def _wait_for_proxy() -> None:
    deadline = time.monotonic() + 180
    with httpx.Client(base_url=PROXY_ORIGIN, timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get("/health/liveliness")
            except httpx.HTTPError:
                time.sleep(1)
                continue
            if response.status_code == 200:
                return
            time.sleep(1)
    raise AssertionError("LiteLLM did not become live after restart")


def _create_named_credentials(client: httpx.Client) -> None:
    for backend in ("a", "b"):
        _request_json(
            client,
            "POST",
            "/credentials",
            payload={
                "credential_name": f"modelskyline/fake-{backend}",
                "credential_info": {"custom_llm_provider": "openai"},
                "credential_values": {
                    "api_base": f"http://fake-provider:8080/{backend}/v1",
                    "api_key": f"modelskyline-e2e-provider-{backend}-only",
                },
            },
        )


def _completion(client: httpx.Client, model: str) -> str:
    value = _request_json(
        client,
        "POST",
        "/v1/chat/completions",
        payload={
            "messages": [{"content": "identify the synthetic backend", "role": "user"}],
            "model": model,
        },
    )
    try:
        content = value["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AssertionError("LiteLLM completion response has an unexpected shape") from None
    if not isinstance(content, str):
        raise AssertionError("LiteLLM completion content is not text")
    return content


def _set_backend_status(client: httpx.Client, backend: str, status: int) -> None:
    response = client.post(
        f"/control/{backend}",
        headers={"X-Control-Token": CONTROL_TOKEN},
        json={"status": status},
    )
    if response.status_code != 200:
        raise AssertionError(f"fake-provider control returned HTTP {response.status_code}")


def _load_plans() -> tuple[ProjectionPlan, ProjectionPlan]:
    config_value = json.loads((FIXTURES / "bindings.json").read_bytes())
    config = IntegrationConfig.model_validate(config_value)
    selection_a = load_selection_snapshot(FIXTURES / "selection-a.json")
    selection_b = load_selection_snapshot(FIXTURES / "selection-b.json")
    plan_a = project_selection(selection_a, config, now=selection_a.generated_at)
    plan_b = project_selection(selection_b, config, now=selection_b.generated_at)
    return plan_a, plan_b


@pytest.fixture(scope="module")
def clean_stack() -> Iterator[None]:
    _compose("down", "--volumes", "--remove-orphans")
    try:
        _compose("up", "--detach", "--wait", "--wait-timeout", "180")
        yield
    finally:
        _compose("down", "--volumes", "--remove-orphans")


def test_blue_green_projection_and_restart_persistence(
    clean_stack: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_stack
    monkeypatch.setenv("LITELLM_MASTER_KEY", MASTER_KEY)
    plan_a, plan_b = _load_plans()
    assert plan_a.group_name != plan_b.group_name

    headers = {"Authorization": f"Bearer {MASTER_KEY}"}
    with (
        httpx.Client(
            base_url=PROXY_ORIGIN,
            headers=headers,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as proxy,
        httpx.Client(
            base_url=FAKE_ORIGIN,
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        ) as fake,
        LiteLLMAdminClient(PROXY_ORIGIN, allow_local_http=True) as admin,
    ):
        _create_named_credentials(proxy)

        stage(plan_a, admin, now=plan_a.generated_at)
        assert _completion(proxy, plan_a.group_name) == "backend-a"

        activate(plan_a, admin, now=plan_a.generated_at)
        assert _completion(proxy, plan_a.stable_alias) == "backend-a"

        _set_backend_status(fake, "a", 503)
        assert _completion(proxy, plan_a.stable_alias) == "backend-b"
        _set_backend_status(fake, "a", 200)

        stage(plan_b, admin, now=plan_b.generated_at)
        activate(plan_b, admin, now=plan_b.generated_at)
        assert _completion(proxy, plan_b.stable_alias) == "backend-b"

    _compose("restart", "litellm")
    _wait_for_proxy()

    with (
        httpx.Client(
            base_url=PROXY_ORIGIN,
            headers=headers,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as proxy,
        LiteLLMAdminClient(PROXY_ORIGIN, allow_local_http=True) as admin,
    ):
        verify_staged(plan_a, admin)
        verify_staged(plan_b, admin)
        assert _completion(proxy, plan_b.stable_alias) == "backend-b"
        assert _completion(proxy, plan_a.group_name) == "backend-a"
