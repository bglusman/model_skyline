from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from model_skyline.models import (
    MAX_DECIMAL_INPUT_LENGTH,
    MAX_DECIMAL_SIGNIFICANT_DIGITS,
    MAX_SAFE_INTEGER,
    MAX_SELECTION_CANDIDATES,
    MAX_SNAPSHOT_TTL_SECONDS,
    AxisEstimate,
    FormulaMetric,
    Observation,
    ObservationRequirements,
    OfferingKey,
    PublishedFile,
    SelectionDefinition,
    SourceReference,
    WorkloadProfile,
)


def test_canonical_decimal_is_normalized_before_serialization() -> None:
    observation = Observation(value="-0.0000", unit="ratio")
    priced = Observation(value="1.2300", unit="USD")

    assert observation.value == Decimal(0)
    assert observation.model_dump(mode="json")["value"] == "0"
    assert priced.value == Decimal("1.23")
    assert priced.model_dump(mode="json")["value"] == "1.23"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("1e1025", "adjusted exponent"),
        ("1e-1025", "decimal places"),
        (
            Decimal("1" * (MAX_DECIMAL_SIGNIFICANT_DIGITS + 1)),
            "significant digits",
        ),
        ("1" * (MAX_DECIMAL_INPUT_LENGTH + 1), "input exceeds"),
        (True, "cannot be booleans"),
    ],
)
def test_canonical_decimal_rejects_unsafe_public_inputs(
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Observation(value=value, unit="ratio")


def test_canonical_decimal_accepts_normal_json_and_yaml_numbers() -> None:
    assert Observation(value=0.125, unit="ratio").value == Decimal("0.125")
    assert Observation(value=1_000_000, unit="tokens").value == Decimal("1E+6")
    assert Observation(value="1e-1000", unit="ratio").value == Decimal("1e-1000")


def test_usd_formula_requires_an_explicit_cost_basis() -> None:
    with pytest.raises(ValidationError, match="must declare one cost_basis"):
        FormulaMetric(
            kind="formula",
            unit="USD/work_unit",
            expression="signals.other_cost_usd_per_work_unit",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("count", MAX_SELECTION_CANDIDATES + 1, "less than or equal to 10000"),
        ("max_per_provider", MAX_SELECTION_CANDIDATES + 1, "less than or equal to 10000"),
        (
            "snapshot_ttl_seconds",
            MAX_SNAPSHOT_TTL_SECONDS + 1,
            "less than or equal to 31536000",
        ),
    ],
)
def test_selection_operational_integers_are_bounded(
    field: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        SelectionDefinition(frontier="coding", **{field: value})


def test_sample_count_integers_stay_in_the_interoperable_json_domain() -> None:
    with pytest.raises(ValidationError, match="less than or equal"):
        Observation(value="1", unit="ratio", sample_count=MAX_SAFE_INTEGER + 1)
    with pytest.raises(ValidationError, match="less than or equal"):
        ObservationRequirements(minimum_samples=MAX_SAFE_INTEGER + 1)


@pytest.mark.parametrize("unsafe_count", [True, "3"])
def test_operational_integers_do_not_coerce_non_integer_inputs(unsafe_count: object) -> None:
    with pytest.raises(ValidationError, match="valid integer"):
        SelectionDefinition.model_validate({"frontier": "coding", "count": unsafe_count})


@pytest.mark.parametrize("field", ["url", "terms_url"])
@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://user:password@example.test/source", "user information"),
        ("https://example.test/source?api_key=secret", "query string"),
        ("https://example.test/source#private", "fragment"),
    ],
)
def test_public_source_urls_reject_secret_bearing_components(
    field: str,
    url: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        SourceReference(id="public-source", **{field: url})


def test_public_source_url_accepts_an_uncredentialed_path() -> None:
    source = SourceReference(
        id="public-source",
        url="https://example.test/public/source.json",
    )

    assert str(source.url) == "https://example.test/public/source.json"


def test_axis_estimate_rejects_one_source_id_with_multiple_descriptors() -> None:
    first = SourceReference(id="benchmark", version="1")
    second = SourceReference(id="benchmark", version="2")

    with pytest.raises(ValidationError, match="multiple descriptors"):
        AxisEstimate(
            value="1",
            unit="ratio",
            source_ids=("benchmark",),
            sources=(first, second),
        )


def test_workload_rejects_one_source_id_with_multiple_descriptors() -> None:
    with pytest.raises(ValidationError, match="multiple descriptors"):
        WorkloadProfile(
            unit="task",
            version="1",
            harness="harness@1",
            cohort="cohort@1",
            sources=[
                SourceReference(id="workload-study", version="1"),
                SourceReference(id="workload-study", version="2"),
            ],
        )


def _offering(capabilities: object) -> OfferingKey:
    return OfferingKey(
        offering_id="provider/model@region-tier",
        model_id="model",
        provider="provider",
        capabilities=capabilities,
    )


def test_capabilities_are_strict_unique_identifiers_with_stable_order() -> None:
    offering = _offering(["tools", "image-input", "mcp:resources"])

    assert offering.capabilities == ("image-input", "mcp:resources", "tools")


@pytest.mark.parametrize(
    ("capabilities", "message"),
    [
        ("tools", "array of strings"),
        (None, "array of strings"),
        ([1], "only strings"),
        (["tools", "tools"], "duplicates"),
        ([""], "at least 1 character"),
        ([" tools"], "match pattern"),
        (["Tools"], "match pattern"),
        (["tools!"], "match pattern"),
    ],
)
def test_capabilities_reject_ambiguous_or_noncanonical_values(
    capabilities: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _offering(capabilities)


@pytest.mark.parametrize(
    "path",
    [
        "a/../b.json",
        "a/./b.json",
        "a//b.json",
        "/absolute.json",
        r"a\b.json",
        ".hidden.json",
    ],
)
def test_published_file_paths_cannot_escape_an_artifact_root(path: str) -> None:
    with pytest.raises(ValidationError):
        PublishedFile(
            path=path,
            sha256="0" * 64,
            media_type="application/json",
        )
