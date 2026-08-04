"""Tool registry, argument specs, GBNF generation, dispatch.

Two things a tool returns, and the difference matters:

* ``data``    — the structured payload a front-end card renders from. Rich.
* ``summary`` — one plain line handed back to the model. Deliberately terse,
  because a 1.5B model given a page of JSON will start reciting it instead of
  answering.
"""

from __future__ import annotations

import asyncio
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


REGISTRY: dict[str, Tool] = {}

# Outbound calls are capped globally. A public URL should not become somebody
# else's fetch proxy, and two concurrent sockets is plenty for one visitor.
_net_semaphore = asyncio.Semaphore(2)


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def tool_list(include_network: bool | None = None) -> list[dict]:
    tools = REGISTRY.values()
    if include_network is False:
        tools = [t for t in tools if not t.network]
    return [t.as_dict() for t in tools]


def available() -> list[Tool]:
    """Tools the current configuration actually permits."""
    out = []
    for t in REGISTRY.values():
        if t.network and not settings.allow_outbound:
            continue
        out.append(t)
    return out


# --------------------------------------------------------------- dispatch ---


async def call_tool(name: str, args: dict) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(False, f"No such tool: {name}", error="unknown_tool")
    if tool.network and not settings.allow_outbound:
        return ToolResult(
            False, "Network tools are disabled on this server.", error="outbound_disabled"
        )

    # Drop anything the tool did not declare — the model invents arguments.
    known = {a.name for a in tool.args}
    clean = {k: v for k, v in (args or {}).items() if k in known}
    for a in tool.args:
        if a.name not in clean and a.default is not None:
            clean[a.name] = a.default
    missing = [a.name for a in tool.required_args() if a.name not in clean]
    if missing:
        return ToolResult(
            False, f"{tool.label} needs: {', '.join(missing)}", error="missing_args"
        )

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
            f"{tool.label} timed out after {settings.tool_timeout}s.",
            ms=(time.perf_counter() - t0) * 1000,
            error="timeout",
        )
    except Exception as exc:  # noqa: BLE001 — surfaced as a failed step in the UI
        return ToolResult(
            False,
            f"{tool.label} failed: {type(exc).__name__}",
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
