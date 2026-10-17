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
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from zoneinfo import ZoneInfo
from typing import AsyncIterator, Awaitable, Callable

from ..config import settings
from ..llm import Completion, Engine, Stop, engine as default_engine
from ..llm.usage import blank
from ..projects.parse import FileBlocks
from ..text import estimate, first_sentence
from ..tools import REGISTRY, call_tool as default_call_tool
from ..tools.base import (
    CALL, CallContext, Link, Rejection, ToolResult, available, fresh_for, untrusted,
)
from ..tools.grammar import PROGRAM, PROGRAM_TOKENS
from ..tools.results import links_of
from .context import (
    SUMMARY_TOKENS, Part, Turn, Window, cap, fit, key, run_room, turns_from_history,
)
from .policy import call_key, refusal, users_words
from .route import (
    NEWS,
    URL_RE,
    Action,
    ToolCall,
    decision_grammar,
    decision_limit,
    free_route,
    news_topic,
    parse_action,
    program_of,
    search_query,
    time_matters,
)
from .skills import SAY_SO, Offer, Skill, check_inputs, did_not_work, offered
from .style import Style, style_for
from .trace import Step, Trace, pass_ms, total
from .transcript import CUE_PLAIN, CUE_WITH_DATA, Transcript, sent_at, system_for
from .verify import caption, denies_access, note, scalars
from .verify import check as check_figures

Event = tuple[str, dict]
ToolCaller = Callable[[str, dict], Awaitable[ToolResult]]


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

# How much of an answer is watched for a claim that it cannot look anything
# up, in characters. Such a claim comes first; past this, the answer stands.
WATCHED = 400


def what_happened(result: ToolResult) -> str:
    """A failed call as one fact, for the model to read.

    The sentence a failure is given for a person often goes on to advise:
    "Try again", "Try adding a country". A small model reading that in a tool
    result takes it as an order and acts on it, so only what happened is
    passed on: the first sentence.
    """
    return first_sentence((result.summary or result.error or "It did not work.").strip())


def run(
    message: str,
    *,
    history: list[dict],
    style: Style | None = None,
    trace: Trace | None = None,
    engine: Engine | None = None,
    call_tool: ToolCaller | None = None,
    tools_enabled: bool | None = None,
    request_id: str | None = None,
    seed: int | None = None,
    loop: bool | None = None,
    context: list[Turn] | None = None,
    scope: str | None = None,
    now: datetime | None = None,
    links: tuple[Link, ...] = (),
    project: list[dict] | None = None,
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

    ``scope`` is the conversation the run belongs to: the one whose notes a
    note tool keeps and reads.

    ``links`` is the last list of results the conversation was shown, what
    "open the second one" means until the run is shown another.

    ``project`` is the conversation's project as it stands, file by file
    (``store.projects.manifest``): what the model is told is there.

    ``style`` is how the answer is written; left unset, it is read from the
    message (``style.style_for``). ``now`` is when the message was sent, as
    the model is told it; left unset, it is now, in the site's timezone.

    The last event is always ``run``: how it ended, how many steps and passes
    it took, and what they cost together.
    """
    trace = trace if trace is not None else Trace()
    if request_id:
        trace.run_id = request_id
    return _Run(
        message=message,
        style=style if style is not None else style_for(message),
        history=history,
        trace=trace,
        engine=engine if engine is not None else default_engine,
        call_tool=call_tool if call_tool is not None else default_call_tool,
        tools_enabled=settings.tools_enabled if tools_enabled is None else tools_enabled,
        request_id=trace.run_id,
        seed=seed,
        loop=settings.agent_loop if loop is None else loop,
        turns=context,
        scope=scope,
        now=now if now is not None else datetime.now(ZoneInfo(settings.timezone)),
        links=tuple(links),
        project=list(project or ()),
        max_steps=settings.agent_max_steps,
        max_decisions=settings.agent_max_decisions,
        max_seconds=settings.agent_max_seconds,
    ).go()


@dataclass
class _Run:
    message: str
    style: Style
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
    # The conversation the run belongs to, whose notes it may keep and read.
    scope: str | None = None
    # Set once the run has read text somebody other than the user wrote: a
    # page, an article, notes others kept. From then on it writes nothing the
    # model chose to write.
    tainted: bool = False
    # When the message was sent, as the model is told it.
    now: datetime | None = None
    # What an answer wrote before it was taken back for saying it could not
    # look something up; None while no answer has been.
    retracted: str | None = None
    # The conversation's project as it stands: each file's path and size.
    project: list[dict] = field(default_factory=list)
    # The last list of results the model was shown: the pages it may ask to
    # open by number, and addresses it may go to though the user never wrote
    # them, since a search, not a page, gave them.
    links: tuple[Link, ...] = ()

    def __post_init__(self) -> None:
        self.said = self.said or self.message

    @property
    def run_room(self) -> int:
        """What the run may add to its prompt: its choices and what its tools
        return. More in a larger window."""
        return run_room(self.engine.cfg.n_ctx)

    @property
    def addresses(self) -> list[str]:
        """Where the server may go: what the user wrote, and the pages of the
        last list of results."""
        return [*self.theirs, *(link.url for link in self.links)]

    @cached_property
    def theirs(self) -> list[str]:
        """What the user has written, this message last: where an address the
        server goes out to must be found."""
        return users_words(self.history, self.message)
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
        self.tainted = any(
            untrusted(REGISTRY[tool])
            for turn in self.turns for part in turn.parts
            if part.kind in ("data", "observation") for tool in part.tools if tool in REGISTRY
        )
        self.said, self.cut = self._cap()
        # What costs nothing comes first: a command or a pattern.
        async for event in self._free():
            yield event
        # What is left is the model's to decide.
        tools = available() if (self.tools_enabled and not self.routed) else []
        if tools:
            async with aclosing(self._looping(offered(tools))) as events:
                async for event in events:
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
        system, prompt = self._system(), self._prompt()
        window = self._window(system, prompt, run=False)
        if self._trimmed(window):
            yield "trimmed", self.trim
        self._sending(system, prompt)
        async with aclosing(self._answer(window.messages, parts=window.parts)) as events:
            async for event in events:
                yield event

    # ------------------------------------------------------------ the window --

    def _system(self) -> str:
        """The system text of every pass of this run. It is the same whether
        the model chooses or something else did, so a conversation keeps one
        system text from message to message."""
        return system_for(offered(available()) if self.tools_enabled else [])

    def _room(self, *, run: bool) -> int:
        """Tokens the prompt may take: the window, less the reply and the
        headroom above it, and less the room for a run's own additions where
        ``run`` says there is one."""
        cfg = self.engine.cfg
        return cfg.n_ctx - self.style.max_tokens - cfg.reply_headroom - (self.run_room if run else 0)

    def _cap(self) -> tuple[str, bool]:
        """The message cut to what the window can give it beside the system
        text, a summary of older turns and, where there are tools, the room
        for a run, if it is longer. Counted only when it might be: no message
        has more tokens than bytes."""
        system = self._system()
        limit = self._room(run=self.tools_enabled) - self.engine.count_tokens(system) - SUMMARY_TOKENS
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
        # The pass wrote JSON under a grammar. Its speed is not the speed of
        # an answer, and is not passed off as one: the rates stay blank.
        self.trace.usage = blank(
            asked=True,
            ttft_ms=said.ttft_ms,
            queue_wait_ms=said.queue_wait_ms,
            prompt_tokens=said.prompt_tokens,
            read_tokens=said.read_tokens,
            cached_tokens=said.cached_tokens,
            completion_tokens=said.completion_tokens or 0,
            total_ms=said.total_ms,
            tool_calls=0,
        )
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
        self.trace.usage = blank(cancelled=True, tool_calls=len(self.trace.calls))
        return "done", self.trace.usage

    # ------------------------------------------------------------ routing --

    async def _free(self) -> AsyncIterator[Event]:
        """The choices that cost nothing: a command, a pattern, or a message
        that needs no looking up at all. Sets ``routed`` if one of them was
        made, or if there is no choosing to do."""
        if not self.tools_enabled:
            self.routed = True
            return
        call, tier = free_route(self.message)
        if tier != "slash" and self.style.id == "project" and not self.project:
            # A new project is written, not looked up. Offered a choice, a
            # small model reaches for whatever is on the menu (the calculator,
            # a file that is not there yet), and a pattern would hear "a
            # weather app" as a question about the weather.
            call, tier = None, "chat"
        if tier is None:
            return
        self.routed = True
        self.calls = [call] if call is not None else []
        self.trace.steps.append(Step(
            "route", name=call.name if call else None, via=tier,
            input=call.args if call else None, output=call.note if call else None,
        ))
        yield "step", self._routed(call, tier, ms=0)

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
        cost = said.ms if said.total_ms is None else pass_ms(said.queue_wait_ms, said.total_ms)
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

    async def _choose(
        self, kind: str, messages: list[dict], tools: list, given: dict, *, held: bool = False
    ) -> tuple[Action, Completion]:
        """One pass that chooses, read as an action and written down whole.
        A tool for a project is on the menu but not in the grammar of a
        conversation that has none."""
        choices = [c for c in tools if self.project or not getattr(c, "project", False)]
        said = await self.engine.complete_json(
            messages, decision_grammar(choices), decision_limit(choices),
            request_id=self.request_id, seed=self.seed, held=held,
        )
        action = parse_action(said, {c.name: c.skill for c in tools if isinstance(c, Offer)})
        stopped = said.cancelled or self.stop.is_set()
        # What went wrong, if anything did: it was stopped, the pass failed, or
        # what it said could not be used.
        error = "stopped" if stopped else (said.error or action.note)
        name = (
            action.call.name if action.call
            else action.skill.id if action.skill
            else "ask" if action.kind == "ask"
            else None
        )
        self.trace.steps.append(self._passed(kind, said, name=name, given=given, error=error))
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
        own: str | None = None
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
                    tokens_in=total(said.prompt_tokens for said in passes),
                    tokens_out=total(said.completion_tokens for said in passes),
                )
                if action.kind == "skill":
                    closing.update(
                        label=action.skill.name, glyph=action.skill.glyph, tool=action.skill.id,
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
                if action.kind == "skill":
                    skill: Skill = action.skill
                    own = skill.cue
                    async for event in self._skill_lookups(skill, action.values, transcript, made,
                                                           steps, start, single):
                        yield event
                    break  # a skill's lookups are all it looks up
                if action.kind != "tool":
                    break  # the model has what it needs
                key = call_key(action.call)
                if key in made:
                    note = ALREADY_LOOKED_UP
                    break
                made.add(key)
                self.calls.append(action.call)
                if self._needs_program(action.call):
                    async for event in self._compose(transcript, action.call, number):
                        yield event
                    if self.stop.is_set():
                        break
                # An even share of the room the run has left, so that the
                # last lookup it may make has as much to read as the first.
                left = self.run_room - (estimate(transcript.rendered()) - start)
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
            transcript.answer_cue(note, own, style=self.style.cue)
            self._sending(transcript.parts()[0].content, transcript.run_parts())
            async with aclosing(self._answer(
                transcript.messages(), parts=transcript.parts(), held=True, watch=self._watch(),
            )) as events:
                async for event in events:
                    yield event
            if self.retracted is not None and not self.stop.is_set():
                async with aclosing(self._guard(transcript, steps, start)) as events:
                    async for event in events:
                        yield event
        finally:
            # On every way out, however it ends. If a pass of this run is
            # still inside the model the slot goes back when it leaves.
            self.engine.give_slot(self.request_id)

    def _filed(self, found: dict) -> dict:
        """A file event of the answer, as the page is told it. A file that
        closed is kept in the trace, with the event, which whoever keeps the
        file completes."""
        if found["type"] != "close":
            return found
        told = {"type": "close", "path": found["path"], "lang": found["lang"],
                "lines": found["content"].count("\n")}
        self.trace.files.append({**found, "event": told})
        return told

    def _needs_program(self, call: ToolCall) -> bool:
        tool = REGISTRY.get(call.name)
        return bool(tool and tool.program and "program" not in call.meta and "refusal" not in call.meta)

    async def _compose(self, transcript: Transcript, call: ToolCall, number: int) -> AsyncIterator[Event]:
        """The program a tool the model chose is to run, written by the model
        in a pass of its own: one fenced block under a grammar, read on from
        the choice, so that it reads only what is new. It goes with the call;
        without one, the call has nothing to run and says so."""
        step_id = f"code-{number}"
        yield "step", {
            "id": step_id, "kind": "route", "status": "running",
            "label": "Writing the code", "glyph": "⌨",
        }
        task = str(call.args.get("task", ""))
        transcript.compose(task)
        said = await self.engine.complete_json(
            transcript.messages(), PROGRAM, max(PROGRAM_TOKENS, self.engine.cfg.route_max_tokens),
            request_id=self.request_id, seed=self.seed, held=True,
        )
        program = program_of(said.raw)
        stopped = said.cancelled or self.stop.is_set()
        error = "stopped" if stopped else said.error or (None if program else "no program in the reply")
        self.trace.steps.append(self._passed(
            "compose", said, name=call.name, given={"task": task}, error=error,
        ))
        if said.raw:
            transcript.program(said.raw)
        call.meta["program"] = program
        yield "step", {
            "id": step_id, "kind": "route",
            "status": "stopped" if stopped else "ok" if program else "failed",
            "label": "Wrote the code" if program else "No code written", "glyph": "⌨",
            "ms": round(said.ms, 1), "tokens_in": said.prompt_tokens,
            "tokens_out": said.completion_tokens,
        }

    def _watch(self) -> Callable[[str], bool] | None:
        """What the answer is watched for, where it is: a claim that there is
        no live data to be had, from a run that looked nothing up and could
        have. None where there is nothing a lookup could add."""
        if self.found or self.lost or self.tainted or self.retracted is not None:
            return None
        return denies_access if self._guard_call() is not None else None

    def _guard_call(self) -> ToolCall | None:
        """The lookup an answer that denied having live data is replaced by:
        the headlines for news, a search for anything else."""
        tools = {t.name for t in available()} if self.tools_enabled else set()
        if NEWS.search(self.message) and "news" in tools:
            topic = news_topic(self.message)
            return ToolCall("news", {"topic": topic} if topic else {}, via="guard")
        query = search_query(self.message)
        if "web_search" in tools and query:
            return ToolCall("web_search", {"query": query}, via="guard")
        return None

    async def _guard(self, transcript: Transcript, steps: int, start: int) -> AsyncIterator[Event]:
        """The answer said it could not look anything up, and it can. What it
        wrote is taken back, the lookup it said it could not make is made,
        and the answer is written again from what came back. Once a run."""
        call = self._guard_call()
        if call is None:
            return
        transcript.action(json.dumps({"tool": call.name, "args": call.args}, ensure_ascii=False))
        self.calls.append(call)
        share = max(LEAST_SHARE, self.run_room - (estimate(transcript.rendered()) - start))
        async for event in self._lookup(steps, call, budget=share):
            yield event
        if self.last is None or self.stop.is_set():
            yield self._stopped()
            return
        transcript.observation(
            call.name, self.seen if self.last.ok else what_happened(self.last), ok=self.last.ok,
        )
        transcript.answer_cue(style=self.style.cue)
        self._sending(transcript.parts()[0].content, transcript.run_parts())
        async with aclosing(
            self._answer(transcript.messages(), parts=transcript.parts(), held=True)
        ) as events:
            async for event in events:
                yield event

    async def _skill_lookups(
        self, skill: Skill, values: dict, transcript: Transcript, made: set[str],
        steps: int, start: int, single: bool,
    ) -> AsyncIterator[Event]:
        """The lookups of a skill the model chose, each read into the
        transcript as a lookup of its own would be. They are steps of the
        run, held to its limit on steps and never made twice."""
        for call in skill.calls(values):
            if self.stop.is_set() or (not single and steps >= self.max_steps):
                break
            call = ToolCall(call.name, call.args, via="model")
            key = call_key(call)
            if key in made:
                continue
            made.add(key)
            self.calls.append(call)
            left = self.run_room - (estimate(transcript.rendered()) - start)
            lookups = len(skill.steps) if single else max(1, self.max_steps - steps)
            share = max(LEAST_SHARE, left // lookups)
            async for event in self._lookup(steps, call, budget=share):
                yield event
            steps += 1
            if self.last is None:
                break  # stopped while the tool was out
            transcript.observation(
                call.name, self.seen if self.last.ok else what_happened(self.last), ok=self.last.ok,
            )

    def _transcript(self, tools: list) -> Transcript:
        """The run's one prompt, with as much of the conversation as leaves
        room for the run. What had to give for it is kept in ``trim``."""
        message = self._message()
        window = self._window(system_for(tools), message, run=True)
        self._trimmed(window)
        return Transcript.fitted(window.parts, own=len(message))

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
        if action.kind not in ("tool", "skill") or self.stop.is_set():
            return action, passes
        rejection = self._refused(action)
        if rejection is None:
            return action, passes

        self.trace.steps[-1].ok = False
        self.trace.steps[-1].error = "rejected: " + "; ".join(rejection.reasons)
        if room < 2:
            return self._carried(action, rejection), passes
        transcript.rejection(rejection.reasons)
        action, said = await self._choose(
            "repair", transcript.messages(), tools, {"rejected": list(rejection.reasons)}, held=True
        )
        passes.append(said)
        if said.raw:
            transcript.action(said.raw)
        if action.kind in ("tool", "skill") and not self.stop.is_set():
            still = self._refused(action)
            if still is not None:
                self.trace.steps[-1].ok = False
                self.trace.steps[-1].error = "rejected: " + "; ".join(still.reasons)
                action = self._carried(action, still)
        return action, passes

    def _refused(self, action: Action) -> Rejection | None:
        """Why the call or the skill the model chose cannot be made as it
        stands, if it cannot. A skill's inputs are checked as a call's are,
        and the inputs it may go on with are kept with it."""
        if action.kind == "tool":
            return refusal(action.call, theirs=self.addresses, tainted=self.tainted)
        clean, rejection = check_inputs(action.skill, action.values, self.theirs)
        if rejection is None:
            action.values = clean
        return rejection

    def _carried(self, action: Action, rejection: Rejection) -> Action:
        """A choice that stays refused. A call is carried on as refused, to
        be shown failing and never made; a skill is no choice at all."""
        if action.kind == "tool":
            action.call.meta["refusal"] = rejection
            return action
        return Action("final", raw=action.raw, note="rejected: " + "; ".join(rejection.reasons))

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
        if result.ok and tool is not None and untrusted(tool):
            self.tainted = True
        if result.ok and tool is not None and tool.links:
            # The newest list is the one its numbers mean.
            self.links = links_of(result.data)
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

    async def _scoped(self, call: ToolCall) -> ToolResult:
        """The call, made in this run's conversation. Set inside the call's
        own task, so that it reaches the tool and goes no further."""
        CALL.set(CallContext(
            conversation_id=self.scope, links=self.links, program=call.meta.get("program", ""),
        ))
        return await self.call_tool(call.name, call.args)

    async def _call(self, call: ToolCall) -> ToolResult | None:
        """Run one tool, unless Stop comes first. None means it did.

        A tool that goes out can take ten seconds. Whoever pressed Stop should
        not have to wait for a page they no longer want.
        """
        tool = asyncio.ensure_future(self._scoped(call))
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

    def _message(self) -> list[Part]:
        """This message as the model reads it: what was said, then when, where
        that matters to the answer, then the project's files, where the
        conversation has a project."""
        parts = [Part("user", "question", self.said)]
        if time_matters(self.message):
            parts.append(sent_at(self.now))
        if self.project:
            listed = ", ".join(
                f"{f['path']} ({f['lines']} line{'' if f['lines'] == 1 else 's'})" for f in self.project
            )
            parts.append(Part("user", "project", (
                f"The project so far: {listed}. To change a file, write the whole file "
                "again under its ### path line."
            )))
        return parts

    def _prompt(self) -> list[Part]:
        """What this message is read as, after the conversation so far: the
        message, what was looked up for it and how to use that, as parts."""
        message = self._message()
        style = self.style.cue
        if self.summaries:
            # The tool output goes inside the user turn, not into a second
            # system message. A 1.5B model barely attends to a trailing system
            # message and falls back on its "I don't have real-time data"
            # prior — it will describe the weather as unavailable while the
            # card beside it shows today's temperature. Stated as part of the
            # question, it complies.
            facts = "\n".join(f"- {s}" for s in self.summaries)
            parts = [*message, Part(
                "user", "data", f"Live data, retrieved just now by a tool:\n{facts}",
                tuple(dict.fromkeys(self.found)),
            )]
            instruction = CUE_WITH_DATA
            if self.failures:
                parts.append(Part(
                    "user", "failed",
                    "A lookup was also tried and did not work:\n" + did_not_work(self.failures),
                    tuple(dict.fromkeys(self.lost)),
                ))
                instruction += " " + SAY_SO
            return [*parts, Part("user", "instruction", f"{instruction} {style}")]
        if self.failures:
            return [*message, Part(
                "user", "failed",
                "A lookup was tried for this and did not work:\n" + did_not_work(self.failures),
                tuple(dict.fromkeys(self.lost)),
            ), Part("user", "instruction", f"{SAY_SO} {style}")]
        return [*message, Part("user", "instruction", f"{CUE_PLAIN} {style}")]

    # ---------------------------------------------------------- the check --

    def _check(self, parts: list[Part]) -> None:
        """Hold the answer against what was looked up for it.

        In full where this run looked something up and it worked: every
        figure, and whether the answer says it has no live data. Where it only
        has results from earlier in the conversation, only figures with a
        unit or a currency, so that a poem written after a weather lookup is
        left alone. Not at all where nothing was looked up.

        What it reads the answer against: what this run's lookups returned,
        all of it, as the visitor sees it on the cards; what was found and
        read in this prompt, this turn's or replayed; and what the user wrote.
        Never an earlier answer, so a mistake does not vouch for itself by
        being repeated.
        """
        made = [(call, result) for call, result in self.trace.calls if result.ok]
        found = [p.content for p in parts if p.kind in ("data", "observation")]
        if not made and not found:
            return
        sources = list(found)
        for _, result in made:
            sources += [result.summary, *scalars(result.data)]
        sources += [m["content"] for m in self.history if m["role"] == "user"]
        sources.append(self.message)
        result = check_figures(
            self.trace.answer or "", sources, with_units_only=not made, denial=bool(made),
        )
        self.trace.checked = True
        if result.clean:
            return
        quoted = [
            (REGISTRY[call.name].label, outcome.summary) for call, outcome in made
            if call.name in REGISTRY and not REGISTRY[call.name].untrusted
        ]
        self.trace.unsupported = list(result.unsupported)
        self.trace.correction = caption(result, quoted)
        if self.trace.sent is not None:
            self.trace.sent.append(Part("user", "correction", note(result)))

    # ---------------------------------------------------------- the answer --

    async def _answer(
        self, messages: list[dict], *, parts: list[Part] = (), held: bool = False,
        watch: Callable[[str], bool] | None = None,
    ) -> AsyncIterator[Event]:
        """The pass that writes the answer, from ``messages``. ``parts`` are
        the same prompt with what each piece is, so the answer can be checked
        against what was looked up for it.

        ``watch``, where given, is held against the start of the answer as it
        is written. If it holds, the pass is stopped, the page is told to take
        back what it showed (``retract``), and ``retracted`` keeps what was
        written; the run decides what follows."""
        sampling = self.style.sampling(looked_up=bool(self.found))
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
            began = time.perf_counter()
            written = ""
            blocks = FileBlocks()
            async for event, data in stream:
                if event == "token":
                    yield event, data
                    for found in blocks.feed(data.get("t", "")):
                        yield "file", self._filed(found)
                    if watch is None:
                        continue
                    written += data.get("t", "")
                    if watch(written):
                        self.retracted = written
                        self.trace.steps.append(Step(
                            "answer", model=True, input=trimmed, output=written,
                            total_ms=(time.perf_counter() - began) * 1000 + waited,
                            ok=False, error="retracted: it said it could not look this up",
                        ))
                        yield "retract", {"reason": "It said it could not look this up, so it will."}
                        return
                    if len(written) > WATCHED:
                        watch = None
                    continue
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
                if not stopped:
                    for found in blocks.finish():
                        yield "file", self._filed(found)
                queued = data.get("queue_wait_ms")
                self.trace.steps.append(Step(
                    "answer", model=True, input=trimmed,
                    prompt_tokens=data.get("prompt_tokens"),
                    new_tokens=data.get("read_tokens"),
                    output_tokens=data.get("completion_tokens"),
                    queue_ms=queued if queued is None and not waited else (queued or 0.0) + waited,
                    first_token_ms=data.get("ttft_ms"),
                    total_ms=pass_ms(queued, data.get("total_ms")) + waited,
                    ok=not stopped, error="stopped" if stopped else None,
                ))
                self.trace.outcome = "stopped" if stopped else "answered"
                self.trace.answer = data.pop("text", "")
                if self.trace.sent is not None:
                    self.trace.sent.append(Part("assistant", "answer", self.trace.answer))
                # Checked before anything is said of the answer: the keeper
                # stores it when its step closes, and stores what was found.
                if not stopped:
                    self._check(parts)
                data["unsupported"] = list(self.trace.unsupported)
                data["correction"] = self.trace.correction
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
