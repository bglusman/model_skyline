from __future__ import annotations

from pathlib import Path

from model_skyline.io import load_config
from model_skyline.models import UncertaintyMode

ROOT = Path(__file__).parents[1]
RECIPES = ROOT / "examples" / "local-runtime-frontiers" / "recommended-frontier-recipes.yaml"


def test_recommended_local_frontier_recipes_are_valid_and_uncertainty_aware() -> None:
    config = load_config(RECIPES)

    assert set(config.frontiers) == {
        "fixed-128k-usefulness",
        "interactive-local-value",
        "quantization-screening",
        "remote-agent-value",
        "session-endurance",
        "warm-cache-operation",
    }
    assert config.frontiers["quantization-screening"].uncertainty is UncertaintyMode.POINT
    assert all(
        frontier.uncertainty is UncertaintyMode.ROBUST
        for frontier_id, frontier in config.frontiers.items()
        if frontier_id != "quantization-screening"
    )
