"""Tool registry, argument specs, GBNF generation, dispatch.

Two things a tool returns, and the difference matters:

* ``data``    — the structured payload a front-end card renders from. Rich.
* ``summary`` — one plain line handed back to the model. Deliberately terse,
  because a 1.5B model given a page of JSON will start reciting it instead of
  answering.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import settings


@dataclass(frozen=True)
class Arg:
    name: str
    type: str = "string"  # string | number
    required: bool = True
    description: str = ""
    default: Any = None


@dataclass
class ToolResult:
    ok: bool
    summary: str
    card: str | None = None
    data: dict = field(default_factory=dict)
    ms: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "card": self.card,
            "data": self.data,
            "ms": round(self.ms, 1),
            "error": self.error,
        }


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    glyph: str
    description: str
    args: tuple[Arg, ...]
    run: Callable[..., Awaitable[ToolResult]]
    card: str
    network: bool = False
    example: str = ""

    def required_args(self) -> tuple[Arg, ...]:
        return tuple(a for a in self.args if a.required)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "glyph": self.glyph,
            "description": self.description,
            "card": self.card,
            "network": self.network,
            "example": self.example,
            "args": [
                {"name": a.name, "type": a.type, "required": a.required,
                 "description": a.description}
                for a in self.args
            ],
        }


# Argument names are internal; a person being told what is missing should get
# a noun phrase, not a parameter.
MISSING_HINT = {
    "expression": "an expression",
    "url": "a link",
    "location": "a place",
    "query": "something to look up",
    "text": "some text",
    "value": "a value",
    "from_unit": "a unit to convert from",
    "to_unit": "a unit to convert to",
    "amount": "an amount",
    "from_currency": "a currency to convert from",
    "to_currency": "a currency to convert to",
    "timezone_name": "a timezone",
}

REGISTRY: dict[str, Tool] = {}

# Outbound calls are capped globally. A public URL should not become somebody
# else's fetch proxy, and two concurrent sockets is plenty for one visitor.
_net_semaphore = asyncio.Semaphore(2)


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def available() -> list[Tool]:
    """Tools the current configuration actually permits."""
    out = []
    for t in REGISTRY.values():
        if t.network and not settings.allow_outbound:
            continue
        out.append(t)
    return out


# ---------------------------------------------------------------- checking ---


@dataclass(frozen=True)
class Rejection:
    """Why a call was not made.

    Said twice, because it has two readers. ``reasons`` are for a model that
    may be asked to try again: one plain fact per problem, naming the argument
    as the model knows it. ``summary`` is the sentence a person reads on the
    failed step. ``code`` is the short word the step is filed under.
    """

    reasons: tuple[str, ...]
    summary: str
    code: str  # "missing input" | "bad input"


# A number written with thousands separators, the way amounts are typed.
_GROUPED = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _coerce(arg: Arg, value: Any) -> tuple[Any, str | None]:
    """One value against what its argument declares.

    Returns the value to use and nothing, or anything and a complaint. The
    complaint ends a sentence that begins with the argument's name.
    """
    if arg.type == "number":
        # A bool is an int to Python and a mistake to everyone else.
        if isinstance(value, bool):
            return value, f"must be a number; got {value!r}"
        if isinstance(value, (int, float)):
            number = value
        elif isinstance(value, str):
            text = value.strip()
            if _GROUPED.match(text):
                text = text.replace(",", "")
            try:
                number = int(text)
            except ValueError:
                try:
                    number = float(text)
                except ValueError:
                    return value, f"must be a number; got {value!r}"
        else:
            return value, f"must be a number; got {_kind(value)}"
        if not math.isfinite(number):
            return value, f"must be a finite number; got {value!r}"
        return number, None

    if isinstance(value, str):
        return value, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value), None
    return value, f"must be text; got {_kind(value)}"


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, (list, tuple, set)):
        return "a list"
    return f"a {type(value).__name__}"


def check(tool: Tool, args: dict | None) -> tuple[dict, Rejection | None]:
    """Turn what a call was given into what the tool can be run with.

    Returns the arguments to run with and nothing, or a ``Rejection`` saying
    why it should not be run. Every caller comes through here, so a tool never
    has to wonder whether its number is a number:

    * arguments the tool never declared are dropped — the model invents them;
    * a number written as text becomes a number, and a number where text is
      wanted becomes text;
    * anything else of the wrong kind is refused, by name;
    * an argument left out, or given as nothing, takes its default;
    * a required one still missing after that is refused.
    """
    declared = {a.name: a for a in tool.args}
    clean: dict[str, Any] = {}
    refused: set[str] = set()
    reasons: list[str] = []
    said: list[str] = []

    for name, value in (args or {}).items():
        arg = declared.get(name)
        if arg is None or value is None:
            continue
        value, complaint = _coerce(arg, value)
        if complaint is None:
            clean[name] = value
            continue
        refused.add(name)
        reasons.append(f"{name} {complaint}")
        hint = MISSING_HINT.get(name, name)
        if arg.type == "number":
            said.append(f"{tool.label} could not use {value!r} as {hint}: it is not a number.")
        else:
            said.append(f"{tool.label} could not use that as {hint}: it is not text.")

    # A default stands in for an argument that was left out. It does not paper
    # over one that was given and refused.
    for arg in tool.args:
        if arg.name not in clean and arg.name not in refused and arg.default is not None:
            clean[arg.name] = arg.default

    missing = [
        a.name for a in tool.required_args() if a.name not in clean and a.name not in refused
    ]
    if missing:
        reasons += [f"{name} is required and was not given" for name in missing]
        said.append(
            f"{tool.label} needs {' and '.join(MISSING_HINT.get(m, m) for m in missing)}."
        )

    if not reasons:
        return clean, None
    code = "bad input" if refused else "missing input"
    return clean, Rejection(tuple(reasons), " ".join(said), code)


# --------------------------------------------------------------- dispatch ---


async def call_tool(name: str, args: dict) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(False, f"There is no tool called {name!r}.", error="unknown tool")
    if tool.network and not settings.allow_outbound:
        return ToolResult(
            False,
            "This server has outgoing requests switched off, so Wikipedia, "
            "weather, currency and page reading are unavailable.",
            error="offline mode",
        )

    clean, rejection = check(tool, args)
    if rejection is not None:
        return ToolResult(False, rejection.summary, error=rejection.code)

    t0 = time.perf_counter()
    try:
        if tool.network:
            async with _net_semaphore:
                result = await asyncio.wait_for(
                    tool.run(**clean), timeout=settings.tool_timeout
                )
        else:
            result = await asyncio.wait_for(tool.run(**clean), timeout=settings.tool_timeout)
    except asyncio.TimeoutError:
        return ToolResult(
            False,
            f"{tool.label} gave up after {settings.tool_timeout} seconds. "
            "The source may be slow or unreachable — try again.",
            ms=(time.perf_counter() - t0) * 1000,
            error="timeout",
        )
    except Exception as exc:  # noqa: BLE001 — surfaced as a failed step in the UI
        return ToolResult(
            False,
            f"{tool.label} could not finish. Try again, or ask without it.",
            ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )

    result.ms = (time.perf_counter() - t0) * 1000
    if result.card is None:
        result.card = tool.card
    return result


# ------------------------------------------------------------------ GBNF ---

_GBNF_PRIMITIVES = r"""
string ::= "\"" ([^"\\\x7F\x00-\x1F] | "\\" ["\\bfnrt])* "\""
number ::= "-"? ("0" | [1-9] [0-9]{0,15}) ("." [0-9]{1,6})?
"""


def _rule_name(tool_name: str) -> str:
    return "t-" + tool_name.replace("_", "-")


def tools_to_gbnf(tools: list[Tool] | None = None) -> str:
    """Build a grammar that can only produce a valid tool call.

    This is what makes routing viable on a 1.5B model. Asked politely for JSON
    it produces trailing commas, prose preambles and invented keys; constrained
    by this grammar, malformed output is not merely unlikely, it is
    unrepresentable.
    """
    tools = tools if tools is not None else available()
    lines: list[str] = []
    alts: list[str] = []

    for tool in tools:
        rule = _rule_name(tool.name)
        alts.append(rule)
        parts = [f'"{{\\"tool\\": \\"{tool.name}\\", \\"args\\": {{"']
        req = tool.required_args()
        for i, arg in enumerate(req):
            sep = "" if i == 0 else ", "
            parts.append(f'"{sep}\\"{arg.name}\\": "')
            parts.append("string" if arg.type == "string" else "number")
        parts.append('"}}"')
        lines.append(f"{rule} ::= {' '.join(parts)}")

    alts.append("t-none")
    lines.append('t-none ::= "{\\"tool\\": \\"none\\"}"')

    root = "root ::= " + " | ".join(alts)
    return "\n".join([root, *lines, _GBNF_PRIMITIVES])


def tool_menu(tools: list[Tool] | None = None) -> str:
    """The compact description block shown to the model during routing."""
    tools = tools if tools is not None else available()
    rows = []
    for t in tools:
        arg_names = ", ".join(a.name for a in t.required_args()) or "none"
        rows.append(f"- {t.name}({arg_names}): {t.description}")
    return "\n".join(rows)
