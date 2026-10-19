"""One message, from the question to the answer.

The model is talked to, and it decides. It reads the conversation and the
message and replies: it may answer at once, or call a tool first, in the
format it was trained to call one in (``calls``). A call is made, what came of
it is put in the transcript, and the model replies again, until it answers or
a limit says it must. A greeting is one reply; a lookup is a call and an
answer. Each stage says what it is doing as it does it, as ``(event,
payload)`` pairs the request handler forwards to the page unchanged.

Two things this module deliberately does not do:

* **It stores nothing.** What happened is written into a ``Trace`` as it
  happens, and whoever asked for the run decides what to keep. A run can then
  be driven without a database, which is how it is tested. (A tool may keep
  what it was asked to keep: a note, a file of the project.)
* **It reaches for nothing by itself.** The engine and the tool dispatcher are
  handed in, so a test can pass an engine that plays back prepared text and
  tools that never touch the network.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import AsyncIterator, Awaitable, Callable
from zoneinfo import ZoneInfo

from ..chatml import THINK_BLOCK
from ..config import settings
from ..llm import Engine, Stop, engine as default_engine
from ..llm.usage import blank
from ..projects import paths
from ..text import first_sentence
from ..tools import REGISTRY, call_tool as default_call_tool
from ..tools.base import (
    CALL, CallContext, Link, Rejection, ToolResult, available, check, enabled, fresh_for,
    untrusted,
)
from ..tools.results import links_of
from .calls import Call, parse_call, render_call
from .context import SUMMARY_TOKENS, Part, Turn, Window, cap, fit, key, run_room, turns_from_history
from .policy import call_key, refusal, users_words
from .reply import Reply, write
from .route import URL_RE, ToolCall, free_route
from .trace import Step, Trace, pass_ms
from .transcript import NO_MORE_CALLS, Transcript, project_line, sent_at, system_for
from .verify import caption, note, scalars
from .verify import check as check_figures

Event = tuple[str, dict]
ToolCaller = Callable[[str, dict], Awaitable[ToolResult]]

# What the model is told of a call it has already made in this message.
ALREADY_MADE = "That call was already made above; use its result."
# What an answer is when the model wrote none: honest, and short.
NO_ANSWER = "I could not finish an answer to that."

# The least a tool's result is given to read, however little room is left.
LEAST_SHARE = 40
# The least room a reply that may still call a tool is given; with less, it
# is the last reply, and answers.
MIN_REPLY = 384
# Tokens a reply is given beyond its thinking: what it says, and a call.
REPLY_ROOM = 1536


def what_happened(result: ToolResult, tool=None) -> str:
    """A failed call as the model reads it.

    A tool that goes out to the web says why it failed in a sentence written
    for a person, which often goes on to advise ("Try again"); only what
    happened is passed on, its first sentence. A tool of this machine's says
    what to do about it on purpose, and is passed on whole.
    """
    said = (result.summary or result.error or "It did not work.").strip()
    if tool is not None and not tool.network:
        return said
    return first_sentence(said)


def run(
    message: str,
    *,
    history: list[dict],
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

    ``seed`` is given to every request of the run, so that the same message
    against the same model gives the same run. The site leaves it unset.

    ``loop`` lets the model call one tool after another, within the limits in
    the settings. Without it the model may call one, and then answers. Left
    unset, the setting decides.

    ``context`` is the conversation as it was sent, turn by turn, which is
    what the model reads of it. ``history`` stays what was said, and only
    that: it is what decides which addresses the user gave. Without
    ``context`` the conversation is read from ``history``.

    ``scope`` is the conversation the run belongs to: the one whose notes a
    note tool keeps and reads, and whose project the file tools work on.

    ``links`` is the last list of results the conversation was shown, what
    "open the second one" means until the run is shown another.

    ``project`` is the conversation's project as it stands, file by file
    (``store.projects.manifest``): what the model is told is there.

    ``now`` is when the message was sent, as the model is told it; left
    unset, it is now, in the site's timezone.

    The last event is always ``run``: how it ended, how many steps and
    replies it took, and what they cost together.
    """
    trace = trace if trace is not None else Trace()
    if request_id:
        trace.run_id = request_id
    looping = settings.agent_loop if loop is None else loop
    return _Run(
        message=message,
        history=history,
        trace=trace,
        engine=engine if engine is not None else default_engine,
        call_tool=call_tool if call_tool is not None else default_call_tool,
        tools_enabled=settings.tools_enabled if tools_enabled is None else tools_enabled,
        request_id=trace.run_id,
        seed=seed,
        turns=context,
        scope=scope,
        now=now if now is not None else datetime.now(ZoneInfo(settings.timezone)),
        links=tuple(links),
        project=list(project or ()),
        max_steps=settings.agent_max_steps if looping else 1,
        max_seconds=settings.agent_max_seconds,
    ).go()


@dataclass
class _Run:
    message: str
    history: list[dict]
    trace: Trace
    engine: Engine
    call_tool: ToolCaller
    tools_enabled: bool
    request_id: str
    seed: int | None = None
    # The most tool calls one message may make, and the time after which no
    # further call is begun.
    max_steps: int = 8
    max_seconds: float = 600.0
    # What the last tool call came to; None if it was stopped. And what the
    # model reads of it, when it worked.
    last: ToolResult | None = None
    seen: str = ""
    # Time spent waiting for the slot that no reply has yet been charged for.
    slot_wait: float = 0.0
    # The calls this message made, and the tools that found something.
    calls: list[ToolCall] = field(default_factory=list)
    found: list[str] = field(default_factory=list)
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
    # The conversation the run belongs to, whose notes and project it uses.
    scope: str | None = None
    # Set once the run has read text somebody other than the user wrote: a
    # page, an article, notes others kept. From then on it writes nothing the
    # model chose to write.
    tainted: bool = False
    # When the message was sent, as the model is told it.
    now: datetime | None = None
    # The conversation's project as it stands: each file's path and size.
    project: list[dict] = field(default_factory=list)
    # The last list of results the model was shown: the pages it may ask to
    # open by number, and addresses it may go to though the user never wrote
    # them, since a search, not a page, gave them.
    links: tuple[Link, ...] = ()
    # What the model said to the reader, reply by reply.
    spoken: list[str] = field(default_factory=list)
    # Set by whoever asks for this run to stop. Looked at before each stage,
    # and waited on while a tool is out.
    stop: Stop = field(default_factory=Stop)

    def __post_init__(self) -> None:
        self.said = self.said or self.message

    @property
    def thinking(self) -> str:
        return getattr(self.engine.cfg, "think_mode", "none")

    @property
    def think_budget(self) -> int:
        return int(getattr(self.engine.cfg, "think_budget", 1024) or 1024)

    @property
    def reply_need(self) -> int:
        """The room one reply is kept: its thinking, and what it says."""
        return (self.think_budget if self.thinking == "on" else 0) + REPLY_ROOM

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

    async def go(self) -> AsyncIterator[Event]:
        # Held for the whole run, so that Stop finds it between replies and
        # while a tool is out, not only while the model is writing.
        started = time.perf_counter()
        with self.engine.hold(self.request_id) as self.stop:
            try:
                # Closed with this generator, not whenever it is collected: a
                # reply underneath may be holding the one inference slot.
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

    # ------------------------------------------------------------ the window --

    def _room(self) -> int:
        """Tokens the conversation and the message may take: the window, less
        the room for the run's own additions, for a reply, and the headroom."""
        cfg = self.engine.cfg
        return cfg.n_ctx - run_room(cfg.n_ctx) - self.reply_need - cfg.reply_headroom

    def _cap(self, system: str) -> tuple[str, bool]:
        """The message cut to what the window can give it beside the system
        text and a summary of older turns, if it is longer. Counted only when
        it might be: no message has more tokens than bytes."""
        limit = self._room() - self.engine.count_tokens(system) - SUMMARY_TOKENS
        if len(self.message.encode("utf-8")) <= limit:
            return self.message, False
        return cap(self.message, max(1, limit), self.engine.count_tokens)

    def _left(self, transcript: Transcript) -> int:
        """Tokens left in the window after the transcript as it stands."""
        cfg = self.engine.cfg
        return cfg.n_ctx - self.engine.prompt_tokens(transcript.messages()) - cfg.reply_headroom

    def _message(self) -> list[Part]:
        """This message as the model reads it: what was said, then when, then
        the project's files, where the conversation has a project."""
        parts = [Part("user", "question", self.said), sent_at(self.now)]
        listed = project_line(self.project)
        if listed is not None:
            parts.append(listed)
        return parts

    def _transcript(self, tools: list) -> Transcript:
        """The run's one prompt, with as much of the conversation as leaves
        room for the run. What had to give for it is kept in ``trim``."""
        system = system_for(tools)
        self.said, self.cut = self._cap(system)
        message = self._message()
        window = fit(
            system, self.turns or [], message, room=self._room(),
            count=self.engine.count_tokens, now=time.time(), fresh_for=fresh_for,
        )
        self._trimmed(window)
        self.trace.system_key = key(system)
        return Transcript(window.parts, own=len(message))

    def _trimmed(self, window: Window) -> None:
        """What had to give for the prompt to fit, if anything did, kept in
        ``trim`` for the page and the trace."""
        if not window.summarised and not self.cut:
            return
        dropped = sum(bool(t.question) + bool(t.answer) for t in (self.turns or [])[:window.summarised])
        self.trim = {
            "dropped": dropped, "turns": window.turns, "summarised": window.summarised,
            "cut": self.cut,
        }

    def _stopped(self) -> Event:
        """How a run ends when it was stopped before an answer was written."""
        self.trace.outcome = "stopped"
        self.trace.answer = self._spoken()
        self.trace.usage = blank(cancelled=True, tool_calls=len(self.trace.calls))
        return "done", self.trace.usage

    def _spoken(self) -> str:
        return "\n\n".join(text.strip() for text in self.spoken if text.strip())

    # ------------------------------------------------------------- the run --

    async def _stages(self) -> AsyncIterator[Event]:
        if self.turns is None:
            self.turns = turns_from_history(self.history)
        self.tainted = any(
            untrusted(REGISTRY[tool])
            for turn in self.turns for part in turn.parts
            if part.kind in ("data", "observation", "response")
            for tool in part.tools if tool in REGISTRY
        )
        tools = available() if self.tools_enabled else []
        transcript = self._transcript(tools)
        if self.trim:
            yield "trimmed", self.trim
        try:
            async for event in self._take():
                yield event
            if self.stop.is_set():
                yield self._stopped()
                return
            made: set[str] = set()
            steps = 0
            began = time.perf_counter()
            if tools:
                call, _ = free_route(self.message)
                if call is not None:
                    async for event in self._seeded(call, transcript, made):
                        yield event
                    steps += 1
            noted = False
            number = 0
            while True:
                if self.stop.is_set():
                    yield self._stopped()
                    return
                number += 1
                left = self._left(transcript)
                final = (
                    not tools or steps >= self.max_steps
                    or time.perf_counter() - began >= self.max_seconds
                    or left < MIN_REPLY + (self.think_budget if self.thinking == "on" else 0)
                )
                if final and steps and not noted:
                    transcript.note(NO_MORE_CALLS)
                    noted = True
                    left = self._left(transcript)
                if left <= 16:
                    self.spoken.append(NO_ANSWER)
                    async for event in self._ended(transcript, None, None):
                        yield event
                    return
                reply = Reply()
                async for event in self._reply(transcript, reply, number, left=left, final=final):
                    yield event
                if reply.ended == "failed":
                    self.trace.outcome = "failed"
                    return
                if reply.ended == "stopped":
                    yield self._stopped()
                    return
                if reply.ended == "call" and not final:
                    call = parse_call(reply.call)
                    transcript.call(reply.raw, call.name)
                    async for event in self._made(call, transcript, made, steps, reply):
                        yield event
                    steps += 1
                    continue
                transcript.answer(reply.raw)
                async for event in self._ended(transcript, reply, number):
                    yield event
                return
        finally:
            # On every way out, however it ends. If a reply of this run is
            # still inside the model the slot goes back when it leaves.
            self.engine.give_slot(self.request_id)

    async def _reply(
        self, transcript: Transcript, reply: Reply, number: int, *, left: int, final: bool,
    ) -> AsyncIterator[Event]:
        """One reply of the model, told as it is written."""
        step_id = f"reply-{number}"
        thinking = self.thinking
        yield "step", {
            "id": step_id, "kind": "model", "status": "running",
            "label": "Thinking" if thinking == "on" else "Writing", "glyph": "✦",
        }
        messages = transcript.messages()
        self.trace.prompt = messages
        waited, self.slot_wait = self.slot_wait, 0.0
        async with aclosing(write(
            self.engine, messages, room=left, final=final, thinking=thinking,
            budget=self.think_budget, request_id=self.request_id, seed=self.seed,
            held=True, stop=self.stop, reply=reply,
        )) as events:
            async for event in events:
                yield event
        if reply.said.strip():
            self.spoken.append(reply.said)
        called = reply.ended == "call" and not final
        name = parse_call(reply.call).name if called else None
        stopped = reply.ended == "stopped"
        queued = reply.queue_wait_ms
        self.trace.steps.append(Step(
            "call" if called else "answer", model=True, name=name, via="model",
            input={"think_tokens": reply.think_tokens} if reply.think_tokens else None,
            output=reply.call if called else None,
            prompt_tokens=reply.prompt_tokens, new_tokens=reply.read_tokens,
            output_tokens=reply.completion_tokens,
            queue_ms=queued if queued is None and not waited else (queued or 0.0) + waited,
            first_token_ms=reply.ttft_ms,
            total_ms=pass_ms(queued, reply.total_ms) + waited,
            ok=reply.ended not in ("failed", "stopped"),
            error="stopped" if stopped else reply.error,
        ))
        tool = REGISTRY.get(name) if name else None
        yield "step", {
            "id": step_id, "kind": "model",
            "status": "stopped" if stopped else "failed" if reply.ended == "failed" else "ok",
            "label": (f"Called {tool.label}" if tool else "Called a tool") if called else "Answer",
            "glyph": "✦", "ms": round(reply.total_ms, 1),
            "tokens_in": reply.prompt_tokens, "tokens_out": reply.completion_tokens,
            "think_tokens": reply.think_tokens,
        }

    async def _ended(self, transcript: Transcript, reply: Reply | None, number: int | None):
        """How a run ends once the model has answered."""
        answer = self._spoken() or NO_ANSWER
        self.trace.answer = answer
        self.trace.outcome = "answered"
        self.trace.sent = list(transcript.run_parts())
        self._check(transcript.parts())
        figures = reply.figures() if reply is not None else blank()
        figures["unsupported"] = list(self.trace.unsupported)
        figures["correction"] = self.trace.correction
        # Every call the model wrote, made or not, as the page shows them.
        figures["tool_calls"] = len(self.trace.calls)
        self.trace.usage = figures
        yield "done", figures

    # ---------------------------------------------------------- the calls --

    async def _seeded(self, call: ToolCall, transcript: Transcript, made: set[str]) -> AsyncIterator[Event]:
        """A call the user made themselves with a command: put in the
        conversation as if the model had made it, and made."""
        opened = THINK_BLOCK if self.thinking in ("on", "off") else ""
        transcript.call(opened + render_call(call.name, call.args), call.name)
        made.add(call_key(call))
        self.calls.append(call)
        async for event in self._lookup(0, call, budget=self._budget(transcript, call.name)):
            yield event
        if self.last is not None:
            transcript.response(call.name, self._read(call), ok=self.last.ok)
            for event in self._files(call.name, self.last, None):
                yield event

    async def _made(
        self, call: Call, transcript: Transcript, made: set[str], index: int, reply: Reply,
    ) -> AsyncIterator[Event]:
        """A call the model wrote: made if it can be, and what came of it
        put in the transcript for the model's next reply."""
        tool = REGISTRY.get(call.name)
        if call.error is not None or tool is None or not enabled(tool):
            said = call.error or f"There is no tool called {call.name!r}."
            failed = ToolCall(call.name or "tool_call", call.args, via="model", raw=reply.call)
            for event in self._failed(index, failed, said, "unknown tool" if call.name else "bad call"):
                yield event
            transcript.response(call.name, said, ok=False)
            return
        # The arguments as the tool takes them, where they can be: a number
        # the model wrote is a number. One that cannot be is kept as written,
        # and refused below.
        clean, wrong = check(tool, call.args)
        made_call = ToolCall(call.name, call.args if wrong else clean, via="model", raw=reply.call)
        known = call_key(made_call)
        if known in made:
            for event in self._failed(index, made_call, ALREADY_MADE, "repeated"):
                yield event
            transcript.response(call.name, ALREADY_MADE, ok=False)
            return
        made.add(known)
        self.calls.append(made_call)
        rejection = refusal(made_call, theirs=self.addresses, tainted=self.tainted)
        if rejection is not None:
            made_call.meta["refusal"] = rejection
        async for event in self._lookup(index, made_call, budget=self._budget(transcript, call.name)):
            yield event
        if self.last is None:
            return  # stopped while the tool was out
        transcript.response(call.name, self._read(made_call), ok=self.last.ok)
        for event in self._files(call.name, self.last, reply.streamed):
            yield event

    def _read(self, call: ToolCall) -> str:
        """What the model reads of the call just made."""
        rejection: Rejection | None = call.meta.get("refusal")
        if rejection is not None:
            return "That call was not made: " + "; ".join(rejection.reasons) + "."
        if self.last.ok:
            return self.seen
        return what_happened(self.last, REGISTRY.get(call.name))

    def _budget(self, transcript: Transcript, name: str) -> int:
        """What a tool's result may take of the window: what is left once a
        reply has its room, and never less than a line."""
        return max(LEAST_SHARE, self._left(transcript) - self.reply_need)

    def _failed(self, index: int, call: ToolCall, said: str, code: str) -> list[Event]:
        """A call that was not made, shown failing and kept as one."""
        step_id = f"tool-{index}"
        result = ToolResult(False, said, error=code)
        self.trace.calls.append((call, result))
        self.trace.steps.append(Step(
            "tool", name=call.name, via=call.via, input=call.args, output=said,
            ok=False, error=code,
        ))
        label = REGISTRY[call.name].label if call.name in REGISTRY else "Tool call"
        return [
            ("step", {"id": step_id, "kind": "tool", "status": "failed", "label": label,
                      "glyph": "◆", "tool": call.name, "ms": 0.0, "detail": code}),
            ("card", {"step_id": step_id, "card": "error", "tool": call.name,
                      "data": {"message": said}}),
        ]

    def _files(self, name: str, result: ToolResult, streamed: str | None) -> list[Event]:
        """What the project panel is told of a file a call wrote: the file,
        where it was not shown as it was written, and how it was kept."""
        if name not in ("write_file", "edit_file"):
            return []
        data = result.data or {}
        path = data.get("path") or streamed
        if not path:
            return []
        lang = data.get("lang") or paths.language(path)
        events: list[Event] = []
        if result.ok and (name == "edit_file" or streamed != path):
            events.append(("file", {"type": "open", "path": path, "lang": lang}))
            for line in (data.get("content") or "").splitlines(keepends=True):
                events.append(("file", {"type": "line", "path": path,
                                        "text": line if line.endswith("\n") else line + "\n"}))
        if result.ok:
            events.append(("file", {"type": "close", "path": path, "lang": lang,
                                    "lines": data.get("lines"), "version": data.get("version"),
                                    "kept": True}))
        elif streamed == path:
            events.append(("file", {"type": "close", "path": path, "lang": lang,
                                    "lines": data.get("lines") or 0, "kept": False,
                                    "why": result.summary}))
        return events

    # ------------------------------------------------------------ one tool --

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
        rejected: Rejection | None = call.meta.get("refusal")
        if rejected is not None:
            # A call the model chose that cannot be made. It is not made.
            # What the visitor sees is the refusal.
            result = ToolResult(False, rejected.summary, error=rejected.code)
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
            self.found.append(call.name)
            yield "card", {
                "step_id": step_id, "card": result.card,
                "tool": call.name, "data": result.data,
            }
        else:
            # A failed tool is shown, not hidden, and the model is told as
            # well: left to think nothing was tried, it answers from memory
            # beside a card that says the lookup failed.
            yield "card", {
                "step_id": step_id, "card": "error", "tool": call.name,
                "data": {"message": result.summary or result.error},
            }

    async def _scoped(self, call: ToolCall) -> ToolResult:
        """The call, made in this run's conversation. Set inside the call's
        own task, so that it reaches the tool and goes no further."""
        CALL.set(CallContext(conversation_id=self.scope, links=self.links, run_id=self.request_id))
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

    # ---------------------------------------------------------- the check --

    @property
    def purpose(self) -> str:
        """What a result is read for: the message, without any address in it.
        An address says where to look, not what to look for."""
        return " ".join(URL_RE.sub(" ", self.message).split())

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
        being repeated. A file the model wrote is not a lookup.
        """
        made = [
            (call, result) for call, result in self.trace.calls
            if result.ok and call.name not in ("write_file", "edit_file", "read_file")
        ]
        found = [
            p.content for p in parts
            if p.kind in ("data", "observation", "response")
            and not set(p.tools) & {"write_file", "edit_file", "read_file"}
        ]
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
