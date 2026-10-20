"""One reply of the model, written in as many requests as it takes.

A reply is what the model says in one turn: it may think first, then say
something, and end either in an answer or in a call to a tool. It is written
in phases, and each change of phase is a stop the model server makes by
itself at a string it was given, followed by a request whose prompt is the
last one with what was written added: the server reads only what is new.

* **thinking**, where the model thinks (``thinking`` = on): up to
  ``think_budget`` tokens, ended where the model closes its block or, at the
  budget, by the line Qwen documents for ending a thought early.
* **prose**: what the model says, sampled as prose. It ends at the end of the
  reply, at the end of a call, or where a call begins a parameter that holds
  code.
* **code**: the value of such a parameter (a file's content, the text an
  edit replaces and replaces it with, a program), sampled as code, up to the
  tag that closes it. Then prose again.

What is said is passed on as it comes; the call is not shown. A file a
``write_file`` call writes is shown as it is written, line by line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

from contextlib import aclosing

from ..chatml import THINK_CLOSE, opener
from ..projects import paths
from .calls import CALL_CLOSE, CALL_OPEN, PARAM_CLOSE, Splitter, writing
from .style import sampling

Event = tuple[str, dict]

# The parameters whose values are code, and are written with code's sampling.
CODE_PARAMS = ("content", "old_string", "new_string", "code")
CODE_OPENERS = tuple(f"<parameter={name}>" for name in CODE_PARAMS)
CODE_END = "\n" + PARAM_CLOSE
# How Qwen3 and Qwen3.5 are told their thinking has to end now: the model
# goes on from here as if it had closed its thought itself.
BUDGET_CLOSER = (
    "\n\nConsidering the limited time by the user, I have to give the solution based on "
    "the thinking directly now.\n" + THINK_CLOSE + "\n\n"
)


@dataclass
class Reply:
    """One reply, as far as it has been written, and what it cost."""

    # How the model thinks: on | off | none.
    thinking: str = "none"
    # Everything after the turn's opening line, as the model will read it
    # back: its opener, its thought, what it said and its call.
    raw: str = ""
    thought: str = ""
    said: str = ""
    # The call it ended in, as written, from <tool_call> on; "" if none.
    call: str = ""
    # How it ended: call | answer | limit | stopped | failed.
    ended: str = "answer"
    error: str | None = None
    # A file a write_file call began to show as it was written.
    streamed: str | None = None
    # What it cost, over every request it took.
    requests: int = 0
    prompt_tokens: int | None = None
    read_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int = 0
    think_tokens: int = 0
    # How its thought ended, when it had one to end: closed (by the model) |
    # budget (cut at the thinking budget) | room (cut where the window ran
    # out first) | eos (the turn ended inside the thought) | stopped |
    # skipped (given no budget, so it opened with an empty thought).
    think_end: str | None = None
    queue_wait_ms: float | None = None
    ttft_ms: float | None = None
    total_ms: float = 0.0
    writing_ms: float = 0.0
    last: dict = field(default_factory=dict)

    def figures(self) -> dict:
        """The reply's figures, as ``done`` carries them."""
        gen_s = max(self.writing_ms / 1000, 1e-6)
        prompt = self.prompt_tokens or 0
        return {
            "cancelled": self.ended == "stopped",
            "ttft_ms": self.ttft_ms,
            "queue_wait_ms": self.queue_wait_ms,
            "prompt_tokens": self.prompt_tokens,
            "read_tokens": self.read_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "think_tokens": self.think_tokens,
            "prefill_tps": round(prompt / max((self.ttft_ms or 0) / 1000, 1e-6), 1) if self.ttft_ms else None,
            "gen_tps": round(max(self.completion_tokens - self.requests, 0) / gen_s, 2),
            "total_ms": round(self.total_ms, 1),
            "rss_mb": self.last.get("rss_mb"),
            "requests": self.requests,
        }


def _add(current: int | None, more) -> int | None:
    if more is None:
        return current
    return (current or 0) + more


async def write(
    engine,
    messages: list[dict],
    *,
    room: int,
    final: bool = False,
    thinking: str = "none",
    budget: int = 1024,
    request_id: str | None = None,
    seed: int | None = None,
    held: bool = False,
    stop=None,
    reply: Reply | None = None,
) -> AsyncIterator[Event]:
    """Write one reply to ``messages`` in at most ``room`` tokens.

    ``final`` keeps it from calling a tool: it is the last reply a run may
    make, and a call it begins ends it. Yields what the page is told as it
    happens (``start`` once, ``think``, ``token``, ``file``, and ``queue`` and
    ``error`` as the engine says them), and fills in ``reply``.
    """
    reply = reply if reply is not None else Reply()
    reply.thinking = thinking
    begin = opener(thinking)
    reply.raw = begin
    split = Splitter()
    phase = "think" if thinking == "on" else "prose"
    if phase == "think" and budget <= 0:
        # No thinking for this reply: the thought is closed before it begins,
        # which written after the opener is the empty block, as with thinking
        # off, so the prompt still only ever grows.
        reply.raw += "\n" + THINK_CLOSE + "\n\n"
        reply.think_end = "skipped"
        phase = "prose"
    code_param: str | None = None
    line = ""
    fresh = False
    told_start = False
    first_shown = True

    def said(text: str) -> str:
        """What the reader is shown of ``text``: all of it, but for the line
        breaks the model puts after its thinking, before it says anything."""
        if not reply.said:
            text = text.lstrip("\n")
        reply.said += text
        return text

    def shown(kind: str, text: str) -> Event:
        nonlocal first_shown
        payload = {"t": text}
        if first_shown and reply.ttft_ms is not None:
            payload["ttft_ms"] = reply.ttft_ms
        first_shown = False
        return kind, payload

    while True:
        left = room - reply.completion_tokens
        if left <= 0:
            reply.ended = "limit"
            if phase == "think":
                reply.think_end = "room"
            break
        if phase == "think":
            stops, n, mode = [THINK_CLOSE], min(budget, left), "prose"
        elif phase == "prose":
            stops = [CALL_CLOSE, *CODE_OPENERS] + ([CALL_OPEN] if final else [])
            n, mode = left, "prose"
        else:
            stops, n, mode = [CODE_END], left, "code"
        settings = {**sampling(mode, thinking), "max_tokens": n}
        prefix = reply.raw[len(begin):]
        written = ""
        finish = word = None
        async with aclosing(engine.stream_chat(
            messages, settings, request_id, prefix=prefix, seed=seed, stop=stops, held=held,
        )) as stream:
            async for event, data in stream:
                if event == "queue":
                    yield event, data
                elif event == "start":
                    if not told_start:
                        told_start = True
                        yield event, data
                elif event == "token":
                    text = data.get("t", "")
                    written += text
                    if reply.ttft_ms is None and data.get("ttft_ms") is not None:
                        reply.ttft_ms = data["ttft_ms"]
                    if phase == "think":
                        reply.thought += text
                        yield shown("think", text)
                    elif phase == "prose":
                        out = said(split.feed(text))
                        if out:
                            yield shown("token", out)
                    else:
                        split.feed(text)
                        if reply.streamed and code_param == "content":
                            line += text
                            if fresh:
                                # The line break the format puts after the
                                # parameter's tag is not part of the file.
                                if not line:
                                    continue
                                line = line[1:] if line.startswith("\n") else line
                                fresh = False
                            while "\n" in line:
                                head, line = line.split("\n", 1)
                                yield "file", {"type": "line", "path": reply.streamed, "text": head + "\n"}
                elif event == "error":
                    reply.ended, reply.error = "failed", data.get("message")
                    yield event, data
                elif event == "done":
                    finish, word = data.get("finish"), data.get("word")
                    reply.requests += 1
                    reply.last = data
                    if reply.requests == 1:
                        # The reply's own prompt: what the turn read, and how
                        # much of it the server already had.
                        reply.prompt_tokens = data.get("prompt_tokens")
                        reply.cached_tokens = data.get("cached_tokens")
                        reply.queue_wait_ms = data.get("queue_wait_ms")
                        if reply.ttft_ms is None:
                            reply.ttft_ms = data.get("ttft_ms")
                    reply.read_tokens = _add(reply.read_tokens, data.get("read_tokens"))
                    produced = data.get("completion_tokens") or 0
                    reply.completion_tokens += produced
                    if phase == "think":
                        reply.think_tokens += produced
                    total = data.get("total_ms") or 0.0
                    reply.total_ms += total
                    reply.writing_ms += max(total - (data.get("ttft_ms") or 0.0), 0.0)
        reply.raw += written
        if reply.ended == "failed":
            return
        if finish == "cancelled" or (stop is not None and stop.is_set()):
            reply.ended = "stopped"
            if phase == "think":
                reply.think_end = "stopped"
            break
        if finish is None:
            reply.ended, reply.error = "failed", (
                "The model server did not say how the reply ended, so the reply could not go on."
            )
            yield "error", {"message": reply.error}
            return
        if phase == "think":
            if finish == "word":
                reply.raw += THINK_CLOSE
                reply.think_end = "closed"
            elif finish == "limit":
                reply.raw += BUDGET_CLOSER
                # Cut at its own budget, or sooner, where the window ran out.
                reply.think_end = "budget" if n >= budget else "room"
            else:
                # The model ended its turn while it was still thinking.
                reply.think_end = "eos"
                reply.ended = "answer"
                break
            phase = "prose"
            continue
        if phase == "code":
            if finish == "word":
                reply.raw += CODE_END
                split.feed(CODE_END)
                if reply.streamed and code_param == "content" and line:
                    yield "file", {"type": "line", "path": reply.streamed, "text": line + "\n"}
                line = ""
                code_param = None
                phase = "prose"
                continue
            reply.ended = "limit" if finish == "limit" else "call"
            break
        # prose
        if finish == "word" and word == CALL_CLOSE:
            reply.raw += CALL_CLOSE
            split.feed(CALL_CLOSE)
            reply.ended = "call"
            break
        if finish == "word" and word in CODE_OPENERS:
            reply.raw += word
            split.feed(word)
            if not split.calling:
                # Said in passing, outside a call: it is prose like the rest.
                out = said(word)
                if out:
                    yield shown("token", out)
                continue
            code_param = word[len("<parameter="):-1]
            fresh = True
            function, path = writing(split.call)
            clean = paths.safe(path) if path else None
            if function == "write_file" and code_param == "content" and clean and not reply.streamed:
                reply.streamed = clean
                yield "file", {"type": "open", "path": clean, "lang": paths.language(clean)}
            phase = "code"
            continue
        if finish == "word" and word == CALL_OPEN:
            # The last reply a run may make began a call: it ends here.
            reply.ended = "answer"
            break
        reply.ended = "limit" if finish == "limit" else ("call" if split.calling else "answer")
        break
    rest = said(split.flush())
    if rest:
        yield shown("token", rest)
    reply.call = split.call
