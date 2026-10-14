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
import json
import re
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from .config import settings
from .context import (
    RUN_ROOM, SUMMARY_TOKENS, Part, Turn, Window, cap, fit, key, turns_from_history,
)
from .excerpt import estimate
from .llm import Completion, Engine, Stop, engine as default_engine
from .personas import Persona
from .router import (
    URL_RE,
    Action,
    ToolCall,
    decision_grammar,
    decision_limit,
    fill_request,
    free_route,
    parse_action,
)
from .skills import SAY_SO, Skill, did_not_work, prompt_parts
from .tools import REGISTRY, call_tool as default_call_tool
from .tools.base import (
    Rejection, ToolResult, args_ceiling, args_to_gbnf, available, check, check_args, fresh_for,
    was_given,
)
from .transcript import Transcript, system_for

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

    kind: str  # route | fill | decide | repair | tool | answer
    # Whether this step was a pass through the model.
    model: bool = False
    # The tool or skill, where there is one.
    name: str | None = None
    # How it came to be chosen: slash | heuristic | skill | model.
    via: str | None = None
    input: dict | None = None
    output: str | None = None
    prompt_tokens: int | None = None
    # Prompt tokens the model had to read, the rest being in its cache. Only a
    # model server reports this, and it is never guessed at.
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
    # What this run sent, from the message to the answer, and the system text
    # it was sent under, by its key: what the next message is read with. Set
    # once the run has answered, or asked.
    sent: list[Part] | None = None
    system_key: str | None = None
    # How it ended: answered | asked | stopped | failed. None while it is
    # running. "asked" is a question put to the user in place of an answer.
    outcome: str | None = None
    # The run's own stopwatch, first stage to last. It is a little more than
    # the steps add up to: the difference is the time between them.
    wall_ms: float | None = None

    def turn(self, question: str, at: float | None = None) -> Turn:
        """This run as the next message is read with it: what it sent, where
        it answered or asked, and otherwise only the message and the answer."""
        kept = self.sent if self.outcome in ("answered", "asked") else None
        return Turn(
            question, self.answer or "", parts=tuple(kept or ()), system_key=self.system_key,
            at=time.time() if at is None else at,
        )

    def totals(self) -> dict:
        """The run in a line: what the page is told when it ends.

        The total is split four ways, and the four add up to it: waiting for
        the slot, the model reading, the model writing, and tools.
        """
        passes = [s for s in self.steps if s.model]
        waiting = sum(s.queue_ms or 0.0 for s in passes)
        reading = sum(s.first_token_ms or 0.0 for s in passes)
        tools = sum(s.total_ms for s in self.steps if not s.model)
        total = sum(s.total_ms for s in self.steps)
        return {
            "id": self.run_id,
            "outcome": self.outcome,
            "steps": len(self.steps),
            "passes": len(passes),
            "total_ms": round(total, 1),
            "waiting_ms": round(waiting, 1),
            "reading_ms": round(reading, 1),
            # What is left of the passes once the waiting and reading are out.
            "writing_ms": round(max(total - tools - waiting - reading, 0.0), 1),
            "tools_ms": round(tools, 1),
            "wall_ms": None if self.wall_ms is None else round(self.wall_ms, 1),
        }


# Where one sentence ends and the next begins.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

# What the answer pass is told when the looking-up was ended for it and not by
# its own choice. One plain line each, so the reply can be honest about what
# was not found.
OUT_OF_LOOKUPS = "No more lookups were possible; say what is still unknown."
ALREADY_LOOKED_UP = (
    "That lookup had already been made and was not repeated; answer from what is above."
)

# The least a lookup in the loop is given to read, however little room is
# left: a line.
LEAST_SHARE = 40


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
    loop: bool | None = None,
    context: list[Turn] | None = None,
) -> AsyncIterator[Event]:
    """Answer one message. Yields the events of the run as they happen.

    ``history`` is the conversation before this message, oldest first, as
    ``{"role", "content"}``. The stream ends in ``done`` or in ``error``.

    ``request_id`` is the name the run can be stopped by, from its first stage
    to its last: ``engine.cancel(request_id)``. It is the trace's ``run_id``;
    given here, it replaces the one the trace made up.

    ``seed`` is given to every pass of the run, so that the same message
    against the same model gives the same run. The site leaves it unset.

    ``loop`` lets a tool the model chose earn it another choice, within the
    limits in the settings. Without it the model chooses once, and the run
    answers after that one lookup. Left unset, the setting decides.

    ``context`` is the conversation as it was sent, turn by turn, which is
    what the model reads of it. ``history`` stays what was said, and only
    that: it is what decides which addresses the user gave. Without
    ``context`` the conversation is read from ``history``.

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
        loop=settings.agent_loop if loop is None else loop,
        turns=context,
        max_steps=settings.agent_max_steps,
        max_decisions=settings.agent_max_decisions,
        max_seconds=settings.agent_max_seconds,
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
    # Whether a tool the model chose earns it another choice, and the limits
    # on that for one message.
    loop: bool = True
    max_steps: int = 3
    max_decisions: int = 3
    max_seconds: float = 90.0
    # Set once the tools for this message have been chosen without the model:
    # by a skill, a command or a pattern.
    routed: bool = False
    # What the last tool call came to; None if it was stopped. And what the
    # model reads of it, when it worked.
    last: ToolResult | None = None
    seen: str = ""
    # Time spent waiting for the slot that no pass has yet been charged for.
    slot_wait: float = 0.0
    # A question the model chose to ask in place of an answer, and the pass
    # that wrote it.
    question: str | None = None
    asked_by: Completion | None = None
    # The calls this message was routed to, and the line each successful one
    # gave back for the model to read, with the tool it came from.
    calls: list[ToolCall] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    found: list[str] = field(default_factory=list)
    # And what each one that failed is known to have done, for the same reader.
    failures: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    # The conversation so far, as turns. Left unset, it is made from
    # ``history``, which stays what the user and the model said and nothing
    # else: what the address rule reads.
    turns: list[Turn] | None = None
    # The message as the model reads it: cut to fit the window, if it had to
    # be, and whether it was. And what was cut from the conversation to make
    # room, as the page is told it.
    said: str = ""
    cut: bool = False
    trim: dict | None = None
    trim_told: bool = False
    # Set while the run holds the slot for every pass it makes, outside the
    # loop: a skill whose inputs the model filled in.
    holding: bool = False
    # What was sent to have them filled in, and what the model wrote.
    filled: list[Part] | None = None

    def __post_init__(self) -> None:
        self.said = self.said or self.message
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
        if self.turns is None:
            self.turns = turns_from_history(self.history)
        self.said, self.cut = self._cap()
        try:
            # What costs nothing comes first: a skill, a command, a pattern.
            async for event in self._free():
                yield event
            # What is left is the model's to decide, where the persona has tools.
            tools = available() if (self.persona.tools and not self.routed) else []
            if tools:
                async with aclosing(self._looping(tools)) as events:
                    async for event in events:
                        yield event
                return
            for index, call in enumerate(self.calls):
                if self.stop.is_set():
                    break
                # A run holding the slot lends it out while a tool is away.
                acting = self._lookup(index, call) if self.holding else self._act(index, call)
                async for event in acting:
                    yield event
            if self.stop.is_set():
                yield self._stopped()
                return
            system, now = self._system(), self._prompt()
            window = self._window(system, now, run=False)
            if self._trimmed(window):
                yield "trimmed", self.trim
            self._sending(system, now)
            async with aclosing(self._answer(window.messages, held=self.holding)) as events:
                async for event in events:
                    yield event
        finally:
            if self.holding:
                self.engine.give_slot(self.request_id)

    # ------------------------------------------------------------ the window --

    def _system(self) -> str:
        """The system text of every pass of this run. A persona with tools
        reads the same one whether the model chooses or something else did,
        so a conversation keeps one system text from message to message."""
        tools = available() if self.persona.tools and self.tools_enabled else []
        return system_for(self.persona, tools) if tools else self.persona.system

    def _room(self, *, run: bool) -> int:
        """Tokens the prompt may take: the window, less the reply and the
        headroom above it, and less the room for a run's own additions where
        ``run`` says there is one."""
        cfg = self.engine.cfg
        return cfg.n_ctx - self.persona.max_tokens - cfg.reply_headroom - (RUN_ROOM if run else 0)

    def _cap(self) -> tuple[str, bool]:
        """The message cut to what the window can give it beside the system
        text, a summary of older turns and, for a persona with tools, the
        room for a run, if it is longer. Counted only when it might be: no
        message has more tokens than bytes."""
        system = self._system()
        tools = system != self.persona.system
        limit = self._room(run=tools) - self.engine.count_tokens(system) - SUMMARY_TOKENS
        if len(self.message.encode("utf-8")) <= limit:
            return self.message, False
        return cap(self.message, max(1, limit), self.engine.count_tokens)

    def _window(self, system: str, now: list[Part], *, run: bool) -> Window:
        """The prompt for this message under ``system``: the conversation so
        far, fitted to the room, then ``now``."""
        return fit(
            system, self.turns or [], now, room=self._room(run=run),
            count=self.engine.count_tokens, now=time.time(), fresh_for=fresh_for,
        )

    def _sending(self, system: str, parts: list[Part]) -> None:
        """Keep what this run is sending of its own, under ``system``. The
        answer is added once it is written."""
        self.trace.sent = list(parts)
        self.trace.system_key = key(system)

    def _trimmed(self, window: Window) -> bool:
        """Whether anything had to give for the prompt to fit that the page
        has not been told of yet. What it was is kept in ``trim`` for the
        page and the trace."""
        if not window.summarised and not self.cut:
            return False
        dropped = sum(bool(t.question) + bool(t.answer) for t in (self.turns or [])[:window.summarised])
        self.trim = {
            "dropped": dropped, "turns": window.turns, "summarised": window.summarised,
            "cut": self.cut,
        }
        told, self.trim_told = self.trim_told, True
        return not told

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
            "read_tokens": said.read_tokens,
            "cached_tokens": said.cached_tokens,
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
            "read_tokens": None,
            "cached_tokens": None,
            "completion_tokens": 0,
            "prefill_tps": None,
            "gen_tps": None,
            "total_ms": None,
            "rss_mb": None,
            "tool_calls": len(self.trace.calls),
        }
        return "done", self.trace.usage

    # ------------------------------------------------------------ routing --

    async def _free(self) -> AsyncIterator[Event]:
        """The choices that cost nothing. Sets ``routed`` if one of them was
        made, or if there is no choosing to do at all."""
        if self.skill is not None:
            self.routed = True
            values = self.skill.read(self.message)
            closing = {
                "id": "route", "kind": "route", "status": "ok",
                "label": self.skill.name, "detail": "skill",
                "glyph": self.skill.glyph, "tool": self.skill.id, "ms": 0,
            }
            if self._fillable(values):
                # The slot is taken once for the fill, the lookups and the
                # answer, so the answer is written on from what the fill read.
                self.holding = True
                async for event in self._take():
                    yield event
                if self.stop.is_set():
                    return
                # A pass is about to be made, so one is announced.
                yield "step", {
                    "id": "route", "kind": "route", "status": "running",
                    "label": self.skill.name, "glyph": self.skill.glyph,
                }
                window = self._window(self._system(), self._fill_parts(), run=False)
                if self._trimmed(window):
                    yield "trimmed", self.trim
                values, said = await self._fill(window.messages, values)
                if self.stop.is_set():
                    yield "step", {**closing, "status": "stopped", "ms": round(said.ms, 1)}
                    return
                closing.update(
                    ms=round(said.ms, 1), tokens_in=said.prompt_tokens,
                    tokens_out=said.completion_tokens,
                )
            self.calls = self.skill.calls(values)
            self.trace.steps.append(Step(
                "route", name=self.skill.id, via="skill",
                input={"calls": [{"tool": c.name, "args": c.args} for c in self.calls]},
            ))
            yield "step", closing
            return
        if not self.tools_enabled:
            self.routed = True
            return

        # A command works for every persona; a pattern only where the persona
        # has tools.
        call, tier = free_route(self.message, tools_enabled=self.persona.tools)
        if call is not None:
            self.routed = True
            self.calls = [call]
            self.trace.steps.append(Step(
                "route", name=call.name, via=tier, input=call.args, output=call.note,
            ))
            yield "step", self._routed(call, tier, ms=0)

    def _fillable(self, values: dict[str, str]) -> bool:
        """Whether to ask the model for the inputs a visitor's skill did not
        find in their message. Only where the skill has a pattern that missed:
        the whole message is the one input of a skill without one. Never for
        an address, which the model may not supply."""
        missing = self.skill.missing(values)
        return bool(
            missing and self.skill.find is not None and self.said.strip()
            and not all(arg.quoted for arg in missing)
        )

    def _fill_parts(self) -> list[Part]:
        """The message, and the request for the skill's inputs after it."""
        return [
            Part("user", "question", self.said),
            Part("user", "instruction", fill_request(self.skill.name, self.skill.inputs)),
        ]

    async def _fill(
        self, messages: list[dict], values: dict[str, str]
    ) -> tuple[dict[str, str], Completion]:
        """One short pass for the skill's inputs, held to a grammar of them.

        What it says is checked like a call: of the right kind, each one
        there, and an address only if the user wrote it. If it holds, it is
        what the skill runs with, and the answer is written on from it; if
        not, the skill runs with what its pattern found, or the whole
        message.
        """
        inputs = self.skill.inputs
        said = await self.engine.complete_json(
            messages, args_to_gbnf(inputs), args_ceiling(inputs),
            request_id=self.request_id, seed=self.seed, held=True,
        )
        clean: dict = {}
        if said.parsed is None:
            reasons: tuple[str, ...] = ("the reply was not a complete JSON object",)
        else:
            clean, rejection = check_args(self.skill.name, inputs, said.parsed)
            reasons = rejection.reasons if rejection else ()
            # Text with nothing in it is not an input given.
            reasons += tuple(
                f"{arg.name} is required and was not given"
                for arg in self.skill.missing({k: str(v).strip() for k, v in clean.items()})
                if arg.name in clean
            )
            theirs = [m["content"] for m in self.history if m["role"] == "user"] + [self.message]
            reasons += tuple(
                f"{arg.name} must be an address the user gave; got {clean[arg.name]!r}"
                for arg in inputs
                if arg.quoted and clean.get(arg.name) and not was_given(clean[arg.name], theirs)
            )
        stopped = said.cancelled or self.stop.is_set()
        error = "stopped" if stopped else (said.error or ("; ".join(reasons) or None))
        self.trace.steps.append(self._passed(
            "fill", said, name=self.skill.id, given={"text": self.said}, error=error,
        ))
        if error is not None:
            return values, said
        self.filled = [*self._fill_parts(), Part("assistant", "action", said.raw)]
        return {name: str(value) for name, value in clean.items()}, said

    def _passed(
        self, kind: str, said: Completion, *, name: str | None, given: dict, error: str | None
    ) -> Step:
        """A pass through the model as a step, charged with any wait for the
        slot that no pass has paid for yet."""
        # A run that holds the slot waited for it before this pass, not in it.
        # That wait is charged to the pass it was waited for.
        waited, self.slot_wait = self.slot_wait, 0.0
        queued = None if said.queue_wait_ms is None and not waited else (said.queue_wait_ms or 0.0) + waited
        # A pass that never reached the model still took the time it took.
        cost = said.ms if said.total_ms is None else _pass_ms(said.queue_wait_ms, said.total_ms)
        return Step(
            kind, model=True, name=name, via="model", input=given, output=said.raw,
            prompt_tokens=said.prompt_tokens, new_tokens=said.read_tokens,
            output_tokens=said.completion_tokens,
            queue_ms=queued, first_token_ms=said.ttft_ms, total_ms=cost + waited,
            ok=error is None, error=error,
        )

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
        self, kind: str, messages: list[dict], tools: list, given: dict, *, held: bool = False
    ) -> tuple[Action, Completion]:
        """One pass that chooses, read as an action and written down whole."""
        said = await self.engine.complete_json(
            messages, decision_grammar(tools), decision_limit(tools),
            request_id=self.request_id, seed=self.seed, held=held,
        )
        action = parse_action(said)
        stopped = said.cancelled or self.stop.is_set()
        # What went wrong, if anything did: it was stopped, the pass failed, or
        # what it said could not be used.
        error = "stopped" if stopped else (said.error or action.note)
        self.trace.steps.append(self._passed(
            kind, said, name=action.call.name if action.call else None, given=given, error=error,
        ))
        return action, said

    # ------------------------------------------------------------- the loop --

    async def _looping(self, tools: list) -> AsyncIterator[Event]:
        """Choose, act, read what came back, and choose again, within limits.

        The run takes the slot once and keeps it, and every pass reads the one
        transcript, so each pass after the first reads only what is new.

        The loop always ends. The model ends it by saying it needs no more; if
        it does not, a limit does: so many tool calls, so many choices, so
        many seconds. A call it has already made is not made again, which is
        how a small model's commonest loop is cut. When something other than
        the model ends the looking-up, the answer pass is told so in a line,
        so that the reply can say what was not found.

        With the loop off it is the same transcript and the same passes, cut
        short: one choice, sent back once if it cannot be made, at most one
        lookup, and then the answer.
        """
        single = not self.loop
        transcript = self._transcript(tools)
        if self.trim:
            yield "trimmed", self.trim
        start = estimate(transcript.rendered())
        made: set[str] = set()
        decisions = steps = 0
        began = time.perf_counter()
        note: str | None = None
        try:
            async for event in self._take():
                yield event
            while not self.stop.is_set():
                if (decisions >= self.max_decisions or steps >= self.max_steps
                        or time.perf_counter() - began >= self.max_seconds):
                    # Not the first time round: a first choice is always allowed.
                    if decisions:
                        note = OUT_OF_LOOKUPS
                        break
                number = decisions + 1
                step_id = "route" if number == 1 else f"route-{number}"
                yield "step", {
                    "id": step_id, "kind": "route", "status": "running",
                    "label": "Choosing a tool", "glyph": "◇", "pass": number,
                }
                action, passes = await self._decision(
                    transcript, tools, first=number == 1,
                    room=2 if single else self.max_decisions - decisions,
                )
                decisions += len(passes)
                ms = round(sum(said.ms for said in passes), 1)
                if self.stop.is_set():
                    yield "step", {
                        "id": step_id, "kind": "route", "status": "stopped",
                        "label": "Choosing a tool", "glyph": "◇", "ms": ms, "pass": number,
                    }
                    break
                closing = self._routed(action.call, "model", ms=ms)
                closing.update(
                    id=step_id, **{"pass": number},
                    tokens_in=_total(said.prompt_tokens for said in passes),
                    tokens_out=_total(said.completion_tokens for said in passes),
                )
                if action.kind == "ask":
                    closing["label"] = "Asking you first"
                    self.question, self.asked_by = action.question, passes[-1]
                    yield "step", closing
                    # What the model wrote, the question in it, is the reply.
                    self._sending(transcript.parts()[0].content, transcript.run_parts())
                    for event in self._ask():
                        yield event
                    return
                yield "step", closing
                if action.kind != "tool":
                    break  # the model has what it needs
                key = self._key(action.call)
                if key in made:
                    note = ALREADY_LOOKED_UP
                    break
                made.add(key)
                self.calls.append(action.call)
                # An even share of the room the run has left, so that the
                # last lookup it may make has as much to read as the first.
                left = RUN_ROOM - (estimate(transcript.rendered()) - start)
                share = max(LEAST_SHARE, left // (1 if single else self.max_steps - steps))
                async for event in self._lookup(steps, action.call, budget=share):
                    yield event
                steps += 1
                if self.last is None:
                    break  # stopped while the tool was out
                transcript.observation(
                    action.call.name,
                    self.seen if self.last.ok else what_happened(self.last),
                    ok=self.last.ok,
                )
                if single:
                    break  # one lookup, and the answer
            if self.stop.is_set():
                yield self._stopped()
                return
            transcript.answer_cue(note)
            self._sending(transcript.parts()[0].content, transcript.run_parts())
            async with aclosing(self._answer(transcript.messages(), held=True)) as events:
                async for event in events:
                    yield event
        finally:
            # On every way out, however it ends. If a pass of this run is
            # still inside the model the slot goes back when it leaves.
            self.engine.give_slot(self.request_id)

    def _transcript(self, tools: list) -> Transcript:
        """The run's one prompt, with as much of the conversation as leaves
        room for the run. What had to give for it is kept in ``trim``."""
        window = self._window(
            system_for(self.persona, tools), [Part("user", "question", self.said)], run=True
        )
        self._trimmed(window)
        return Transcript.fitted(window.parts)

    async def _take(self) -> AsyncIterator[Event]:
        """Take the slot for the whole run, passing on the notices of its
        place in the queue. If the run is stopped while it waits, the slot is
        not taken and the caller finds the run stopped."""
        async with aclosing(self.engine.take_slot(self.request_id)) as waiting:
            async for name, payload in waiting:
                if name == "queue":
                    yield name, payload
                else:
                    self.slot_wait += payload["queue_wait_ms"]

    async def _decision(
        self, transcript: Transcript, tools: list, *, first: bool, room: int
    ) -> tuple[Action, list[Completion]]:
        """One choice in the loop, read from the transcript and added to it.

        Like a single choice, it may be sent back once if the call cannot be
        made. A second try is itself a pass that chooses, so it is only made
        while there is ``room`` for one; a call still refused is carried on as
        refused and never run.
        """
        given = {"text": self.message} if first else {"read": transcript.messages()[-1]["content"]}
        action, said = await self._choose(
            "route" if first else "decide", transcript.messages(), tools, given, held=True
        )
        passes = [said]
        if said.raw:
            transcript.action(said.raw)
        if action.kind != "tool" or self.stop.is_set():
            return action, passes
        rejection = self._refusal(action.call)
        if rejection is None:
            return action, passes

        self.trace.steps[-1].ok = False
        self.trace.steps[-1].error = "rejected: " + "; ".join(rejection.reasons)
        if room < 2:
            action.call.meta["refusal"] = rejection
            return action, passes
        transcript.rejection(rejection.reasons)
        action, said = await self._choose(
            "repair", transcript.messages(), tools, {"rejected": list(rejection.reasons)}, held=True
        )
        passes.append(said)
        if said.raw:
            transcript.action(said.raw)
        if action.kind == "tool" and not self.stop.is_set():
            still = self._refusal(action.call)
            if still is not None:
                self.trace.steps[-1].ok = False
                self.trace.steps[-1].error = "rejected: " + "; ".join(still.reasons)
                action.call.meta["refusal"] = still
        return action, passes

    def _key(self, call: ToolCall) -> str:
        """A call as it is compared with the ones already made: the tool, and
        its arguments as the tool would be run with them, without regard to
        case or spacing. "Paris" and " paris" are one lookup."""
        clean, _ = check(REGISTRY[call.name], call.args)
        plain = {
            name: " ".join(value.split()).casefold() if isinstance(value, str) else value
            for name, value in clean.items()
        }
        return call.name + " " + json.dumps(plain, sort_keys=True, ensure_ascii=False)

    async def _lookup(
        self, index: int, call: ToolCall, *, budget: int | None = None
    ) -> AsyncIterator[Event]:
        """One tool call of a run that is holding the slot.

        A tool that works here takes milliseconds, and the slot is kept. One
        that goes out can take ten seconds with the model idle. If anybody is
        waiting, the slot is lent to them for that time, and the run takes its
        place in the queue again afterwards. The price is that the model may
        have to read this run's prompt again, and it is paid only when
        somebody did in fact go in.
        """
        tool = REGISTRY.get(call.name)
        lend = bool(
            tool and tool.network and "refusal" not in call.meta and self.engine.others_waiting()
        )
        if lend:
            self.engine.give_slot(self.request_id)
        async for event in self._act(index, call, budget=budget):
            yield event
        if lend and not self.stop.is_set():
            async for event in self._take():
                yield event

    # ---------------------------------------------------------- one tool --

    async def _act(
        self, index: int, call: ToolCall, *, budget: int | None = None
    ) -> AsyncIterator[Event]:
        """Make one call and say what came of it. What the model reads of a
        result that worked is kept in ``seen``: within ``budget`` tokens if
        given, and the tool's own budget in any case."""
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
        self.last = result
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
            self.seen = (
                tool.observation(result, self.purpose, budget) if tool else result.summary
            )
            self.summaries.append(f"{call.name}: {self.seen}")
            self.found.append(call.name)
            yield "card", {
                "step_id": step_id, "card": result.card,
                "tool": call.name, "data": result.data,
            }
        else:
            # A failed tool is shown, not hidden, and the model is told as
            # well: left to think nothing was tried, it answers from memory
            # beside a card that says the lookup failed.
            self.failures.append(f"{call.name}: {what_happened(result)}")
            self.lost.append(call.name)
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

    @property
    def purpose(self) -> str:
        """What a result is read for: the message, without any address in it.
        An address says where to look, not what to look for."""
        return " ".join(URL_RE.sub(" ", self.message).split())

    def _prompt(self) -> list[Part]:
        """What this message is read as, after the conversation so far: the
        message, what was looked up for it and how to use that, as parts."""
        question = Part("user", "question", self.said)
        if self.skill is not None:
            return [*(self.filled or ()), *prompt_parts(
                self.skill, self.said, self.summaries, self.failures,
                tuple(dict.fromkeys(self.found + self.lost)),
            )]
        if self.summaries:
            # The tool output goes inside the user turn, not into a second
            # system message. A 1.5B model barely attends to a trailing system
            # message and falls back on its "I don't have real-time data"
            # prior — it will describe the weather as unavailable while the
            # card beside it shows today's temperature. Stated as part of the
            # question, it complies.
            facts = "\n".join(f"- {s}" for s in self.summaries)
            parts = [question, Part(
                "user", "data", f"Live data, retrieved just now by a tool:\n{facts}",
                tuple(dict.fromkeys(self.found)),
            )]
            if self.failures:
                parts.append(Part(
                    "user", "failed",
                    "A lookup was also tried and did not work:\n" + did_not_work(self.failures),
                    tuple(dict.fromkeys(self.lost)),
                ))
            instruction = (
                "Answer the question using only the data above. It is "
                "current and correct. Do not say you lack real-time "
                "access, and do not recalculate any figure."
            )
            if self.failures:
                instruction += " " + SAY_SO
            return [*parts, Part("user", "instruction", instruction)]
        if self.failures:
            return [question, Part(
                "user", "failed",
                "A lookup was tried for this and did not work:\n" + did_not_work(self.failures),
                tuple(dict.fromkeys(self.lost)),
            ), Part("user", "instruction", SAY_SO)]
        return [question]

    # ---------------------------------------------------------- the answer --

    async def _answer(self, messages: list[dict], *, held: bool = False) -> AsyncIterator[Event]:
        sampling = self.persona.sampling()
        # Fitted to the window already. The engine's own fitting is kept as
        # the last guard: a run that added more than the room it was given.
        fitted, _, dropped = self.engine.fit_context(messages, sampling["max_tokens"])
        if dropped:
            self.trim = {**(self.trim or {"cut": self.cut}), "dropped": dropped}
            yield "trimmed", {"dropped": dropped}
        # Kept with the step: a prompt that lost its oldest turns, or the end
        # of its message, is not the prompt the conversation suggests.
        trimmed = self.trim

        yield "step", {
            "id": "answer", "kind": "model", "status": "running",
            "label": "Writing the answer", "glyph": "✦",
        }
        self.trace.prompt = fitted
        waited, self.slot_wait = self.slot_wait, 0.0
        async with aclosing(
            self.engine.stream_chat(fitted, sampling, self.request_id, seed=self.seed, held=held)
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
                queued = data.get("queue_wait_ms")
                self.trace.steps.append(Step(
                    "answer", model=True, input=trimmed,
                    prompt_tokens=data.get("prompt_tokens"),
                    new_tokens=data.get("read_tokens"),
                    output_tokens=data.get("completion_tokens"),
                    queue_ms=queued if queued is None and not waited else (queued or 0.0) + waited,
                    first_token_ms=data.get("ttft_ms"),
                    total_ms=_pass_ms(queued, data.get("total_ms")) + waited,
                    ok=not stopped, error="stopped" if stopped else None,
                ))
                self.trace.outcome = "stopped" if stopped else "answered"
                self.trace.answer = data.pop("text", "")
                if self.trace.sent is not None:
                    self.trace.sent.append(Part("assistant", "answer", self.trace.answer))
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
