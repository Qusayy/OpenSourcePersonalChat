"""What the model chooses from, and how its choice is read.

The model decides what every message needs: a tool, a question back, files,
or nothing. It is the one deciding because it is the one that understands
the message; no pattern stands in for it.

One thing is decided without it: a **slash command** (``/calc 2+2``,
``/python …``), where the user has named the tool themselves.

The choice is made in a pass held to a GBNF grammar, so it cannot come out
malformed. The pass itself is made by whoever is running the message, from
the run's own transcript; what is here is what it is held to and how its
reply is read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from ..tools import REGISTRY, tools_to_gbnf
from ..tools.grammar import decision_ceiling

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
    via: str = "model"  # slash | model | skill
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
        return ToolCall("python", {"task": "run the program as given"}, via="slash",
                        meta={"program": code}), rest
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


# ----------------------------------------------------------- model routing ---

# Whether the model is offered the choice of asking the user a question.
ASK = True


@dataclass
class Action:
    """What the model chose to do next, read from its reply.

    ``tool`` is a call to make. ``ask`` is a question to put to the user in
    place of an answer. ``files`` is "write the answer as a project of
    files". ``final`` is "write the answer now": the model said no tool is
    needed, or said something that could not be used, in which case ``note``
    says what was wrong with it.
    """

    kind: str  # tool | skill | ask | files | final
    call: ToolCall | None = None
    question: str | None = None
    raw: str = ""
    note: str | None = None
    # For a skill the model chose: the skill, and the inputs it gave it.
    skill: Any = None
    values: dict | None = None


def ask_reply(question: str) -> str:
    """A question, as the grammar has the model write one."""
    return json.dumps({"tool": "ask", "args": {"question": question}}, ensure_ascii=False)


def free_route(text: str) -> tuple[ToolCall | None, str | None]:
    """What the user decided themselves. Returns ``(call, "slash")`` for a
    slash command, or ``(None, None)``: the model decides."""
    call, _ = parse_slash(text)
    if call is not None:
        return call, "slash"
    return None, None


def decision_grammar(tools: list) -> str:
    """The grammar a choice is held to."""
    return tools_to_gbnf(tools, ask=ASK, files=True)


def decision_limit(tools: list) -> int:
    """Tokens for a choice: enough for the longest reply the grammar allows,
    so a reply is never cut off before it closes. The grammar ends it long
    before that; a setting above it is honoured, one below it is not."""
    return max(settings.route_max_tokens, decision_ceiling(tools, ask=ASK, files=True))


def parse_action(said, skills: dict | None = None) -> Action:
    """Read one grammar-constrained reply (a ``Completion``) as an action.

    ``skills`` are the skills the model was offered, by the name it chooses
    them by. Anything that cannot be acted on is ``final`` with a note, never
    an error: a choice that came to nothing is "no tool", and the run answers.
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
    if name == "write_files":
        return Action("files", raw=said.raw)
    args = parsed.get("args")
    if name == "ask" and ASK:
        question = args.get("question") if isinstance(args, dict) else None
        if isinstance(question, str) and question.strip():
            return Action("ask", question=question.strip(), raw=said.raw)
        return Action("final", raw=said.raw, note="asked without a question")
    if skills and name in skills:
        values = args if isinstance(args, dict) else {}
        return Action("skill", skill=skills[name], values=values, raw=said.raw)
    if name not in REGISTRY:
        return Action("final", raw=said.raw, note=f"model picked an unknown tool: {name}")
    call = ToolCall(name, args if isinstance(args, dict) else {}, via="model", ms=said.ms, raw=said.raw)
    return Action("tool", call=call, raw=said.raw)
