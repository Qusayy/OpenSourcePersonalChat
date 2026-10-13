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
import re
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from .config import settings
from .llm import Completion, Engine, Stop, engine as default_engine
from .personas import Persona
from .router import (
    Action,
    ToolCall,
    decision_grammar,
    decision_limit,
    decision_messages,
    free_route,
    parse_action,
    repair_messages,
)
from .skills import SAY_SO, Skill, build_prompt, did_not_work
from .tools import REGISTRY, call_tool as default_call_tool
from .tools.base import Rejection, ToolResult, available, check, was_given

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

    kind: str  # route | repair | tool | answer
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
    # The prompt that pass was given, as it was sent: after the tool results
    # were folded in and after any trimming. None if no answer was begun.
    prompt: list[dict] | None = None
    # How it ended: answered | asked | stopped | failed. None while it is
    # running. "asked" is a question put to the user in place of an answer.
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


# Where one sentence ends and the next begins.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def what_happened(result: ToolResult) -> str:
    """A failed call as one fact, for the model to read.

    The sentence a failure is given for a person often goes on to advise:
    "Try again", "Try adding a country". A small model reading that in a tool
    result takes it as an order and acts on it, so only what happened is
    passed on: the first sentence.
    """
    said = (result.summary or result.error or "It did not work.").strip()
    return _SENTENCE_END.split(said, maxsplit=1)[0]


def _pass_ms(queue_wait_ms: float | None, total_ms: float | None) -> float:
    """What a pass cost: its wait for the slot and its time inside it."""
    return (queue_wait_ms or 0.0) + (total_ms or 0.0)


def _total(figures) -> int | None:
    """Token counts added up. None if none of the passes ever ran."""
    known = [f for f in figures if f is not None]
    return sum(known) if known else None


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
    seed: int | None = None,
    follows_ask: bool = False,
) -> AsyncIterator[Event]:
    """Answer one message. Yields the events of the run as they happen.

    ``history`` is the conversation before this message, oldest first, as
    ``{"role", "content"}``. The stream ends in ``done`` or in ``error``.

    ``request_id`` is the name the run can be stopped by, from its first stage
    to its last: ``engine.cancel(request_id)``. It is the trace's ``run_id``;
    given here, it replaces the one the trace made up.

    ``seed`` is given to every pass of the run, so that the same message
    against the same model gives the same run. The site leaves it unset.

    ``follows_ask`` says the last thing in ``history`` is a question the model
    chose to ask the user. This message is then their answer to it, and is
    routed with that question in view.

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
        seed=seed,
        follows_ask=follows_ask,
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
    seed: int | None = None
    follows_ask: bool = False
    # A question the model chose to ask in place of an answer, and the pass
    # that wrote it.
    question: str | None = None
    asked_by: Completion | None = None
    # The calls this message was routed to, and the line each successful one
    # gave back for the model to read.
    calls: list[ToolCall] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    # And what each one that failed is known to have done, for the same reader.
    failures: list[str] = field(default_factory=list)
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
        if self.question is not None and not self.stop.is_set():
            for event in self._ask():
                yield event
            return
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

    def _ask(self) -> list[Event]:
        """How a run ends when the model asked a question instead of choosing.

        The question is the reply. It was written by the pass that chose to
        ask, so nothing more is generated: it is sent whole, with that pass's
        own figures, and no answer pass is made.
        """
        said = self.asked_by
        self.trace.outcome = "asked"
        self.trace.answer = self.question
        self.trace.usage = {
            "cancelled": False,
            "asked": True,
            "ttft_ms": said.ttft_ms,
            "queue_wait_ms": said.queue_wait_ms,
            "prompt_tokens": said.prompt_tokens,
            "completion_tokens": said.completion_tokens or 0,
            # The pass wrote JSON under a grammar. Its speed is not the speed
            # of an answer, and is not passed off as one.
            "prefill_tps": None,
            "gen_tps": None,
            "total_ms": said.total_ms,
            "rss_mb": None,
            "tool_calls": 0,
        }
        token = {"t": self.question}
        if said.ttft_ms is not None:
            token["ttft_ms"] = said.ttft_ms
        return [("token", token), ("done", self.trace.usage)]

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

        # The two tiers that cost nothing come first. A command works for
        # every persona; a pattern only where the persona has tools.
        call, tier = free_route(self.message, tools_enabled=self.persona.tools)
        if call is not None:
            self.calls = [call]
            self.trace.steps.append(Step(
                "route", name=call.name, via=tier, input=call.args, output=call.note,
            ))
            yield "step", self._routed(call, tier, ms=0)
            return

        tools = available() if self.persona.tools else []
        if not tools:
            return

        # Only now is a pass about to be made, so only now is one announced.
        yield "step", {
            "id": "route", "kind": "route", "status": "running",
            "label": "Choosing a tool", "glyph": "◇",
        }
        action, passes = await self._decide(tools)
        # The choice is one step on the page however many passes it took, and
        # says what all of them cost.
        ms = round(sum(said.ms for said in passes), 1)
        if self.stop.is_set():
            # Stopped while the choice was being made. Whatever came back is
            # not acted on, and the step that was announced is closed.
            yield "step", {
                "id": "route", "kind": "route", "status": "stopped",
                "label": "Choosing a tool", "glyph": "◇", "ms": ms,
            }
            return
        if action.kind == "tool":
            self.calls = [action.call]
        elif action.kind == "ask":
            self.question, self.asked_by = action.question, passes[-1]
        closing = self._routed(action.call, "model", ms=ms)
        if action.kind == "ask":
            closing["label"] = "Asking you first"
        # The choice cost passes, and a pass is counted in tokens.
        closing["tokens_in"] = _total(said.prompt_tokens for said in passes)
        closing["tokens_out"] = _total(said.completion_tokens for said in passes)
        yield "step", closing

    def _routed(self, call: ToolCall | None, tier: str, *, ms: float) -> dict:
        """The step that says what was chosen, and by which tier."""
        tool = REGISTRY[call.name] if call else None
        return {
            "id": "route", "kind": "route", "status": "ok",
            "label": tool.label if tool else "No tool needed",
            "detail": tier,
            "glyph": tool.glyph if tool else "◇",
            "tool": call.name if call else None,
            "ms": ms,
        }

    async def _decide(self, tools: list) -> tuple[Action, list[Completion]]:
        """Ask the model which tool, if any. Returns what it chose and every
        pass that took.

        Usually that is one pass. If the call it chose is one the tool cannot
        take, it is told why and asked once more, and whatever it says then is
        what the run goes on with: a call that can be made, no call at all, or
        a call that still cannot be made. That last one is not argued with a
        second time, and it is not made: it is shown to the visitor as a call
        that was refused, with the reason.
        """
        messages = decision_messages(self.message, tools, self._asked())
        action, said = await self._choose("route", messages, tools, {"text": messages[-1]["content"]})
        passes = [said]
        if action.kind != "tool" or self.stop.is_set():
            return action, passes
        rejection = self._refusal(action.call)
        if rejection is None:
            return action, passes

        # The step just written down chose a call that was not made.
        self.trace.steps[-1].ok = False
        self.trace.steps[-1].error = "rejected: " + "; ".join(rejection.reasons)
        action, said = await self._choose(
            "repair", repair_messages(messages, said.raw, rejection), tools,
            {"rejected": list(rejection.reasons)},
        )
        if action.kind == "tool" and not self.stop.is_set():
            still = self._refusal(action.call)
            if still is not None:
                self.trace.steps[-1].ok = False
                self.trace.steps[-1].error = "rejected: " + "; ".join(still.reasons)
                # Carried with the call, so that it is refused and not run.
                action.call.meta["refusal"] = still
        return action, [*passes, said]

    def _asked(self) -> tuple[str, str] | None:
        """The exchange this message answers, if it answers a question the
        model asked: what the user had sent, and what they were asked back."""
        if not self.follows_ask or len(self.history) < 2:
            return None
        sent, question = self.history[-2], self.history[-1]
        if sent["role"] != "user" or question["role"] != "assistant":
            return None
        return sent["content"], question["content"]

    def _refusal(self, call: ToolCall) -> Rejection | None:
        """Why a call the model chose cannot be made as it stands, if it cannot.

        Two things can be wrong with it. The tool cannot take what it was
        given; or it was given an address to go out to that the user never
        wrote. The second is not a mistake of form, and the dispatcher would
        run it. It is refused here because of where the address came from:
        the only other place the model could have got one is something it
        read.
        """
        tool = REGISTRY[call.name]
        clean, rejection = check(tool, call.args)
        if rejection is not None:
            return rejection
        theirs = [m["content"] for m in self.history if m["role"] == "user"] + [self.message]
        for arg in tool.args:
            value = clean.get(arg.name)
            if arg.quoted and value is not None and not was_given(value, theirs):
                return Rejection(
                    (f"{arg.name} must be an address the user gave; got {value!r}",),
                    f"{tool.label} only goes to an address you gave it yourself.",
                    "not allowed",
                )
        return None

    async def _choose(
        self, kind: str, messages: list[dict], tools: list, given: dict
    ) -> tuple[Action, Completion]:
        """One pass that chooses, read as an action and written down whole."""
        said = await self.engine.complete_json(
            messages, decision_grammar(tools), decision_limit(tools),
            request_id=self.request_id, seed=self.seed,
        )
        action = parse_action(said)
        stopped = said.cancelled or self.stop.is_set()
        # What went wrong, if anything did: it was stopped, the pass failed, or
        # what it said could not be used.
        error = "stopped" if stopped else (said.error or action.note)
        # A pass that never reached the model still took the time it took.
        cost = said.ms if said.total_ms is None else _pass_ms(said.queue_wait_ms, said.total_ms)
        self.trace.steps.append(Step(
            kind, model=True, name=action.call.name if action.call else None, via="model",
            input=given, output=said.raw,
            prompt_tokens=said.prompt_tokens, output_tokens=said.completion_tokens,
            queue_ms=said.queue_wait_ms, first_token_ms=said.ttft_ms, total_ms=cost,
            ok=error is None, error=error,
        ))
        return action, said

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
        refusal: Rejection | None = call.meta.get("refusal")
        if refusal is not None:
            # A call the model chose that was refused and stayed refused. It
            # is not made. What the visitor sees is the refusal.
            result = ToolResult(False, refusal.summary, error=refusal.code)
        else:
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
            # A failed tool is shown, not hidden, and the model is told as
            # well: left to think nothing was tried, it answers from memory
            # beside a card that says the lookup failed.
            self.failures.append(f"{call.name}: {what_happened(result)}")
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
            content = build_prompt(self.skill, self.message, self.summaries, self.failures)
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
            )
            if self.failures:
                content += "A lookup was also tried and did not work:\n" + did_not_work(self.failures) + "\n\n"
            content += (
                "Answer the question using only the data above. It is "
                "current and correct. Do not say you lack real-time "
                "access, and do not recalculate any figure."
            )
            if self.failures:
                content += " " + SAY_SO
        elif self.failures:
            content = (
                f"{self.message}\n\n"
                "A lookup was tried for this and did not work:\n"
                + did_not_work(self.failures) + "\n\n" + SAY_SO
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
        self.trace.prompt = fitted
        async with aclosing(
            self.engine.stream_chat(fitted, sampling, self.request_id, seed=self.seed)
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
