#!/usr/bin/env python3
"""Render one Harbor JobConfig from the pinned local quality protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

MAX_INPUT_BYTES = 8_000_000


class PilotConfigError(ValueError):
    """The pinned protocol cannot produce an exact Harbor configuration."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PilotConfigError("protocol must be a regular file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise PilotConfigError("protocol is too large")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PilotConfigError("protocol is invalid YAML") from exc
    if not isinstance(value, dict):
        raise PilotConfigError("protocol must be an object")
    return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PilotConfigError(f"{field} must be an object with string keys")
    return value


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PilotConfigError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PilotConfigError(f"{field} must be a positive integer")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise PilotConfigError(f"{field} must be a boolean")
    return value


def _decimal(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PilotConfigError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise PilotConfigError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise PilotConfigError(f"{field} must be a finite number")
    return float(result)


def _task_names(protocol_root: Path, task_set: dict[str, Any]) -> list[str]:
    tasks = task_set.get("tasks")
    if not isinstance(tasks, list):
        manifest_name = _string(task_set.get("task_manifest"), field="task_set.task_manifest")
        manifest_input = protocol_root / manifest_name
        manifest_path = manifest_input.resolve()
        if (
            not manifest_path.is_relative_to(protocol_root.resolve())
            or manifest_input.is_symlink()
            or not manifest_input.is_file()
        ):
            raise PilotConfigError("task manifest escapes the protocol directory")
        if _sha256(manifest_path) != task_set.get("task_manifest_sha256"):
            raise PilotConfigError("task manifest does not match its pinned digest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PilotConfigError("task manifest is invalid JSON") from exc
        tasks = _mapping(manifest, field="task_manifest").get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise PilotConfigError("task set must contain tasks")
    result: list[str] = []
    for index, value in enumerate(tasks):
        task = _mapping(value, field=f"tasks[{index}]")
        full_name = _string(task.get("name"), field=f"tasks[{index}].name")
        result.append(full_name.rsplit("/", 1)[-1])
    if len(result) != len(set(result)):
        raise PilotConfigError("Harbor task basenames must be unique")
    return result


def render_config(
    *,
    protocol_path: Path,
    candidate_name: str,
    task_set_name: str,
    tasks_directory: Path,
    jobs_directory: Path,
    job_name: str,
    api_base: str,
    extra_docker_compose: list[Path],
) -> dict[str, Any]:
    protocol = _load_yaml(protocol_path)
    if protocol.get("schema_version") != "model-skyline/local-quality-pilot/v1":
        raise PilotConfigError("unsupported protocol schema_version")
    root = protocol_path.resolve().parent
    harness = _mapping(protocol.get("harness"), field="harness")
    candidates = _mapping(protocol.get("candidates"), field="candidates")
    candidate = _mapping(candidates.get(candidate_name), field=f"candidates.{candidate_name}")
    task_sets = _mapping(protocol.get("task_sets"), field="task_sets")
    task_set = _mapping(task_sets.get(task_set_name), field=f"task_sets.{task_set_name}")
    profile_name = _string(candidate.get("system_profile"), field="candidate.system_profile")
    profile_input = root / profile_name
    profile_path = profile_input.resolve()
    if (
        not profile_path.is_relative_to(root)
        or profile_input.is_symlink()
        or not profile_input.is_file()
        or _sha256(profile_path) != candidate.get("system_profile_sha256")
    ):
        raise PilotConfigError("candidate system profile does not match its pinned digest")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    route = _string(candidate.get("route"), field="candidate.route")
    if not isinstance(profile, dict) or profile.get("served_model") != route:
        raise PilotConfigError("candidate route and system profile disagree")
    agent_name = _string(harness.get("name"), field="harness.name").removeprefix("harbor/")
    environment: dict[str, Any] = {"type": "docker"}
    if extra_docker_compose:
        environment["extra_docker_compose"] = [str(path.resolve()) for path in extra_docker_compose]
    return {
        "job_name": job_name,
        "jobs_dir": str(jobs_directory.resolve()),
        "n_concurrent_trials": _integer(harness.get("concurrency"), field="harness.concurrency"),
        "environment": environment,
        "agents": [
            {
                "name": agent_name,
                "model_name": f"openai/{route}",
                "kwargs": {
                    "api_base": api_base.rstrip("/"),
                    "parser_name": _string(harness.get("parser"), field="harness.parser"),
                    "temperature": _decimal(
                        harness.get("temperature"), field="harness.temperature"
                    ),
                    "max_turns": _integer(harness.get("max_turns"), field="harness.max_turns"),
                    "enable_summarize": _boolean(
                        harness.get("summarization_enabled"),
                        field="harness.summarization_enabled",
                    ),
                    "proactive_summarization_threshold": _integer(
                        harness.get("proactive_summarization_free_tokens"),
                        field="harness.proactive_summarization_free_tokens",
                    ),
                    "model_info": {
                        "max_input_tokens": _integer(
                            harness.get("max_input_tokens"), field="harness.max_input_tokens"
                        ),
                        "max_output_tokens": _integer(
                            harness.get("max_output_tokens"), field="harness.max_output_tokens"
                        ),
                        "input_cost_per_token": 0,
                        "output_cost_per_token": 0,
                    },
                    "llm_call_kwargs": {
                        "top_p": _decimal(harness.get("top_p"), field="harness.top_p")
                    },
                    "store_all_messages": _boolean(
                        harness.get("store_all_messages"), field="harness.store_all_messages"
                    ),
                },
            }
        ],
        "datasets": [
            {
                "path": str(tasks_directory.resolve()),
                "task_names": _task_names(root, task_set),
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--task-set", required=True)
    parser.add_argument("--tasks-directory", type=Path, required=True)
    parser.add_argument("--jobs-directory", type=Path, required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--extra-docker-compose", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = render_config(
            protocol_path=args.protocol,
            candidate_name=args.candidate,
            task_set_name=args.task_set,
            tasks_directory=args.tasks_directory,
            jobs_directory=args.jobs_directory,
            job_name=args.job_name,
            api_base=args.api_base,
            extra_docker_compose=args.extra_docker_compose,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
