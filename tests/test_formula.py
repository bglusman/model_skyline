from __future__ import annotations

from decimal import Decimal

import pytest

from model_skyline.formula import FormulaError, compile_formula, evaluate_formula


def test_formula_uses_decimal_and_reports_dependencies() -> None:
    result = evaluate_formula(
        "(signals.read * signals.read_rate + signals.write * signals.write_rate) / 1000000",
        {
            "signals": {
                "read": Decimal("1500000"),
                "read_rate": Decimal("0.10"),
                "write": Decimal("200000"),
                "write_rate": Decimal("1.25"),
            },
            "workload": {},
            "metadata": {},
        },
    )

    assert result.value == Decimal("0.40")
    assert result.referenced_paths == {
        "signals.read",
        "signals.read_rate",
        "signals.write",
        "signals.write_rate",
    }


def test_coalesce_and_if_are_lazy() -> None:
    result = evaluate_formula(
        "if(signals.hits > 0, coalesce(signals.discount, 0.5), signals.missing)",
        {
            "signals": {"hits": Decimal(1)},
            "workload": {},
            "metadata": {},
        },
    )

    assert result.value == Decimal("0.5")
    assert "signals.missing" not in result.referenced_paths


@pytest.mark.parametrize(
    "expression",
    [
        "__import__(1)",
        "os.system(1)",
        "unknown.value + 1",
        "[1, 2, 3]",
    ],
)
def test_formula_language_rejects_python_and_unknown_names(expression: str) -> None:
    with pytest.raises(FormulaError):
        compile_formula(expression)


def test_missing_value_outside_coalesce_is_an_error() -> None:
    with pytest.raises(FormulaError, match="missing"):
        evaluate_formula(
            "signals.absent + 1",
            {"signals": {}, "workload": {}, "metadata": {}},
        )


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1" * 129, "numeric literal"),
        ("1e1001", "literal exponent"),
        ("2 ^ 1001", "power exponent"),
        ("round(1.5, 1001)", "round"),
        ("round(1.5, 1.5)", "integer"),
        ("exp(2301)", "exp"),
        ("123456789012345678901234567890123456789", "significant digits"),
    ],
)
def test_formula_complexity_and_decimal_operations_are_bounded(
    expression: str, message: str
) -> None:
    with pytest.raises(FormulaError, match=message):
        evaluate_formula(
            expression,
            {"signals": {}, "workload": {}, "metadata": {}},
        )


def test_formula_length_is_bounded_before_parsing() -> None:
    with pytest.raises(FormulaError, match="4096 characters"):
        compile_formula("1+" * 2049)


def test_clamp_rejects_inverted_bounds() -> None:
    with pytest.raises(FormulaError, match="lower bound"):
        evaluate_formula(
            "clamp(5, 10, 0)",
            {"signals": {}, "workload": {}, "metadata": {}},
        )


def test_non_finite_context_value_is_rejected() -> None:
    with pytest.raises(FormulaError, match="finite"):
        evaluate_formula(
            "metadata.score",
            {"signals": {}, "workload": {}, "metadata": {"score": Decimal("NaN")}},
        )
