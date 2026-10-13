"""Tools that need no network: exact arithmetic, clocks, units, text stats.

The calculator is the important one. "A 1.5B model gets arithmetic wrong" is
the first thing anyone notices, and it is the cheapest thing to fix properly —
so this evaluates exactly, with Fractions, and says so on the card.
"""

from __future__ import annotations

import ast
import math
import re
from datetime import datetime, timezone
from fractions import Fraction

from .base import Arg, Tool, ToolResult, register

# --------------------------------------------------------------- calculator --

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
            # Guard against 9**9**9 turning into a denial of service.
            if right.denominator != 1 or abs(right) > 512:
                return _num(float(left) ** float(right))
            if abs(left) > 10**9 and abs(right) > 8:
                raise CalcError("exponent too large")
            return left ** int(right)
        fn = _BINOPS.get(op)
        if fn is None:
            raise CalcError("unsupported operator")
        if op in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise CalcError("division by zero")
        return _num(fn(left, right))

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
            "exact": exact,
            "float": float(value),
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


# ------------------------------------------------------------------ clock ---

COMMON_ZONES = ["UTC", "Europe/London", "Europe/Paris", "America/New_York", "Asia/Tokyo"]


# Cities whose IANA zone is not named after them.
CITY_ZONES = {
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "beijing": "Asia/Shanghai", "washington": "America/New_York",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "geneva": "Europe/Zurich", "boston": "America/New_York", "miami": "America/New_York",
    "philadelphia": "America/New_York", "seattle": "America/Los_Angeles",
    "austin": "America/Chicago", "dallas": "America/Chicago", "houston": "America/Chicago",
    "uk": "Europe/London", "england": "Europe/London", "britain": "Europe/London",
    "france": "Europe/Paris", "germany": "Europe/Berlin", "japan": "Asia/Tokyo",
    "india": "Asia/Kolkata", "china": "Asia/Shanghai", "morocco": "Africa/Casablanca",
}


def _resolve_zone(name: str):
    """Accept 'Europe/Paris', but also 'Tokyo', 'new york' and 'Japan'.

    People do not type IANA identifiers, and refusing them would make the tool
    useless for the query that triggers it most often.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

    raw = (name or "UTC").strip()
    for candidate in (raw, raw.replace(" ", "_")):
        try:
            return ZoneInfo(candidate), candidate, None
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass

    target = raw.replace("_", " ").lower()
    if target in CITY_ZONES:
        return ZoneInfo(CITY_ZONES[target]), CITY_ZONES[target], None

    # Match on the last path segment: "tokyo" -> "Asia/Tokyo".
    slug = target.replace(" ", "_")
    try:
        for zone in available_timezones():
            if zone.split("/")[-1].lower() == slug:
                return ZoneInfo(zone), zone, None
    except ZoneInfoNotFoundError:
        pass

    return timezone.utc, "UTC", f"Unknown timezone {raw!r} — showing UTC."


async def _clock(timezone_name: str = "UTC") -> ToolResult:
    try:
        from zoneinfo import ZoneInfo  # noqa: F401 — probe for the stdlib module
    except ImportError:  # pragma: no cover - stdlib since 3.9
        return ToolResult(
            False,
            "This server has no timezone database installed, so only UTC is "
            "available.",
            error="no timezone data",
        )

    tz, name, note = _resolve_zone(timezone_name)

    now = datetime.now(tz)
    others = []
    for zone in COMMON_ZONES:
        try:
            others.append({"zone": zone, "time": datetime.now(ZoneInfo(zone)).strftime("%H:%M")})
        except Exception:  # noqa: BLE001
            continue

    offset = now.utcoffset()
    offset_h = round(offset.total_seconds() / 3600, 2) if offset else 0
    return ToolResult(
        True,
        f"{now.strftime('%A %d %B %Y, %H:%M')} in {name}."
        + (f" ({note})" if note else ""),
        card="clock",
        data={
            "zone": name,
            "iso": now.isoformat(timespec="seconds"),
            "time": now.strftime("%H:%M"),
            "seconds": now.second,
            "date": now.strftime("%A %d %B %Y"),
            "offset_hours": offset_h,
            "others": others,
            "note": note,
        },
    )


register(Tool(
    name="clock",
    label="Clock",
    glyph="◷",
    description="Current date and time, optionally in a named timezone.",
    args=(Arg("timezone_name", "string", False, "IANA zone, e.g. Europe/Paris",
              default="UTC"),),
    run=_clock,
    card="clock",
    example="/clock Asia/Tokyo",
))


# ------------------------------------------------------------------ units ---

# name -> (dimension, factor to base unit)
UNITS: dict[str, tuple[str, float]] = {}


def _add_units(dimension: str, mapping: dict[str, float]) -> None:
    for name, factor in mapping.items():
        UNITS[name] = (dimension, factor)


_add_units("length", {
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344, "nmi": 1852.0,
})
_add_units("mass", {
    "mg": 1e-6, "g": 0.001, "kg": 1.0, "t": 1000.0,
    "oz": 0.028349523125, "lb": 0.45359237, "st": 6.35029318,
})
_add_units("time", {
    "ms": 0.001, "s": 1.0, "min": 60.0, "h": 3600.0, "day": 86400.0, "week": 604800.0,
})
_add_units("data", {
    "bit": 0.125, "byte": 1.0, "kb": 1e3, "kib": 1024.0, "mb": 1e6, "mib": 1048576.0,
    "gb": 1e9, "gib": 1073741824.0, "tb": 1e12, "tib": 1099511627776.0,
})
_add_units("speed", {"mps": 1.0, "kmh": 1 / 3.6, "mph": 0.44704, "kn": 0.5144444444})
_add_units("volume", {
    "ml": 0.001, "l": 1.0, "gal": 3.785411784, "qt": 0.946352946, "pt": 0.473176473,
    "cup": 0.2365882365, "floz": 0.0295735295625,
})

ALIASES = {
    "metre": "m", "meter": "m", "metres": "m", "meters": "m",
    "kilometre": "km", "kilometer": "km", "kilometres": "km", "kilometers": "km",
    "centimetre": "cm", "centimeter": "cm", "millimetre": "mm", "millimeter": "mm",
    "inch": "in", "inches": "in", "foot": "ft", "feet": "ft",
    "yard": "yd", "yards": "yd", "mile": "mi", "miles": "mi",
    "gram": "g", "grams": "g", "kilogram": "kg", "kilograms": "kg", "kilo": "kg",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "ounce": "oz", "ounces": "oz",
    "tonne": "t", "tonnes": "t", "ton": "t", "stone": "st",
    "second": "s", "seconds": "s", "sec": "s", "minute": "min", "minutes": "min",
    "hour": "h", "hours": "h", "hr": "h", "days": "day", "weeks": "week",
    "bytes": "byte", "bits": "bit", "kilobyte": "kb", "megabyte": "mb",
    "gigabyte": "gb", "terabyte": "tb",
    "kph": "kmh", "km/h": "kmh", "m/s": "mps", "mi/h": "mph", "knot": "kn", "knots": "kn",
    "litre": "l", "liter": "l", "litres": "l", "liters": "l",
    "millilitre": "ml", "milliliter": "ml", "gallon": "gal", "gallons": "gal",
    "celsius": "c", "centigrade": "c", "°c": "c", "fahrenheit": "f", "°f": "f",
    "kelvin": "k", "°k": "k",
}

TEMPS = {"c", "f", "k"}


def _canon(unit: str) -> str:
    u = (unit or "").strip().lower().rstrip(".")
    return ALIASES.get(u, u)


def _to_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[unit]


def _from_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": value * 9 / 5 + 32, "k": value + 273.15}[unit]


async def _convert(value: float, from_unit: str, to_unit: str) -> ToolResult:
    src, dst = _canon(from_unit), _canon(to_unit)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ToolResult(False, "That value is not a number.", error="bad value")

    if src in TEMPS or dst in TEMPS:
        if src not in TEMPS or dst not in TEMPS:
            return ToolResult(
            False,
            "Temperatures convert only to other temperatures — C, F or K.",
            error="mismatched units",
        )
        out = _from_celsius(_to_celsius(value, src), dst)
        dimension = "temperature"
    else:
        if src not in UNITS or dst not in UNITS:
            unknown = src if src not in UNITS else dst
            return ToolResult(
                False,
                f"{unknown!r} is not a unit this converter knows. It handles "
                "length, mass, time, data, speed, volume and temperature.",
                error="unknown unit",
            )
        if UNITS[src][0] != UNITS[dst][0]:
            return ToolResult(
                False,
                f"Those measure different things — {UNITS[src][0]} cannot "
                f"convert to {UNITS[dst][0]}.",
                error="mismatched units",
            )
        dimension = UNITS[src][0]
        out = value * UNITS[src][1] / UNITS[dst][1]

    pretty = f"{out:,.6g}"
    return ToolResult(
        True,
        f"{value:,.6g} {src} = {pretty} {dst}",
        card="convert",
        data={
            "value": value, "from": src, "to": dst,
            "result": out, "result_text": pretty, "dimension": dimension,
        },
    )


register(Tool(
    name="convert_units",
    label="Convert",
    glyph="⇄",
    description="Convert a value between units (length, mass, time, data, speed, volume, temperature).",
    args=(
        Arg("value", "number", True, "the quantity"),
        Arg("from_unit", "string", True, "unit to convert from"),
        Arg("to_unit", "string", True, "unit to convert to"),
    ),
    run=_convert,
    card="convert",
    example="/convert 100 km mi",
))


# ------------------------------------------------------------- text stats ---

STOPWORDS = set(
    "the a an and or but if then than that this these those of to in for on at by "
    "with from as is are was were be been being it its it's i you he she they we "
    "not no so such can will just do does did have has had about into over after "
    "your our their his her my me him them us".split()
)


async def _text_stats(text: str) -> ToolResult:
    body = (text or "").strip()
    if not body:
        return ToolResult(False, "There is no text to measure.", error="empty")

    words = re.findall(r"[A-Za-z0-9']+", body)
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", body) if s.strip()]
    counts: dict[str, int] = {}
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or len(lw) < 3:
            continue
        counts[lw] = counts.get(lw, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    minutes = len(words) / 200 if words else 0

    return ToolResult(
        True,
        f"{len(words):,} words, {len(sentences):,} sentences, "
        f"about {max(1, round(minutes))} minute(s) to read.",
        card="textstats",
        data={
            "words": len(words),
            "characters": len(body),
            "sentences": len(sentences),
            "paragraphs": len([p for p in body.split("\n\n") if p.strip()]),
            "reading_minutes": round(minutes, 1),
            "avg_sentence_words": round(len(words) / max(1, len(sentences)), 1),
            "top_terms": [{"term": t, "count": c} for t, c in top],
        },
    )


register(Tool(
    name="text_stats",
    label="Text stats",
    glyph="▤",
    description="Word, sentence and reading-time statistics for a block of text.",
    args=(Arg("text", "string", True, "the text to measure"),),
    run=_text_stats,
    card="textstats",
    example="/stats <paste some text>",
))
