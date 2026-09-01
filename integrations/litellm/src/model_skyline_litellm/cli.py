from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

from model_skyline.models import SelectionSnapshot
from model_skyline.resolver import DynamicResolver, ResolverError
from pydantic import ValidationError

from model_skyline_litellm.api import (
    AdminAPIError,
    LiteLLMAdminClient,
    _reject_constant,
    _unique_object,
)
from model_skyline_litellm.models import IntegrationConfig
from model_skyline_litellm.project import ProjectionError, project_selection
from model_skyline_litellm.reconcile import (
    IndeterminateActivationError,
    ReconcileError,
    activate,
    stage,
)

MAX_CONFIG_BYTES = 1024 * 1024
MAX_ERROR_CHARACTERS = 2048


class CLIError(RuntimeError):
    """A content-free command-line failure."""


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError:
        raise CLIError("--at must be an ISO 8601 timestamp") from None
    if result.tzinfo is None:
        raise CLIError("--at must include a timezone")
    return result


def _load_config(path: Path) -> IntegrationConfig:
    try:
        size = path.stat().st_size
        if size > MAX_CONFIG_BYTES:
            raise CLIError("integration configuration exceeds the size limit")
        raw = path.read_bytes()
    except CLIError:
        raise
    except OSError:
        raise CLIError("cannot read integration configuration") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise CLIError("integration configuration exceeds the size limit")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return IntegrationConfig.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValidationError, ValueError):
        # Validation errors include rejected input values, which may be a
        # mistakenly pasted credential. Never echo them to a terminal.
        raise CLIError("integration configuration is invalid") from None


def _selection(
    path: Path,
    config: IntegrationConfig,
    now: datetime,
) -> SelectionSnapshot:
    resolver = DynamicResolver(
        path,
        expected_selection_id=config.expected_selection_id,
        expected_frontier_id=config.expected_frontier_id,
        expected_workload_id=config.expected_workload.id,
        expected_workload_version=config.expected_workload.version,
        refresh_interval=timedelta(0),
        stale_if_error=timedelta(0),
        allow_local_file=True,
        clock=lambda: now,
    )
    snapshot = resolver.resolve(force_refresh=True)
    if snapshot.workload != config.expected_workload:
        raise CLIError("selection workload unit does not match the integration pin")
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modelskyline-litellm",
        description="Stage and activate an experimental LiteLLM blue/green projection.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "stage", "activate"):
        command = subparsers.add_parser(name)
        command.add_argument("selection", type=Path)
        command.add_argument("config", type=Path)
        command.add_argument("--at", help="trusted ISO 8601 verification time")
        if name != "plan":
            command.add_argument("--base-url", required=True, help="LiteLLM proxy origin")
            command.add_argument(
                "--allow-local-http",
                action="store_true",
                help="permit plain HTTP only for a loopback integration test",
            )
    return parser


def _safe_error(value: str) -> str:
    result: list[str] = []
    used = 0
    for character in value:
        category = unicodedata.category(character)
        rendered = character if category not in {"Cc", "Cf", "Cs"} else "?"
        if used + len(rendered) > MAX_ERROR_CHARACTERS:
            result.append("…[truncated]")
            break
        result.append(rendered)
        used += len(rendered)
    return "".join(result)


def run(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        now = _timestamp(arguments.at)
        config = _load_config(arguments.config)
        snapshot = _selection(arguments.selection, config, now)
        plan = project_selection(snapshot, config, now=now)
        if arguments.command == "plan":
            print(json.dumps(plan.public_summary(), indent=2, sort_keys=True))
            return 0
        with LiteLLMAdminClient(
            arguments.base_url,
            allow_local_http=arguments.allow_local_http,
        ) as api:
            if arguments.command == "stage":
                stage(plan, api, now=now)
                print(
                    f"staged {plan.snapshot_id} as {plan.group_name} "
                    f"({len(plan.deployments)} deployments)"
                )
            else:
                activate(plan, api, now=now)
                print(f"activated {plan.stable_alias} -> {plan.group_name}")
        return 0
    except IndeterminateActivationError as exc:
        print(f"indeterminate: {_safe_error(str(exc))}", file=sys.stderr)
        return 3
    except (
        AdminAPIError,
        CLIError,
        ProjectionError,
        ReconcileError,
        ResolverError,
        ValueError,
    ) as exc:
        print(f"error: {_safe_error(str(exc))}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(run())
