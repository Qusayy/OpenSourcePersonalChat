"""What the user decided themselves, and the record of a call.

The model decides what every message needs, and calls a tool itself when it
judges one is needed (``calls``). One thing is decided without it: a **slash
command** (``/calc 2+2``, ``/python ...``), where the user has named the tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..tools import REGISTRY

SLASH = {
    "calc": "calculator", "calculate": "calculator", "math": "calculator",
    "wiki": "wikipedia", "wikipedia": "wikipedia",
    "weather": "weather", "forecast": "weather",
    "read": "read_url", "url": "read_url", "fetch": "read_url",
    "convert": "convert_units", "units": "convert_units",
    "currency": "currency", "fx": "currency", "money": "currency",
    "clock": "clock", "time": "clock", "date": "clock",
    "stats": "text_stats", "textstats": "text_stats",
    "remember": "remember", "note": "remember",
    "recall": "recall", "notes": "recall",
    "search": "web_search", "web": "web_search", "google": "web_search",
    "news": "news", "headlines": "news",
    "open": "open_result",
    "python": "python", "py": "python", "run": "python",
    "file": "read_file", "cat": "read_file",
    "runfile": "run_file",
}

URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
NUMBER = r"(-?\d[\d,]*\.?\d*)"


@dataclass
class ToolCall:
    name: str
    args: dict
    via: str = "model"  # slash | model
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

    found = re.match(r"/(\S+)\s*(.*)", stripped, re.S)
    head, rest = found.group(1), found.group(2)
    tool_name = SLASH.get(head.lower())
    if tool_name is None or tool_name not in REGISTRY:
        return None, text

    if tool_name == "python":
        # The program is the rest, run as it was written.
        code = program_of(rest) or rest
        if not code.strip():
            return None, text
        return ToolCall("python", {"code": code}, via="slash"), rest
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
    if tool_name == "remember":
        return {"text": rest} if rest else None
    if tool_name == "recall":
        return {"query": rest}
    if tool_name in ("read_file", "run_file"):
        return {"path": rest} if rest else None
    if tool_name == "web_search":
        return {"query": rest} if rest else None
    if tool_name == "news":
        return {"topic": rest} if rest else {}
    if tool_name == "open_result":
        return {"number": rest} if rest in [str(n) for n in range(1, 9)] else None
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


# A fenced Python block: the program in "/python ```python …```".
FENCED = re.compile(r"```(?:python|py)[ \t]*\n(.*?)```", re.S | re.I)


def program_of(text: str) -> str:
    """The program in a fenced Python block, or "" where there is none."""
    found = FENCED.search(text or "")
    return found.group(1).strip("\n") + "\n" if found else ""


def free_route(text: str) -> tuple[ToolCall | None, str | None]:
    """What the user decided themselves. Returns ``(call, "slash")`` for a
    slash command, or ``(None, None)``: the model decides."""
    call, _ = parse_slash(text)
    if call is not None:
        return call, "slash"
    return None, None
