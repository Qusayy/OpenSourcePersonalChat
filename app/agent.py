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
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from .config import settings
from .llm import Completion, Engine, Stop, engine as default_engine
from .personas import Persona
from .router import ToolCall, route
from .skills import Skill, build_prompt
from .tools import REGISTRY, call_tool as default_call_tool
from .tools.base import ToolResult

Event = tuple[str, dict]
ToolCaller = Callable[[str, dict], Awaitable[ToolResult]]


@dataclass
class Step:
    """One thing a run did, and what it cost.

    ``total_ms`` is the step's whole cost and nothing else's. For a pass
    through the model that is its wait for the slot plus its time inside it,
    so waiting, reading and writing add up to the step exactly. For a tool it
    is the tool's own time. A choice made without the model costs nothing.
    """

    kind: str  # route | tool | answer
    # Whether this step was a pass through the model.
    model: bool = False
    # The tool or skill, where there is one.
    name: str | None = None
    # How it came to be chosen: slash | heuristic | skill | model.
    via: str | None = None
    input: dict | None = None
    output: str | None = None
    prompt_tokens: int | None = None
    # Prompt tokens the model had to read, the rest being in its cache. No
    # engine reports this yet, and it is never guessed at.
    new_tokens: int | None = None
    output_tokens: int | None = None
    queue_ms: float | None = None
    first_token_ms: float | None = None
    total_ms: float = 0.0
    ok: bool = True
    error: str | None = None
    at: float = field(default_factory=time.time)


@dataclass
class Trace:
    """What one run did, filled in as it happens.

    The run appends to this before it announces anything, so a caller that
    stores what it finds here each time an event arrives has always stored a
    thing before the page hears of it.
    """

    # The run's name: what it is stopped by, and what its steps are filed under.
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Everything the run did, in order.
    steps: list[Step] = field(default_factory=list)
    # Every tool call that finished, in order, with what it returned.
    calls: list[tuple[ToolCall, ToolResult]] = field(default_factory=list)
    # The answer's text once the writing pass has ended; None until then. An
    # answer that was stopped before its first word is "", not None.
    answer: str | None = None
    # That pass's own figures: what `done` carries.
    usage: dict = field(default_factory=dict)
    # How it ended: answered | stopped | failed. None while it is running.
    outcome: str | None = None
    # The run's own stopwatch, first stage to last. It is a little more than
    # the steps add up to: the difference is the time between them.
    wall_ms: float | None = None

    def totals(self) -> dict:
        """The run in a line: what the page is told when it ends."""
        return {
            "id": self.run_id,
            "outcome": self.outcome,
            "steps": len(self.steps),
            "passes": sum(1 for s in self.steps if s.model),
            "total_ms": round(sum(s.total_ms for s in self.steps), 1),
            "wall_ms": None if self.wall_ms is None else round(self.wall_ms, 1),
        }


def _pass_ms(queue_wait_ms: float | None, total_ms: float | None) -> float:
    """What a pass cost: its wait for the slot and its time inside it."""
    return (queue_wait_ms or 0.0) + (total_ms or 0.0)


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
    request_id: str | None = None,
) -> AsyncIterator[Event]:
    """Answer one message. Yields the events of the run as they happen.

    ``history`` is the conversation before this message, oldest first, as
    ``{"role", "content"}``. The stream ends in ``done`` or in ``error``.

    ``request_id`` is the name the run can be stopped by, from its first stage
    to its last: ``engine.cancel(request_id)``. It is the trace's ``run_id``;
    given here, it replaces the one the trace made up.

    The last event is always ``run``: how it ended, how many steps and passes
    it took, and what they cost together.
    """
    trace = trace if trace is not None else Trace()
    if request_id:
        trace.run_id = request_id
    return _Run(
        message=message,
        persona=persona,
        skill=skill,
        history=history,
        trace=trace,
        engine=engine if engine is not None else default_engine,
        call_tool=call_tool if call_tool is not None else default_call_tool,
        tools_enabled=settings.tools_enabled if tools_enabled is None else tools_enabled,
        request_id=trace.run_id,
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
    request_id: str
    # The calls this message was routed to, and the line each successful one
    # gave back for the model to read.
    calls: list[ToolCall] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    # Set by whoever asks for this run to stop. Looked at before each stage,
    # and waited on while a tool is out.
    stop: Stop = field(default_factory=Stop)

    async def go(self) -> AsyncIterator[Event]:
        # Held for the whole run, so that Stop finds it between passes and
        # while a tool is out, not only while the model is writing.
        started = time.perf_counter()
        with self.engine.hold(self.request_id) as self.stop:
            try:
                # Closed with this generator, not whenever it is collected: a
                # pass underneath may be holding the one inference slot.
                async with aclosing(self._stages()) as events:
                    async for event in events:
                        yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the stream ends in words, not a dropped connection
                self.trace.outcome = "failed"
                yield "error", {"message": f"{type(exc).__name__}: {exc}"}
            # Not reached when the reader has gone: there is nobody to tell.
            self.trace.wall_ms = (time.perf_counter() - started) * 1000
            yield "run", self.trace.totals()

    async def _stages(self) -> AsyncIterator[Event]:
        async for event in self._route():
            yield event
        for index, call in enumerate(self.calls):
            if self.stop.is_set():
                break
            async for event in self._act(index, call):
                yield event
        if self.stop.is_set():
            yield self._stopped()
            return
        async with aclosing(self._answer(self._prompt())) as events:
            async for event in events:
                yield event

    def _stopped(self) -> Event:
        """How a run ends when it was stopped before any answer was begun.

        It is the same ``done`` a stopped answer ends in, with nothing in the
        figures: no prompt was read and no token written.
        """
        self.trace.outcome = "stopped"
        self.trace.answer = ""
        self.trace.usage = {
            "cancelled": True,
            "ttft_ms": None,
            "queue_wait_ms": None,
            "prompt_tokens": None,
            "completion_tokens": 0,
            "prefill_tps": None,
            "gen_tps": None,
            "total_ms": None,
            "rss_mb": None,
            "tool_calls": len(self.trace.calls),
        }
        return "done", self.trace.usage

    # ------------------------------------------------------------ routing --

    async def _route(self) -> AsyncIterator[Event]:
        if self.skill is not None:
            self.calls = self.skill.prepare(self.message)
            self.trace.steps.append(Step(
                "route", name=self.skill.id, via="skill",
                input={"calls": [{"tool": c.name, "args": c.args} for c in self.calls]},
            ))
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
            self.message, tools_enabled=self.persona.tools, engine=self.engine,
            request_id=self.request_id,
        )
        said: Completion | None = told.get("completion")
        self._record_route(call, told, said)
        if self.stop.is_set():
            # Stopped while the choice was being made. Whatever came back is
            # not acted on, and the step that was announced is closed.
            if needs_pass:
                yield "step", {
                    "id": "route", "kind": "route", "status": "stopped",
                    "label": "Choosing a tool", "glyph": "◇",
                    "ms": round(told.get("ms") or 0, 1),
                }
            return
        if call is not None:
            self.calls = [call]
        if call is not None or needs_pass:
            tool = REGISTRY[call.name] if call else None
            closing = {
                "id": "route", "kind": "route", "status": "ok",
                "label": tool.label if tool else "No tool needed",
                "detail": told.get("tier") or "direct",
                "glyph": tool.glyph if tool else "◇",
                "tool": call.name if call else None,
                "ms": round(told.get("ms") or 0, 1),
            }
            if said is not None:
                # The choice cost a pass, and a pass is counted in tokens.
                closing["tokens_in"] = said.prompt_tokens
                closing["tokens_out"] = said.completion_tokens
            yield "step", closing

    def _record_route(self, call: ToolCall | None, told: dict, said: Completion | None) -> None:
        """Write down how the choice was made. Nothing is written when nothing
        was tried: a persona without tools, and a message that is no command."""
        tier = told.get("tier")
        if tier is None:
            return
        if said is None:
            self.trace.steps.append(Step(
                "route", name=call.name if call else None, via=tier,
                input=call.args if call else None, output=call.note if call else None,
            ))
            return
        stopped = said.cancelled or self.stop.is_set()
        # What went wrong, if anything did: it was stopped, the pass failed, or
        # what it said could not be used.
        error = "stopped" if stopped else (said.error or told.get("note"))
        # A pass that never reached the model still took the time it took.
        cost = said.ms if said.total_ms is None else _pass_ms(said.queue_wait_ms, said.total_ms)
        self.trace.steps.append(Step(
            "route", model=True, name=call.name if call else None, via=tier,
            input={"text": told.get("text")}, output=said.raw,
            prompt_tokens=said.prompt_tokens, output_tokens=said.completion_tokens,
            queue_ms=said.queue_wait_ms, first_token_ms=said.ttft_ms, total_ms=cost,
            ok=error is None, error=error,
        ))

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
        began = time.perf_counter()
        result = await self._call(call)
        if result is None:
            self.trace.steps.append(Step(
                "tool", name=call.name, via=call.via, input=call.args,
                total_ms=(time.perf_counter() - began) * 1000, ok=False, error="stopped",
            ))
            yield "step", {
                "id": step_id, "kind": "tool", "status": "stopped",
                "label": label, "glyph": glyph, "tool": call.name,
            }
            return
        self.trace.calls.append((call, result))
        self.trace.steps.append(Step(
            "tool", name=call.name, via=call.via, input=call.args, output=result.summary,
            total_ms=result.ms, ok=result.ok,
            error=None if result.ok else (result.error or "failed"),
        ))
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

    async def _call(self, call: ToolCall) -> ToolResult | None:
        """Run one tool, unless Stop comes first. None means it did.

        A tool that goes out can take ten seconds. Whoever pressed Stop should
        not have to wait for a page they no longer want.
        """
        tool = asyncio.ensure_future(self.call_tool(call.name, call.args))
        asked = asyncio.ensure_future(self.stop.wait())
        try:
            await asyncio.wait({tool, asked}, return_when=asyncio.FIRST_COMPLETED)
            return tool.result() if tool.done() else None
        finally:
            # Whichever lost is called off. So is the tool if this run itself
            # is being cancelled: nothing may be left running for nobody.
            for task in (tool, asked):
                if not task.done():
                    task.cancel()

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
        # Kept with the step: a prompt that lost its oldest turns is not the
        # prompt the conversation suggests.
        trimmed = {"dropped": dropped} if dropped else None
        if dropped:
            yield "trimmed", {"dropped": dropped}

        yield "step", {
            "id": "answer", "kind": "model", "status": "running",
            "label": "Writing the answer", "glyph": "✦",
        }
        async with aclosing(
            self.engine.stream_chat(fitted, sampling, self.request_id)
        ) as stream:
            async for event, data in stream:
                if event == "error":
                    self.trace.steps.append(Step(
                        "answer", model=True, input=trimmed, ok=False, error=data.get("message"),
                    ))
                    self.trace.outcome = "failed"
                    yield event, data
                    continue
                if event != "done":
                    yield event, data
                    continue
                stopped = bool(data.get("cancelled"))
                self.trace.steps.append(Step(
                    "answer", model=True, input=trimmed,
                    prompt_tokens=data.get("prompt_tokens"),
                    output_tokens=data.get("completion_tokens"),
                    queue_ms=data.get("queue_wait_ms"), first_token_ms=data.get("ttft_ms"),
                    total_ms=_pass_ms(data.get("queue_wait_ms"), data.get("total_ms")),
                    ok=not stopped, error="stopped" if stopped else None,
                ))
                self.trace.outcome = "stopped" if stopped else "answered"
                self.trace.answer = data.pop("text", "")
                data["tool_calls"] = len(self.calls)
                self.trace.usage = data
                yield "step", {
                    "id": "answer", "kind": "model", "status": "ok",
                    "label": "Answer", "glyph": "✦",
                    "ms": round(data.get("total_ms") or 0, 1),
                    "tokens_in": data.get("prompt_tokens"),
                    "tokens_out": data.get("completion_tokens"),
                }
                yield "done", data
