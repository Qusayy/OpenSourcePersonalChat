"""convert_units: length, mass, time, data, speed, volume and temperature.
Needs no network.
"""

from __future__ import annotations

from .base import Arg, Tool, ToolResult, register


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


def canon_unit(unit: str) -> str:
    u = (unit or "").strip().lower().rstrip(".")
    return ALIASES.get(u, u)


# Every way a unit may be written: its own name, or a word for it. A model
# choosing this tool can write only one of these, and each means one unit.
UNIT_SPELLINGS = tuple(sorted({*UNITS, *TEMPS, *ALIASES}))


def _to_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[unit]


def _from_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": value * 9 / 5 + 32, "k": value + 273.15}[unit]


async def _convert(value: float, from_unit: str, to_unit: str) -> ToolResult:
    src, dst = canon_unit(from_unit), canon_unit(to_unit)
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
        Arg("from_unit", "string", True, "unit to convert from",
            choices=UNIT_SPELLINGS, canon=canon_unit),
        Arg("to_unit", "string", True, "unit to convert to",
            choices=UNIT_SPELLINGS, canon=canon_unit),
    ),
    run=_convert,
    card="convert",
    example="/convert 100 km mi",
))
