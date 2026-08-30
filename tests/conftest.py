from __future__ import annotations

from pathlib import Path

import pytest

from model_skyline.io import load_catalog, load_config
from model_skyline.models import ObservationCatalog, ProjectConfig

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "coding-session"


@pytest.fixture
def example_config() -> ProjectConfig:
    return load_config(EXAMPLE / "frontier.yaml")


@pytest.fixture
def example_catalog() -> ObservationCatalog:
    return load_catalog(EXAMPLE / "observations.json")
