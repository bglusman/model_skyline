from __future__ import annotations

import model_skyline


def test_root_api_is_the_small_core_calculation_surface() -> None:
    assert model_skyline.__all__ == [
        "__version__",
        "FrontierEngine",
        "FrontierSnapshot",
        "Observation",
        "ObservationCatalog",
        "OfferingKey",
        "OfferingObservation",
        "ProjectConfig",
        "SelectionSnapshot",
        "select_models",
    ]
    assert not hasattr(model_skyline, "DynamicResolver")
    assert not hasattr(model_skyline, "PortfolioPolicy")


def test_advanced_apis_remain_available_from_explicit_modules() -> None:
    from model_skyline.quality_portfolio import PortfolioPolicy
    from model_skyline.resolver import DynamicResolver

    assert PortfolioPolicy.__module__ == "model_skyline.quality_portfolio"
    assert DynamicResolver.__module__ == "model_skyline.resolver"
