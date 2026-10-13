"""Deciding which tool — if any — a message needs.

Ordered cheapest-first, because a routing pass on 2 vCPU costs seconds:

1. **Slash command** — the user said exactly what they wanted. Free, certain.
2. **Heuristics** — a pasted URL, an arithmetic expression, "convert 100 km to
   miles", "weather in Paris". Regex, free, and far more reliable than a 1.5B
   model's judgement.
3. **Grammar pass** — only when the persona enables tools and nothing above
   fired. Constrained by GBNF so the output cannot be malformed. The pass
   itself is made by whoever is running the message; what is here is what it
   reads, what it is held to, and how its reply is read.

Anything the heuristics catch is a model call that never happens, which on this
hardware is the difference between an instant answer and a five-second wait.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import settings
from .tools import REGISTRY, tools_to_gbnf
from .tools.base import decision_ceiling, tool_menu
from .tools.offline import TEMPS, UNITS, _canon
from .tools.web import CURRENCY_CODES

# The same list the currency tool is held to, as the patterns below match it.
CURRENCIES = {code.lower() for code in CURRENCY_CODES}

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

# How much of a message the routing pass reads. A choice of tool is made from
# the first lines; a pasted document after them only costs seconds to read.
ROUTE_TEXT = 600

# Asking the user a question is something the grammar can say and nothing can
# act on yet, so it is not offered.
ASK = False


@dataclass
class Action:
    """What the model chose to do next, read from its reply.

    ``tool`` is a call to make. ``final`` is "write the answer now": the model
    said no tool is needed, or said something that could not be used, in which
    case ``note`` says what was wrong with it.
    """

    kind: str  # tool | final
    call: ToolCall | None = None
    raw: str = ""
    note: str | None = None


def free_route(text: str, *, tools_enabled: bool) -> tuple[ToolCall | None, str | None]:
    """The two tiers that cost nothing. Returns ``(call, tier)``.

    A command is honoured for everyone. A pattern is only tried where tools
    are on, which is the persona's choice.
    """
    call, _ = parse_slash(text)
    if call is not None:
        return call, "slash"
    if not tools_enabled:
        return None, None
    call = heuristic(text)
    if call is not None:
        return call, "heuristic"
    return None, None


def decision_messages(text: str, tools: list) -> list[dict]:
    """What the model reads when it is asked to choose: the menu and the
    start of the message, and nothing of the conversation before it."""
    return [
        {"role": "system", "content": ROUTE_SYSTEM.format(menu=tool_menu(tools))},
        {"role": "user", "content": text[:ROUTE_TEXT]},
    ]


def decision_grammar(tools: list) -> str:
    """The grammar a choice is held to."""
    return tools_to_gbnf(tools, ask=ASK)


def decision_limit(tools: list) -> int:
    """Tokens for a choice: enough for the longest reply the grammar allows,
    so a reply is never cut off before it closes. The grammar ends it long
    before that; a setting above it is honoured, one below it is not."""
    return max(settings.route_max_tokens, decision_ceiling(tools, ask=ASK))


def parse_action(said) -> Action:
    """Read one grammar-constrained reply (a ``Completion``) as an action.

    Anything that cannot be acted on is ``final`` with a note, never an
    error: a choice that came to nothing is "no tool", and the run answers.
    """
    parsed = said.parsed
    if parsed is None:
        # The grammar makes this impossible unless the reply ran out of tokens
        # before it closed. It is worth knowing it happened.
        cut_off = bool(said.raw) and said.error is None
        return Action(
            "final", raw=said.raw,
            note="the reply was not a complete JSON object" if cut_off else None,
        )
    name = parsed.get("tool")
    if name in (None, "none"):
        return Action("final", raw=said.raw)
    if name not in REGISTRY:
        return Action("final", raw=said.raw, note=f"model picked an unknown tool: {name}")
    args = parsed.get("args")
    call = ToolCall(name, args if isinstance(args, dict) else {}, via="model", ms=said.ms, raw=said.raw)
    return Action("tool", call=call, raw=said.raw)
