"""One message, from the question to the answer.

The stages a message goes through are here, in the order they happen: decide
which tools to call, call them, build the prompt, write the answer. Each stage
says what it is doing as it does it, as ``(event, payload)`` pairs the request
handler forwards to the page unchanged.

Two things this module deliberately does not do:

* **It stores nothing.** What happened is written into a ``Trace`` as it
  happens, and whoever asked for the run decides what to keep. A run can then
  be driven without a database, which is how it is tested.
* **It reaches for nothing by itself.** The engine and the tool dispatcher are
  handed in, so a test can pass an engine that plays back prepared text and
  tools that never touch the network.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from .config import settings
from .llm import Engine, engine as default_engine
from .personas import Persona
from .router import ToolCall, route
from .skills import Skill, build_prompt
from .tools import REGISTRY, call_tool as default_call_tool
from .tools.base import ToolResult

Event = tuple[str, dict]
ToolCaller = Callable[[str, dict], Awaitable[ToolResult]]


@dataclass
class Trace:
    """What one run did, filled in as it happens.

    The run appends to this before it announces anything, so a caller that
    stores what it finds here each time an event arrives has always stored a
    thing before the page hears of it.
    """

    # Every tool call that finished, in order, with what it returned.
    calls: list[tuple[ToolCall, ToolResult]] = field(default_factory=list)
    # The answer's text once the writing pass has ended; None until then. An
    # answer that was stopped before its first word is "", not None.
    answer: str | None = None
    # That pass's own figures: what `done` carries.
    usage: dict = field(default_factory=dict)


def run(
    message: str,
    *,
    persona: Persona,
    skill: Skill | None = None,
    history: list[dict],
    trace: Trace | None = None,
    engine: Engine | None = None,
    call_tool: ToolCaller | None = None,
    tools_enabled: bool | None = None,
) -> AsyncIterator[Event]:
    """Answer one message. Yields the events of the run as they happen.

    ``history`` is the conversation before this message, oldest first, as
    ``{"role", "content"}``. The stream ends in ``done`` or in ``error``.
    """
    return _Run(
        message=message,
        persona=persona,
        skill=skill,
        history=history,
        trace=trace if trace is not None else Trace(),
        engine=engine if engine is not None else default_engine,
        call_tool=call_tool if call_tool is not None else default_call_tool,
        tools_enabled=settings.tools_enabled if tools_enabled is None else tools_enabled,
    ).go()


@dataclass
class _Run:
    message: str
    persona: Persona
    skill: Skill | None
    history: list[dict]
    trace: Trace
    engine: Engine
    call_tool: ToolCaller
    tools_enabled: bool
    # The calls this message was routed to, and the line each successful one
    # gave back for the model to read.
    calls: list[ToolCall] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)

    async def go(self) -> AsyncIterator[Event]:
        try:
            async for event in self._route():
                yield event
            for index, call in enumerate(self.calls):
                async for event in self._act(index, call):
                    yield event
            # Closed with this generator, not whenever it is collected: the
            # pass underneath is holding the one inference slot.
            async with aclosing(self._answer(self._prompt())) as events:
                async for event in events:
                    yield event
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the stream ends in words, not a dropped connection
            yield "error", {"message": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------ routing --

    async def _route(self) -> AsyncIterator[Event]:
        if self.skill is not None:
            self.calls = self.skill.prepare(self.message)
            yield "step", {
                "id": "route", "kind": "route", "status": "ok",
                "label": self.skill.name, "detail": "skill",
                "glyph": self.skill.glyph, "tool": self.skill.id, "ms": 0,
            }
            return
        if not self.tools_enabled:
            return

        needs_pass = self.persona.tools and not self.message.startswith("/")
        if needs_pass:
            yield "step", {
                "id": "route", "kind": "route", "status": "running",
                "label": "Choosing a tool", "glyph": "◇",
            }
        call, told = await route(
            self.message, tools_enabled=self.persona.tools, engine=self.engine
        )
        if call is not None:
            self.calls = [call]
        if call is not None or needs_pass:
            tool = REGISTRY[call.name] if call else None
            yield "step", {
                "id": "route", "kind": "route", "status": "ok",
                "label": tool.label if tool else "No tool needed",
                "detail": told.get("tier") or "direct",
                "glyph": tool.glyph if tool else "◇",
                "tool": call.name if call else None,
                "ms": round(told.get("ms") or 0, 1),
            }

    # ---------------------------------------------------------- one tool --

    async def _act(self, index: int, call: ToolCall) -> AsyncIterator[Event]:
        tool = REGISTRY.get(call.name)
        step_id = f"tool-{index}"
        label = tool.label if tool else call.name
        glyph = tool.glyph if tool else "◆"
        yield "step", {
            "id": step_id, "kind": "tool", "status": "running",
            "label": label, "glyph": glyph,
            "tool": call.name, "args": call.args,
        }
        result = await self.call_tool(call.name, call.args)
        self.trace.calls.append((call, result))
        yield "step", {
            "id": step_id, "kind": "tool", "status": "ok" if result.ok else "failed",
            "label": label, "glyph": glyph,
            "tool": call.name, "ms": round(result.ms, 1),
            "detail": None if result.ok else (result.error or "failed"),
        }
        if result.ok:
            self.summaries.append(f"{call.name}: {result.summary}")
            yield "card", {
                "step_id": step_id, "card": result.card,
                "tool": call.name, "data": result.data,
            }
        else:
            # A failed tool is shown, not hidden — the model still answers.
            yield "card", {
                "step_id": step_id, "card": "error", "tool": call.name,
                "data": {"message": result.summary or result.error},
            }

    # ---------------------------------------------------------- the prompt --

    def _prompt(self) -> list[dict]:
        messages = [{"role": "system", "content": self.persona.system}]
        messages += [{"role": m["role"], "content": m["content"]} for m in self.history]

        if self.skill is not None:
            content = build_prompt(self.skill, self.message, self.summaries)
        elif self.summaries:
            # The tool output goes inside the user turn, not into a second
            # system message. A 1.5B model barely attends to a trailing system
            # message and falls back on its "I don't have real-time data"
            # prior — it will describe the weather as unavailable while the
            # card beside it shows today's temperature. Stated as part of the
            # question, it complies.
            facts = "\n".join(f"- {s}" for s in self.summaries)
            content = (
                f"{self.message}\n\n"
                f"Live data, retrieved just now by a tool:\n{facts}\n\n"
                "Answer the question using only the data above. It is "
                "current and correct. Do not say you lack real-time "
                "access, and do not recalculate any figure."
            )
        else:
            content = self.message
        messages.append({"role": "user", "content": content})
        return messages

    # ---------------------------------------------------------- the answer --

    async def _answer(self, messages: list[dict]) -> AsyncIterator[Event]:
        sampling = self.persona.sampling()
        fitted, _, dropped = self.engine.fit_context(messages, sampling["max_tokens"])
        if dropped:
            yield "trimmed", {"dropped": dropped}

        yield "step", {
            "id": "answer", "kind": "model", "status": "running",
            "label": "Writing the answer", "glyph": "✦",
        }
        async with aclosing(self.engine.stream_chat(fitted, sampling)) as stream:
            async for event, data in stream:
                if event != "done":
                    yield event, data
                    continue
                self.trace.answer = data.pop("text", "")
                data["tool_calls"] = len(self.calls)
                self.trace.usage = data
                yield "step", {
                    "id": "answer", "kind": "model", "status": "ok",
                    "label": "Answer", "glyph": "✦",
                    "ms": round(data.get("total_ms") or 0, 1),
                }
                yield "done", data
