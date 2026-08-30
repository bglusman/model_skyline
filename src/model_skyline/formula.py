from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
from typing import Any, cast

from lark import Lark, Token, Tree
from lark.exceptions import LarkError

from model_skyline.canonical import POLICY_DECIMAL_CONTEXT
from model_skyline.models import _bounded_canonical_decimal

GRAMMAR = r"""
?start: comparison
?comparison: sum
           | sum "==" sum  -> eq
           | sum "!=" sum  -> ne
           | sum "<=" sum  -> le
           | sum "<" sum   -> lt
           | sum ">=" sum  -> ge
           | sum ">" sum   -> gt
?sum: sum "+" product      -> add
    | sum "-" product      -> sub
    | product
?product: product "*" power -> mul
        | product "/" power -> div
        | product "%" power -> mod
        | power
?power: unary "^" power     -> pow
      | unary
?unary: "-" unary           -> neg
      | "+" unary           -> pos
      | atom
?atom: NUMBER                -> number
     | path
     | NAME "(" [arguments] ")" -> call
     | "(" comparison ")"
arguments: comparison ("," comparison)*
path: NAME ("." NAME)+

NAME: /[A-Za-z_][A-Za-z0-9_]*/
NUMBER: /(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?/
%import common.WS
%ignore WS
"""


PARSER = Lark(GRAMMAR, parser="lalr", start="start")
ALLOWED_ROOTS = frozenset({"signals", "workload", "metadata"})
ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "ceil",
        "clamp",
        "coalesce",
        "exp",
        "floor",
        "if",
        "log",
        "max",
        "mean",
        "min",
        "round",
        "sqrt",
    }
)

# Formulas are deliberately small configuration expressions, not programs. These
# limits keep a remotely supplied registry from turning parsing or Decimal math
# into an accidental CPU or memory denial of service.
MAX_EXPRESSION_LENGTH = 4096
MAX_TREE_DEPTH = 128
MAX_TREE_NODES = 2048
MAX_NUMERIC_LITERAL_LENGTH = 128
MAX_ABS_LITERAL_EXPONENT = 1000
MAX_ABS_POWER_EXPONENT = 1000
MAX_ABS_ROUND_PLACES = 1000
MAX_ABS_RESULT_ADJUSTED_EXPONENT = 1000
MAX_CANONICAL_RESULT_LENGTH = 4096
MAX_FORMULA_SIGNIFICANT_DIGITS = 38
MAX_ABS_EXP_ARGUMENT = Decimal(2300)


class FormulaError(ValueError):
    """A formula is syntactically invalid or cannot be evaluated."""


class _Missing:
    pass


MISSING = _Missing()


@dataclass(frozen=True, slots=True)
class FormulaResult:
    value: Decimal
    referenced_paths: frozenset[str]


def _path_parts(tree: Tree[Token]) -> tuple[str, ...]:
    return tuple(str(child) for child in tree.children)


def _arguments(tree: Tree[Token]) -> list[Tree[Token] | Token]:
    if len(tree.children) == 1:
        return []
    node = tree.children[1]
    if isinstance(node, Tree) and node.data == "arguments":
        return list(node.children)
    return [node]


def _validate(root: Tree[Token]) -> None:
    nodes = 0
    stack: list[tuple[Tree[Token] | Token, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_TREE_NODES:
            raise FormulaError(f"formula exceeds {MAX_TREE_NODES} syntax nodes")
        if depth > MAX_TREE_DEPTH:
            raise FormulaError(f"formula exceeds maximum nesting depth {MAX_TREE_DEPTH}")
        if isinstance(node, Token):
            if node.type == "NUMBER":
                literal = str(node)
                if len(literal) > MAX_NUMERIC_LITERAL_LENGTH:
                    raise FormulaError(
                        f"numeric literal exceeds {MAX_NUMERIC_LITERAL_LENGTH} characters"
                    )
                value = Decimal(literal)
                exponent = value.as_tuple().exponent
                if isinstance(exponent, int) and abs(exponent) > MAX_ABS_LITERAL_EXPONENT:
                    raise FormulaError(
                        f"numeric literal exponent exceeds {MAX_ABS_LITERAL_EXPONENT} in magnitude"
                    )
            continue
        if node.data == "path":
            parts = _path_parts(node)
            if not parts or parts[0] not in ALLOWED_ROOTS:
                raise FormulaError(
                    f"formula path must begin with one of {', '.join(sorted(ALLOWED_ROOTS))}"
                )
        elif node.data == "call":
            name = str(node.children[0])
            if name not in ALLOWED_FUNCTIONS:
                raise FormulaError(f"formula function {name!r} is not allowed")
        stack.extend(
            (child, depth + 1)
            for child in reversed(node.children)
            if isinstance(child, (Tree, Token))
        )


@lru_cache(maxsize=512)
def compile_formula(expression: str) -> Tree[Token]:
    if not expression.strip():
        raise FormulaError("formula cannot be empty")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise FormulaError(f"formula exceeds {MAX_EXPRESSION_LENGTH} characters")
    try:
        tree = PARSER.parse(expression)
    except (LarkError, RecursionError) as exc:
        raise FormulaError(f"invalid formula: {exc}") from exc
    if isinstance(tree, Token):
        raise FormulaError("formula cannot consist only of a token")
    _validate(tree)
    return tree


def referenced_formula_paths(expression: str) -> frozenset[str]:
    """Return every syntactic path in a validated formula, including lazy branches."""

    root = compile_formula(expression)
    paths: set[str] = set()
    stack: list[Tree[Token] | Token] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Token):
            continue
        if node.data == "path":
            paths.add(".".join(_path_parts(node)))
        stack.extend(reversed(node.children))
    return frozenset(paths)


class _Evaluator:
    def __init__(self, context: Mapping[str, Any]) -> None:
        self.context = context
        self.referenced_paths: set[str] = set()

    def evaluate(self, node: Tree[Token] | Token) -> Decimal | bool | _Missing:
        if isinstance(node, Token):
            raise FormulaError(f"unexpected formula token {node}")
        method = getattr(self, f"_eval_{node.data}", None)
        if method is None:
            if len(node.children) == 1:
                return self.evaluate(node.children[0])
            raise FormulaError(f"unsupported formula operation {node.data}")
        try:
            evaluator = cast(
                "Callable[[Tree[Token]], Decimal | bool | _Missing]",
                method,
            )
            return evaluator(node)
        except FormulaError:
            raise
        except (ArithmeticError, InvalidOperation, TypeError, ValueError) as exc:
            raise FormulaError(f"formula operation {node.data} failed: {exc}") from exc

    def _decimal(self, value: Decimal | bool | _Missing, label: str) -> Decimal:
        if isinstance(value, _Missing):
            raise FormulaError(f"{label} uses a missing value")
        if isinstance(value, bool):
            raise FormulaError(f"{label} requires a number")
        return value

    def _eval_number(self, node: Tree[Token]) -> Decimal:
        return Decimal(str(node.children[0]))

    def _eval_path(self, node: Tree[Token]) -> Decimal | _Missing:
        parts = _path_parts(node)
        text = ".".join(parts)
        self.referenced_paths.add(text)
        current: Any = self.context
        for part in parts:
            if not isinstance(current, Mapping) or part not in current:
                return MISSING
            current = current[part]
        if isinstance(current, bool) or not isinstance(current, (Decimal, int, float, str)):
            raise FormulaError(f"formula path {text!r} is not numeric")
        encoded = str(current)
        if len(encoded) > MAX_NUMERIC_LITERAL_LENGTH:
            raise FormulaError(
                f"formula path {text!r} exceeds {MAX_NUMERIC_LITERAL_LENGTH} characters"
            )
        try:
            result = current if isinstance(current, Decimal) else Decimal(encoded)
        except InvalidOperation as exc:
            raise FormulaError(f"formula path {text!r} is not numeric") from exc
        if not result.is_finite():
            raise FormulaError(f"formula path {text!r} must be finite")
        exponent = result.as_tuple().exponent
        if isinstance(exponent, int) and abs(exponent) > MAX_ABS_LITERAL_EXPONENT:
            raise FormulaError(
                f"formula path {text!r} exponent exceeds {MAX_ABS_LITERAL_EXPONENT} in magnitude"
            )
        return result

    def _binary(self, node: Tree[Token], operation: str) -> Decimal:
        left = self._decimal(self.evaluate(node.children[0]), operation)
        right = self._decimal(self.evaluate(node.children[1]), operation)
        if operation == "add":
            return left + right
        if operation == "sub":
            return left - right
        if operation == "mul":
            return left * right
        if operation == "div":
            return left / right
        if operation == "mod":
            return left % right
        if operation == "pow":
            if right != right.to_integral_value():
                raise FormulaError("power exponent must be an integer")
            if abs(right) > MAX_ABS_POWER_EXPONENT:
                raise FormulaError(f"power exponent exceeds {MAX_ABS_POWER_EXPONENT} in magnitude")
            return left ** int(right)
        raise AssertionError(operation)

    def _eval_add(self, node: Tree[Token]) -> Decimal:
        return self._binary(node, "add")

    def _eval_sub(self, node: Tree[Token]) -> Decimal:
        return self._binary(node, "sub")

    def _eval_mul(self, node: Tree[Token]) -> Decimal:
        return self._binary(node, "mul")

    def _eval_div(self, node: Tree[Token]) -> Decimal:
        return self._binary(node, "div")

    def _eval_mod(self, node: Tree[Token]) -> Decimal:
        return self._binary(node, "mod")

    def _eval_pow(self, node: Tree[Token]) -> Decimal:
        return self._binary(node, "pow")

    def _eval_neg(self, node: Tree[Token]) -> Decimal:
        return -self._decimal(self.evaluate(node.children[0]), "negation")

    def _eval_pos(self, node: Tree[Token]) -> Decimal:
        return self._decimal(self.evaluate(node.children[0]), "unary plus")

    def _compare(self, node: Tree[Token], operation: str) -> bool:
        left = self._decimal(self.evaluate(node.children[0]), operation)
        right = self._decimal(self.evaluate(node.children[1]), operation)
        return {
            "eq": left == right,
            "ne": left != right,
            "lt": left < right,
            "le": left <= right,
            "gt": left > right,
            "ge": left >= right,
        }[operation]

    def _eval_eq(self, node: Tree[Token]) -> bool:
        return self._compare(node, "eq")

    def _eval_ne(self, node: Tree[Token]) -> bool:
        return self._compare(node, "ne")

    def _eval_lt(self, node: Tree[Token]) -> bool:
        return self._compare(node, "lt")

    def _eval_le(self, node: Tree[Token]) -> bool:
        return self._compare(node, "le")

    def _eval_gt(self, node: Tree[Token]) -> bool:
        return self._compare(node, "gt")

    def _eval_ge(self, node: Tree[Token]) -> bool:
        return self._compare(node, "ge")

    def _eval_call(self, node: Tree[Token]) -> Decimal | bool | _Missing:
        name = str(node.children[0])
        arguments = _arguments(node)
        if name == "coalesce":
            if not arguments:
                raise FormulaError("coalesce() requires at least one argument")
            for argument in arguments:
                value = self.evaluate(argument)
                if not isinstance(value, _Missing):
                    return self._decimal(value, "coalesce")
            return MISSING
        if name == "if":
            if len(arguments) != 3:
                raise FormulaError("if() requires condition, true value, and false value")
            condition = self.evaluate(arguments[0])
            if not isinstance(condition, bool):
                raise FormulaError("if() condition must be a comparison")
            return self.evaluate(arguments[1] if condition else arguments[2])

        values = [self._decimal(self.evaluate(argument), name) for argument in arguments]
        if name in {"min", "max", "mean"} and not values:
            raise FormulaError(f"{name}() requires at least one argument")
        if name == "abs" and len(values) == 1:
            return abs(values[0])
        if name == "min":
            return min(values)
        if name == "max":
            return max(values)
        if name == "mean":
            return sum(values, Decimal(0)) / Decimal(len(values))
        if name == "clamp" and len(values) == 3:
            value, lower, upper = values
            if lower > upper:
                raise FormulaError("clamp lower bound cannot exceed upper bound")
            return max(lower, min(value, upper))
        if name == "sqrt" and len(values) == 1:
            return values[0].sqrt()
        if name == "log" and len(values) == 1:
            return values[0].ln()
        if name == "exp" and len(values) == 1:
            if abs(values[0]) > MAX_ABS_EXP_ARGUMENT:
                raise FormulaError(f"exp() argument exceeds {MAX_ABS_EXP_ARGUMENT} in magnitude")
            return values[0].exp()
        if name == "floor" and len(values) == 1:
            return values[0].to_integral_value(rounding="ROUND_FLOOR")
        if name == "ceil" and len(values) == 1:
            return values[0].to_integral_value(rounding="ROUND_CEILING")
        if name == "round" and len(values) in {1, 2}:
            if len(values) == 2 and values[1] != values[1].to_integral_value():
                raise FormulaError("round() places must be an integer")
            places = int(values[1]) if len(values) == 2 else 0
            if abs(places) > MAX_ABS_ROUND_PLACES:
                raise FormulaError(f"round() places exceeds {MAX_ABS_ROUND_PLACES} in magnitude")
            return values[0].quantize(Decimal(1).scaleb(-places))
        raise FormulaError(f"invalid arguments for {name}()")


def evaluate_formula(expression: str, context: Mapping[str, Any]) -> FormulaResult:
    evaluator = _Evaluator(context)
    with localcontext(POLICY_DECIMAL_CONTEXT):
        value = evaluator.evaluate(compile_formula(expression))
    if isinstance(value, _Missing):
        raise FormulaError("formula result is missing")
    if isinstance(value, bool):
        raise FormulaError("formula result must be numeric")
    if not value.is_finite():
        raise FormulaError("formula result must be finite")
    if abs(value.adjusted()) > MAX_ABS_RESULT_ADJUSTED_EXPONENT:
        raise FormulaError(
            f"formula result exponent exceeds {MAX_ABS_RESULT_ADJUSTED_EXPONENT} in magnitude"
        )
    if len(format(value, "f")) > MAX_CANONICAL_RESULT_LENGTH:
        raise FormulaError(
            f"canonical formula result exceeds {MAX_CANONICAL_RESULT_LENGTH} characters"
        )
    significant_digits = list(value.as_tuple().digits)
    while len(significant_digits) > 1 and significant_digits[-1] == 0:
        significant_digits.pop()
    if len(significant_digits) > MAX_FORMULA_SIGNIFICANT_DIGITS:
        raise FormulaError(
            f"formula result exceeds {MAX_FORMULA_SIGNIFICANT_DIGITS} significant digits"
        )
    try:
        canonical_value = _bounded_canonical_decimal(value)
    except ValueError as exc:
        raise FormulaError(f"formula result is outside the public decimal contract: {exc}") from exc
    return FormulaResult(
        value=canonical_value,
        referenced_paths=frozenset(evaluator.referenced_paths),
    )
