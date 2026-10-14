"""Checking an answer against what was looked up for it.

The Agent is told never to give a figure no tool returned, and a small model
does it anyway: a temperature converted when it was told not to, a rate
rounded the wrong way, a year from memory beside a card that shows another.
Nothing here asks the model whether it did. It reads the numbers out of the
answer and out of what was looked up, and says which of the answer's numbers
are in neither what was looked up nor what the user wrote.

A number is the same number when it is what the other comes to, rounded or
cut short to the precision it was written with: "about 62 miles" agrees with
62.1371 and "63 miles" does not; "60" agrees with 62.1 and "110,000" with
110,983, since a trailing zero is how a rounded figure is written. A number
with a unit only agrees with one in the same unit, or with one that has none,
so that 18 degrees is not taken as agreeing with 18 km/h.

Some numbers are not figures, and are let through: a count up to ten with no
unit ("two sentences", "3 reasons"), an ordinal, and the number of an item in
a list. Years are figures and are checked.

The answer is also read for the other failure the product most wants to avoid:
saying it has no live data beside a card that shows some.

Everything here is a pure function of its arguments.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

# A unit a number can carry, by the spellings it is written in.
_UNIT_WORDS = {
    "°c": "°C", "℃": "°C", "celsius": "°C", "°f": "°F", "℉": "°F", "fahrenheit": "°F",
    "°": "°", "degree": "°", "degrees": "°", "deg": "°",
    "%": "%", "percent": "%", "per cent": "%",
    "km/h": "km/h", "kph": "km/h", "mph": "mph", "m/s": "m/s",
    "km": "km", "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km",
    "mi": "mi", "mile": "mi", "miles": "mi",
    "m": "m", "metre": "m", "metres": "m", "meter": "m", "meters": "m",
    "cm": "cm", "mm": "mm", "ft": "ft", "feet": "ft", "foot": "ft",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "g": "g", "gram": "g", "grams": "g",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "oz": "oz",
    "l": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l", "ml": "ml",
    "k": "K", "kelvin": "K",
}
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_STUCK_SCALE = {"k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9}

_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)(?:\s*([ap])(?:\.m\.|m\b))?", re.I)
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NUMBER = re.compile(
    r"(?P<symbol>[€$£¥])?\s?"
    r"(?<![\w.])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)"
    r"(?P<exp>[eE][+-]?\d+)?"
    r"(?:(?P<stuck>bn|[kKmMbB])(?![a-zA-Z]))?"
    r"(?:(?P<ordinal>st|nd|rd|th)(?![a-zA-Z]))?"
)
# A currency's code after an amount, as the tools write it: "92.34 EUR".
_CODE_AFTER = re.compile(r"\s?([A-Z]{3})(?![A-Za-z])")
_UNIT_AFTER = re.compile(r"\s?(°\s?[CcFf](?![a-z])|[℃℉°%]|per cent|km/h|kph|mph|m/s|[A-Za-z]+)")
_LIST_MARKER = re.compile(r"^(\s*(?:[-*•]\s*)?(?:step\s+)?)\d+[.):]\s", re.I | re.M)


@dataclass(frozen=True)
class Figure:
    """A number as it was written."""

    raw: str
    # What it is worth, with any sign left off: "−5" and "5" are one figure
    # here, and a range like 1815–1852 is two positive ones.
    value: float
    # One unit of its last significant digit: how close a number must be to
    # count as the same one.
    precision: float
    unit: str | None = None
    # A time, in minutes since midnight, is only ever the same as a time. One
    # written without am or pm may be either.
    time: bool = False
    either_half: bool = False
    # Read with a scale that might not have been one ("10k" may be ten
    # kelvin): the value without it also counts.
    plain: float | None = None


def _precision(digits: str, exponent: int = 0) -> float:
    """One unit of the last significant digit of a number written as
    ``digits`` (no separators), times ten to ``exponent``."""
    if "." in digits:
        decimals = len(digits.split(".", 1)[1])
        return 10.0 ** (exponent - decimals)
    trailing = len(digits) - len(digits.rstrip("0"))
    if trailing == len(digits):  # zero itself
        return 10.0 ** exponent
    return 10.0 ** (exponent + trailing)


def _unit_after(text: str, end: int) -> tuple[str | None, int]:
    """The unit written straight after a number, if it is one, and where it
    ends."""
    code = _CODE_AFTER.match(text, end)
    if code:
        return code.group(1), code.end()
    match = _UNIT_AFTER.match(text, end)
    if not match:
        return None, end
    word = match.group(1).lower()
    if word.startswith("°") and len(word) > 1:
        word = "°" + word[-1]
    unit = _UNIT_WORDS.get(word)
    return unit, (match.end() if unit else end)


def figures(text: str) -> list[Figure]:
    """Every number in ``text`` that could be a figure, with its unit."""
    out: list[Figure] = []
    taken: list[tuple[int, int]] = []

    for match in _TIME.finditer(text):
        hours, minutes, half = int(match.group(1)), int(match.group(2)), match.group(3)
        if minutes > 59 or hours > 24:
            continue
        if half:
            hours = hours % 12 + (12 if half.lower() == "p" else 0)
        out.append(Figure(
            match.group(0), float(hours * 60 + minutes), 1.0, time=True,
            either_half=not half and 1 <= hours <= 12,
        ))
        taken.append(match.span())
    for match in _ISO_DATE.finditer(text):
        for part in match.groups():
            out.append(Figure(part, float(int(part)), 1.0))
        taken.append(match.span())
    text = _LIST_MARKER.sub(lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1))), text)

    for match in _NUMBER.finditer(text):
        start, end = match.span("num")
        if any(a <= start < b for a, b in taken):
            continue
        if match.group("ordinal"):
            continue
        digits = match.group("num").replace(",", "")
        exponent = int(match.group("exp")[1:]) if match.group("exp") else 0
        value = float(digits) * 10.0 ** exponent
        precision = _precision(digits, exponent)
        unit = _SYMBOLS.get(match.group("symbol") or "")
        plain = None
        stop = match.end()
        stuck = (match.group("stuck") or "").lower()
        if stuck in _STUCK_SCALE:
            # "10k" is ten thousand, or ten kelvin; "5m" five million, or five
            # metres. Either is taken.
            plain, scale = value, _STUCK_SCALE[stuck]
            value, precision = value * scale, precision * scale
        else:
            gap = len(text[stop:stop + 12]) - len(text[stop:stop + 12].lstrip())
            after = text[stop + gap:stop + gap + 12].lower()
            word = next((w for w in _SCALE if after.startswith(w)), None)
            if word:
                plain, value, precision = value, value * _SCALE[word], precision * _SCALE[word]
                stop += gap + len(word)
            elif unit is None:
                unit, stop = _unit_after(text, stop)
        raw = text[match.start():stop].strip()
        out.append(Figure(raw, abs(value), precision, unit, plain=plain))
    return out


def _exempt(figure: Figure) -> bool:
    """Numbers that are not figures: a small count with no unit."""
    return (
        not figure.time and figure.unit is None and figure.plain is None
        and figure.value <= 10 and figure.value == int(figure.value)
        and "." not in figure.raw
    )


def _units_agree(a: str | None, b: str | None) -> bool:
    if a is None or b is None or a == b:
        return True
    return {a, b} <= {"°", "°C", "°F"} and "°" in (a, b)


def supported(figure: Figure, sources: list[Figure]) -> bool:
    """Whether ``figure`` agrees with any of ``sources``, to the precision it
    was written with and in a unit that does not say otherwise."""
    values = [v for v in (figure.value, figure.plain) if v is not None]
    if figure.either_half:
        values.append(figure.value + 720)
    for source in sources:
        if source.time != figure.time or not _units_agree(figure.unit, source.unit):
            continue
        # What the source comes to at the figure's precision, rounded half
        # up or cut short.
        steps = source.value / figure.precision
        near = {math.floor(steps + 0.5), math.floor(steps + 1e-9)}
        for value in values:
            mine = value / figure.precision
            if any(abs(mine - n) < 1e-6 * max(1.0, abs(n)) for n in near):
                return True
    return False


# Saying there is no live data to be had. Read only where a lookup worked.
_DENIALS = (
    re.compile(
        r"(?:\bno|\bnot have|n't have|\bwithout|\black)\s+(?:any\s+)?(?:access\s+to\s+)?"
        r"(?:real[- ]?time|live|current|up[- ]to[- ]date)\s+"
        r"(?:data|information|access|updates|weather|figures|rates|internet)",
        re.I,
    ),
    re.compile(
        r"\b(?:can't|cannot|can not|unable to|not able to)\s+"
        r"(?:browse|access\s+(?:the\s+)?(?:internet|web|real[- ]?time|live|current)|"
        r"check\s+(?:current|live|real[- ]?time|today's)|look\s+(?:that|this|it)\s+up)",
        re.I,
    ),
)


def denies_access(answer: str) -> bool:
    """Whether the answer says it has no way to get live or current data."""
    return any(pattern.search(answer) for pattern in _DENIALS)


@dataclass(frozen=True)
class Check:
    """What was found wrong with one answer."""

    unsupported: tuple[str, ...] = ()
    denied: bool = False

    @property
    def clean(self) -> bool:
        return not self.unsupported and not self.denied


def check(
    answer: str, sources: Iterable[str], *, with_units_only: bool = False, denial: bool = True
) -> Check:
    """The figures in ``answer`` that are in none of ``sources``, and whether
    it denies having live data.

    ``with_units_only`` checks only figures that carry a unit or a currency:
    for an answer that looked nothing up itself but was written beside
    results from earlier in the conversation. ``denial`` is off where no
    lookup worked, since saying so is then true.
    """
    known = [f for text in sources for f in figures(text)]
    missing: list[str] = []
    for figure in figures(answer):
        if _exempt(figure) or (with_units_only and figure.unit is None):
            continue
        if not supported(figure, known) and figure.raw not in missing:
            missing.append(figure.raw)
    return Check(tuple(missing), denial and denies_access(answer))


def _listed(raws: tuple[str, ...]) -> str:
    return raws[0] if len(raws) == 1 else ", ".join(raws[:-1]) + " and " + raws[-1]


def caption(found: Check, quoted: Iterable[tuple[str, str]] = ()) -> str | None:
    """The line the visitor reads under the answer: what was wrong, and what
    the tools whose output can be quoted returned. ``quoted`` is ``(label,
    summary)`` for each of them."""
    if found.clean:
        return None
    said = []
    if found.unsupported:
        said.append(f"Not in what was looked up: {_listed(found.unsupported)}.")
    if found.denied and not found.unsupported:
        said.append("This was looked up just now, and it worked.")
    said += [f"{label} returned: {summary.rstrip('.')}." for label, summary in quoted]
    return " ".join(said)


def note(found: Check) -> str | None:
    """The same, as the model reads it in later turns: fixed words in the
    harness's own voice, with nothing quoted from any tool."""
    if found.clean:
        return None
    said = []
    if found.unsupported:
        verb = "is" if len(found.unsupported) == 1 else "are"
        said.append(f"{_listed(found.unsupported)} in the answer above {verb} not in what was looked up.")
    if found.denied:
        said.append("The answer above says the data could not be had, but the lookup worked.")
    return "Checked by the harness: " + " ".join(said)


def scalars(data: object) -> list[str]:
    """Every number and string in a tool's data, as text to read figures
    from: what the visitor can see on its card."""
    out: list[str] = []
    if isinstance(data, bool) or data is None:
        return out
    if isinstance(data, (int, float)):
        return [repr(data)]
    if isinstance(data, str):
        return [data]
    if isinstance(data, dict):
        for value in data.values():
            out += scalars(value)
    elif isinstance(data, (list, tuple)):
        for value in data:
            out += scalars(value)
    return out
