from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "local-runtime-frontiers"
SCRIPT = EXAMPLE / "render_harbor_pilot_config.py"
SPEC = importlib.util.spec_from_file_location("render_harbor_pilot_config", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RENDERER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RENDERER
SPEC.loader.exec_module(RENDERER)


def test_renders_exact_candidate_harness_and_five_task_set(tmp_path: Path) -> None:
    config = RENDERER.render_config(
        protocol_path=EXAMPLE / "harbor-quality-pilot.yaml",
        candidate_name="qwen38_flash_ds4",
        task_set_name="pilot_5",
        tasks_directory=tmp_path / "tasks",
        jobs_directory=tmp_path / "jobs",
        job_name="pilot-ds4",
        api_base="http://127.0.0.1:8090/v1/",
        extra_docker_compose=[tmp_path / "overlay.yaml"],
    )

    assert config["n_concurrent_trials"] == 1
    assert config["agents"] == [
        {
            "name": "terminus-2",
            "model_name": "openai/qwen3.8-flash-next",
            "kwargs": {
                "api_base": "http://127.0.0.1:8090/v1",
                "parser_name": "json",
                "temperature": 1.0,
                "max_turns": 40,
                "enable_summarize": True,
                "proactive_summarization_threshold": 8192,
                "model_info": {
                    "max_input_tokens": 114688,
                    "max_output_tokens": 16384,
                    "input_cost_per_token": 0,
                    "output_cost_per_token": 0,
                },
                "llm_call_kwargs": {"top_p": 0.95},
                "store_all_messages": True,
            },
        }
    ]
    assert config["datasets"][0]["task_names"] == [
        "fix-git",
        "build-cython-ext",
        "multi-source-data-merger",
        "fix-code-vulnerability",
        "cancel-async-tasks",
    ]


def test_renders_the_full_pinned_task_manifest(tmp_path: Path) -> None:
    config = RENDERER.render_config(
        protocol_path=EXAMPLE / "harbor-quality-pilot.yaml",
        candidate_name="ornith15_baseline",
        task_set_name="all_89",
        tasks_directory=tmp_path / "tasks",
        jobs_directory=tmp_path / "jobs",
        job_name="all-tasks",
        api_base="http://127.0.0.1:8090/v1",
        extra_docker_compose=[],
    )

    assert len(config["datasets"][0]["task_names"]) == 89
    assert len(set(config["datasets"][0]["task_names"])) == 89
