"""calculator: exact arithmetic.

"A 1.5B model gets arithmetic wrong" is the first thing anyone notices, and
it is the cheapest thing to fix properly — so this evaluates exactly, with
Fractions, and says so on the card.
"""

from __future__ import annotations

import ast
import math
from fractions import Fraction

from .base import Arg, Tool, ToolResult, register


MAX_EXPR = 240

# Functions reachable from an expression. Anything not in here is a hard error,
# which is what keeps `__import__`, attribute access and dunder tricks out.
FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor,
    "ceil": math.ceil, "log": math.log, "log2": math.log2, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "min": min, "max": max, "pow": pow, "factorial": math.factorial,
}
CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}


# The most bits an exact value may take at any step: about 3,900 digits,
# under the 4,300 Python will write out, and small enough that working one
# out never holds the server up.
MAX_BITS = 13_000


def _bits(value: Fraction) -> int:
    """The size of an exact value: its numerator's bits and its denominator's."""
    return value.numerator.bit_length() + value.denominator.bit_length()


def _sized(value):
    """A value, once it is known to be within ``MAX_BITS``."""
    if isinstance(value, Fraction) and _bits(value) > MAX_BITS:
        raise CalcError("the result would be too large")
    return value


class CalcError(ValueError):
    pass


def _num(value):
    """Keep everything exact for as long as possible."""
    if isinstance(value, bool):
        raise CalcError("booleans are not numbers")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(value).limit_denominator(10**12)
    if isinstance(value, Fraction):
        return value
    raise CalcError("unsupported value")


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return _num(node.value)
        raise CalcError("only numbers are allowed")

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_eval(node.operand)
        if isinstance(node.op, ast.UAdd):
            return _eval(node.operand)
        raise CalcError("unsupported unary operator")

    if isinstance(node, ast.BinOp):
        op = type(node.op)
        left, right = _eval(node.left), _eval(node.right)
        if op is ast.Pow:
            # Guard against 9**9**9 turning into a denial of service: an
            # exact power is worked out only where its size is known to fit.
            if right.denominator != 1 or abs(right) > 512:
                try:
                    return _num(float(left) ** float(right))
                except OverflowError:
                    raise CalcError("the result would be too large") from None
            if _bits(left) * abs(int(right)) > MAX_BITS:
                raise CalcError("the result would be too large")
            return _sized(left ** int(right))
        fn = _BINOPS.get(op)
        if fn is None:
            raise CalcError("unsupported operator")
        if op in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise CalcError("division by zero")
        if op is ast.Mult and _bits(left) + _bits(right) > MAX_BITS:
            raise CalcError("the result would be too large")
        return _sized(_num(fn(left, right)))

    if isinstance(node, ast.Name):
        if node.id in CONSTS:
            return _num(CONSTS[node.id])
        raise CalcError(f"unknown name: {node.id}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise CalcError("unknown function")
        if node.keywords:
            raise CalcError("keyword arguments are not supported")
        fn = FUNCS[node.func.id]
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial":
            n = args[0]
            if n.denominator != 1 or n < 0 or n > 1000:
                raise CalcError("factorial needs a small non-negative integer")
            return _num(math.factorial(int(n)))
        if node.func.id in ("abs", "min", "max", "round"):
            return _num(fn(*[a if a.denominator != 1 else int(a) for a in args]))
        return _num(fn(*[float(a) for a in args]))

    raise CalcError("expression not allowed")


def _terminates(frac: Fraction) -> bool:
    """True when the fraction has an exact finite decimal form."""
    d = frac.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    return d == 1


def _format(frac: Fraction) -> tuple[str, bool]:
    if frac.denominator == 1:
        return f"{int(frac):,}", True
    if _terminates(frac) and abs(frac) < Fraction(10**15):
        # Exact: expand the decimal rather than rounding a float.
        digits = 0
        d = frac.denominator
        while d % 2 == 0:
            d //= 2
            digits += 1
        d = frac.denominator
        five = 0
        while d % 5 == 0:
            d //= 5
            five += 1
        places = max(digits, five)
        scaled = frac * (10 ** places)
        text = f"{int(scaled):,}"
        neg = text.startswith("-")
        raw = text.replace(",", "").lstrip("-").rjust(places + 1, "0")
        whole, frac_part = raw[:-places], raw[-places:]
        whole = f"{int(whole):,}"
        out = f"{'-' if neg else ''}{whole}.{frac_part.rstrip('0') or '0'}"
        return out, True
    value = float(frac)
    return f"{value:,.10g}", False


def _float(value: Fraction) -> float | None:
    """A value as a float, or None for one too large to be one."""
    try:
        return float(value)
    except OverflowError:
        return None


# The most intermediate values kept with a result.
MAX_WORKING = 20


def _working(tree: ast.Expression) -> list[str]:
    """The value of each part of the expression that is itself a calculation,
    innermost first: the figures a person writes down on the way to the
    answer, so that an answer explaining the working is not taken for one
    that invented them."""
    parts = [
        node for node in ast.walk(tree.body)
        if node is not tree.body and isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Call))
    ]
    out: list[str] = []
    for node in reversed(parts):
        try:
            text, _ = _format(_eval(node))
        except Exception:  # noqa: BLE001 - a part that cannot be worked out alone is left out
            continue
        if text not in out:
            out.append(text)
    return out[:MAX_WORKING]


async def _calculator(expression: str) -> ToolResult:
    expr = (expression or "").strip().rstrip("=").strip()
    if not expr:
        return ToolResult(False, "There is no expression to calculate.", error="empty")
    if len(expr) > MAX_EXPR:
        return ToolResult(
            False,
            f"That expression is longer than {MAX_EXPR} characters. Try it in "
            "smaller parts.",
            error="too long",
        )

    # Accept the way people actually type maths.
    normalised = (
        expr.replace("×", "*").replace("·", "*").replace("÷", "/")
        .replace("^", "**").replace(",", "").replace("%", "/100")
    )
    try:
        tree = ast.parse(normalised, mode="eval")
        value = _eval(tree)
    except CalcError as exc:
        return ToolResult(
            False,
            "That is not something this calculator handles. It does numbers, "
            "the usual operators, and functions like sqrt, round, log and "
            "factorial.",
            error=str(exc),
        )
    except SyntaxError:
        return ToolResult(
            False,
            "That expression is incomplete — check for a missing bracket or "
            "operator.",
            error="syntax",
        )

    text, exact = _format(value)
    return ToolResult(
        True,
        f"{expr} = {text}",
        card="calculator",
        data={
            "expression": expr,
            "result": text,
            "working": _working(tree),
            "exact": exact,
            "float": _float(value),
            "fraction": f"{value.numerator}/{value.denominator}"
            if value.denominator != 1
            else None,
        },
    )


register(Tool(
    name="calculator",
    label="Calculator",
    glyph="⚡",
    description="Evaluate an arithmetic expression exactly. Use for any sum.",
    args=(Arg("expression", "string", True, "e.g. 1247*89 or sqrt(2)+1"),),
    run=_calculator,
    card="calculator",
    example="/calc 1247 * 89",
))
