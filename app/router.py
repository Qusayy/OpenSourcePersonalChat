"""Deciding which tool — if any — a message needs.

Ordered cheapest-first, because a routing pass on 2 vCPU costs seconds:

1. **Slash command** — the user said exactly what they wanted. Free, certain.
2. **Heuristics** — a pasted URL, an arithmetic expression, "convert 100 km to
   miles", "weather in Paris". Regex, free, and far more reliable than a 1.5B
   model's judgement.
3. **Grammar pass** — only when the persona enables tools and nothing above
   fired. Constrained by GBNF so the output cannot be malformed.

Anything the heuristics catch is a model call that never happens, which on this
hardware is the difference between an instant answer and a five-second wait.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tools import REGISTRY, tools_to_gbnf
from .tools.base import available, tool_menu
from .tools.offline import TEMPS, UNITS, _canon

CURRENCIES = {
    "usd", "eur", "gbp", "jpy", "chf", "cad", "aud", "nzd", "cny", "sek", "nok",
    "dkk", "pln", "czk", "huf", "try", "inr", "brl", "zar", "mxn", "sgd", "hkd",
    "krw", "rub", "ils", "thb", "php", "idr", "myr", "ron", "bgn", "isk",
}

SLASH = {
    "calc": "calculator", "calculate": "calculator", "math": "calculator",
    "wiki": "wikipedia", "wikipedia": "wikipedia",
    "weather": "weather", "forecast": "weather",
    "read": "read_url", "url": "read_url", "fetch": "read_url",
    "convert": "convert_units", "units": "convert_units",
    "currency": "currency", "fx": "currency", "money": "currency",
    "clock": "clock", "time": "clock", "date": "clock",
    "stats": "text_stats", "textstats": "text_stats",
}

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
MATH_RE = re.compile(r"^[\s\d()+\-*/^%.,×÷]+$")
NUMBER = r"(-?\d[\d,]*\.?\d*)"


@dataclass
class ToolCall:
    name: str
    args: dict
    via: str = "heuristic"  # slash | heuristic | model
    ms: float = 0.0
    raw: str = ""
    note: str | None = None
    meta: dict = field(default_factory=dict)


# ------------------------------------------------------------ slash parse ---


def parse_slash(text: str) -> tuple[ToolCall | None, str]:
    """Return (call, remaining_text). `/calc 2+2` needs no model at all."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, text

    head, _, rest = stripped[1:].partition(" ")
    tool_name = SLASH.get(head.lower())
    if tool_name is None or tool_name not in REGISTRY:
        return None, text

    rest = rest.strip()
    args = _args_from_free_text(tool_name, rest)
    if args is None:
        return None, rest or text
    return ToolCall(tool_name, args, via="slash"), rest or text


def _args_from_free_text(tool_name: str, rest: str) -> dict | None:
    """Map the tail of a slash command onto a tool's arguments."""
    if tool_name == "calculator":
        return {"expression": rest} if rest else None
    if tool_name == "wikipedia":
        return {"query": rest} if rest else None
    if tool_name == "weather":
        return {"location": rest} if rest else None
    if tool_name == "read_url":
        match = URL_RE.search(rest)
        return {"url": match.group(0) if match else rest} if rest else None
    if tool_name == "clock":
        return {"timezone_name": rest or "UTC"}
    if tool_name == "text_stats":
        return {"text": rest} if rest else None
    if tool_name == "convert_units":
        m = re.match(rf"{NUMBER}\s*([^\s]+)\s*(?:to|in|into|->)?\s*([^\s]+)", rest, re.I)
        if not m:
            return None
        return {
            "value": float(m.group(1).replace(",", "")),
            "from_unit": m.group(2),
            "to_unit": m.group(3),
        }
    if tool_name == "currency":
        m = re.match(rf"{NUMBER}\s*([A-Za-z]{{3}})\s*(?:to|in|into|->)?\s*([A-Za-z]{{3}})",
                     rest, re.I)
        if not m:
            return None
        return {
            "amount": float(m.group(1).replace(",", "")),
            "from_currency": m.group(2),
            "to_currency": m.group(3),
        }
    return None


# ------------------------------------------------------------- heuristics ---

_STOP_WIKI = re.compile(
    r"\b(best|how|why|should|could|would|difference|better|worse|opinion|think)\b", re.I
)


def heuristic(text: str) -> ToolCall | None:
    body = text.strip()
    if not body:
        return None
    low = body.lower()

    # 1. A link is unambiguous.
    url = URL_RE.search(body)
    if url:
        return ToolCall("read_url", {"url": url.group(0)}, note="found a link")

    # 2. Bare arithmetic, or an explicit request to compute something.
    expr = None
    # "15% of 200" is arithmetic that does not look like arithmetic.
    pct = re.search(rf"{NUMBER}\s*%\s+of\s+{NUMBER}", body, re.I)
    if pct:
        expr = f"{pct.group(1).replace(',', '')}/100*{pct.group(2).replace(',', '')}"
    elif MATH_RE.match(body) and re.search(r"\d", body) and re.search(r"[+\-*/^%×÷]", body):
        expr = body
    else:
        m = re.match(
            r"^(?:what(?:'s| is)|calculate|compute|work out|how much is)\s+(.+?)\s*[=?]?$",
            body, re.I,
        )
        if m and MATH_RE.match(m.group(1)) and re.search(r"[+\-*/^%×÷]", m.group(1)):
            expr = m.group(1)
    if expr:
        return ToolCall("calculator", {"expression": expr.strip()}, note="looks like arithmetic")

    # 3. Currency before units — "100 EUR to USD" also matches the unit shape.
    m = re.search(rf"{NUMBER}\s*([A-Za-z]{{3}})\s+(?:to|in|into)\s+([A-Za-z]{{3}})\b", body, re.I)
    if m and (m.group(2).lower() in CURRENCIES or m.group(3).lower() in CURRENCIES):
        return ToolCall(
            "currency",
            {
                "amount": float(m.group(1).replace(",", "")),
                "from_currency": m.group(2),
                "to_currency": m.group(3),
            },
            note="currency pair",
        )

    # 4. Unit conversion.
    m = re.search(rf"{NUMBER}\s*([A-Za-z°/]+)\s+(?:to|in|into)\s+([A-Za-z°/]+)", body, re.I)
    if m:
        src, dst = _canon(m.group(2)), _canon(m.group(3))
        known = (src in UNITS or src in TEMPS) and (dst in UNITS or dst in TEMPS)
        if known:
            return ToolCall(
                "convert_units",
                {
                    "value": float(m.group(1).replace(",", "")),
                    "from_unit": src,
                    "to_unit": dst,
                },
                note="unit conversion",
            )

    # 5. Weather.
    m = re.search(
        r"(?:weather|forecast|temperature|how (?:hot|cold|warm) is it)\b[^.?!]*?"
        r"\b(?:in|at|for)\s+([A-Za-zÀ-ɏ][\wÀ-ɏ'’\- ]{1,40})",
        body, re.I,
    )
    if m:
        return ToolCall("weather", {"location": m.group(1).strip(" ?.!,")}, note="weather query")
    if re.match(r"^(?:the )?weather(?: today| now)?\??$", low):
        return None  # no place named — let the model ask

    # 6. Clock.
    m = re.search(
        r"\btime\b(?:\s+is\s+it)?\s+(?:in|at)\s+([\w/+\-\s]{2,40})",
        body, re.I,
    )
    if m:
        place = m.group(1).strip(" ?.!,")
        return ToolCall("clock", {"timezone_name": place.replace(" ", "_")}, note="time query")
    if re.match(r"^(?:what(?:'s| is) the )?(?:time|date)( now| today)?\??$", low):
        return ToolCall("clock", {"timezone_name": "UTC"}, note="time query")

    # 7. Encyclopaedia. Deliberately narrow: a short noun phrase, no opinion
    #    words, or "what is the best X" starts hitting Wikipedia for no reason.
    m = re.match(
        r"^(?:who (?:is|was|were)|what (?:is|was|are|were)|tell me about|look up)\s+"
        r"(?:a |an |the )?(.+?)\s*\??$",
        body, re.I,
    )
    if m:
        tail = m.group(1).strip()
        if 0 < len(tail.split()) <= 4 and not _STOP_WIKI.search(tail):
            return ToolCall("wikipedia", {"query": tail}, note="factual lookup")

    return None


# ----------------------------------------------------------- model routing ---

ROUTE_SYSTEM = (
    "You choose a tool for the user's message. Reply with one JSON object only.\n"
    "Tools:\n{menu}\n"
    'If no tool is needed, reply {{"tool": "none"}}.'
)


async def route(
    text: str,
    *,
    tools_enabled: bool,
    engine=None,
    allow_model: bool = True,
) -> tuple[ToolCall | None, dict]:
    """Pick a tool. Returns (call, telemetry)."""
    telemetry: dict = {"tier": None, "ms": 0.0}

    call, _ = parse_slash(text)
    if call is not None:
        telemetry["tier"] = "slash"
        return call, telemetry

    if not tools_enabled:
        return None, telemetry

    call = heuristic(text)
    if call is not None:
        telemetry["tier"] = "heuristic"
        return call, telemetry

    if not allow_model or engine is None:
        return None, telemetry

    tools = available()
    if not tools:
        return None, telemetry

    messages = [
        {"role": "system", "content": ROUTE_SYSTEM.format(menu=tool_menu(tools))},
        {"role": "user", "content": text[:600]},
    ]
    parsed, ms, raw = await engine.complete_json(messages, tools_to_gbnf(tools))
    telemetry.update({"tier": "model", "ms": round(ms, 1), "raw": raw[:200]})

    if not parsed or parsed.get("tool") in (None, "none"):
        return None, telemetry
    name = parsed.get("tool")
    if name not in REGISTRY:
        telemetry["note"] = f"model picked an unknown tool: {name}"
        return None, telemetry

    return ToolCall(name, parsed.get("args") or {}, via="model", ms=ms, raw=raw), telemetry
