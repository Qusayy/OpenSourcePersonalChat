"""What of a conversation the model reads with the next message, and how it
is fitted to the window.

A turn is kept as what was sent: the parts of the prompt from the visitor's
message to the answer, each with a kind, joined in order by the one rule a
prompt is built with, so that joining them gives back exactly the text the
model read. Replayed as it was, the next prompt begins with the last one and
its answer, and a model server that keeps its cache reads only what is new.

Replayed as it was, that is, under the same system text. A turn sent under
another (another persona, other tools) is replayed compact: what the visitor
asked, what was looked up for it, and the answer. A result that goes out of
date (the weather, a rate, the time) is replayed, once it has, as a fixed line
saying so: the prompt changes once, at that point, and not every turn after.

When the conversation outgrows the window, its oldest turns are dropped whole,
and only where a block of about ``TRIM_STEP`` tokens begins. A trim then
happens about once in that many tokens, and every prompt in between extends
the one before. The dropped turns become one short summary at the top.

Nothing here reaches the model. Counting is the caller's, given as a function,
and every text is counted at most once a fit; the choice of where to cut is
made on an estimate, and one whole-prompt count checks it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..chatml import render_chatml
from ..text import clip, estimate, first_sentence

# The most a summary of dropped turns takes.
SUMMARY_TOKENS = 150
# The most an earlier message takes when its turn is replayed compact. A
# longer one (a pasted file, say) is cut, so that it cannot crowd out the rest
# of the conversation for as long as it stays in the window. A turn replayed
# as it was sent is replayed whole: cutting it would cost the model a re-read
# of everything after it.
TURN_ROOM = 800
# Tokens kept free, when a looping run's conversation is fitted to the window,
# for what the run itself will add: its choices and what its tools return.
# With that room left at the start, the beginning of the prompt does not have
# to change part-way through the run. The least of it; a larger window gives a
# run more (``run_room``), so a search can be read at length.
RUN_ROOM = 600
RUN_ROOM_MOST = 2000


def run_room(n_ctx: int) -> int:
    """The room a run is given in a window of ``n_ctx`` tokens: an eighth of
    it, and never less than ``RUN_ROOM`` or more than ``RUN_ROOM_MOST``."""
    return max(RUN_ROOM, min(RUN_ROOM_MOST, n_ctx // 8))
# Old turns are dropped in blocks of about this many tokens, never fewer.
TRIM_STEP = 600

# What a part of a prompt can be.
KINDS = frozenset({
    "question",     # what the visitor wrote
    "data",         # what a lookup found, as the answer pass reads it
    "failed",       # what is known of a lookup that did not work
    "instruction",  # how to use what was found
    "cue",          # what a looping run is asked last, before it answers
    "action",       # what the model replied when asked to choose
    "observation",  # what a tool the model chose brought back
    "rejection",    # a call the model chose that was not made, and why
    "summary",      # the turns that no longer fit, in short
    "answer",       # the reply
    "correction",   # what the harness found wrong with the reply
    "now",          # when the message was sent
    "compose",      # what a program the model is about to write is for
    "code",         # the program it wrote
})
# What survives of a turn replayed compact, beside the question and answer.
FOUND = frozenset({"data", "failed", "observation"})

# What a result that is out of date is replayed as.
STALE = "This was looked up earlier and is out of date now."
SUMMARY_HEADING = "Earlier in this conversation, in short:"



@dataclass(frozen=True)
class Part:
    """One piece of a prompt, and what it is."""

    role: str  # user | assistant
    kind: str
    content: str
    # The tools whose results it holds, for the kinds that hold any.
    tools: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"role": self.role, "kind": self.kind, "content": self.content,
                "tools": list(self.tools)}

    @classmethod
    def from_dict(cls, data: dict) -> "Part":
        return cls(data["role"], data["kind"], data["content"], tuple(data.get("tools") or ()))


@dataclass(frozen=True)
class Turn:
    """One earlier exchange: a message and its answer."""

    question: str
    answer: str
    # What was sent, from the message to the answer. Empty for a turn known
    # only by its two messages: an older conversation, or a run that kept
    # nothing.
    parts: tuple[Part, ...] = ()
    # The system text it was sent under, by its ``key``.
    system_key: str | None = None
    # When it was answered, in seconds since the epoch.
    at: float = 0.0


@dataclass
class Window:
    """A prompt fitted to the room there is."""

    # The prompt, as parts and as the messages they make.
    parts: list[Part]
    messages: list[dict]
    # Earlier turns replayed, and earlier turns given only as a line of the
    # summary.
    turns: int = 0
    summarised: int = 0
    # The prompt as counted.
    tokens: int = 0


def key(system: str) -> str:
    """A system text as a turn remembers it."""
    return hashlib.sha256(system.encode("utf-8")).hexdigest()[:16]


def merged(parts: Iterable[Part]) -> list[dict]:
    """Parts as messages. Turns always alternate: a part whose speaker is the
    last one's is joined onto it, after a blank line."""
    out: list[dict] = []
    for part in parts:
        if out and out[-1]["role"] == part.role and part.role != "system":
            out[-1]["content"] += "\n\n" + part.content
        else:
            out.append({"role": part.role, "content": part.content})
    return out


def turns_from_history(history: Iterable[dict]) -> list[Turn]:
    """An older conversation's messages as turns, each a message and the
    answer that followed it, if any did."""
    turns: list[Turn] = []
    question: str | None = None
    for message in history:
        if message["role"] == "user":
            if question is not None:
                turns.append(Turn(question, ""))
            question = message["content"]
        elif message["role"] == "assistant":
            turns.append(Turn(question or "", message["content"]))
            question = None
    if question is not None:
        turns.append(Turn(question, ""))
    return turns


def turns_from(messages: Iterable[dict], records: Iterable[dict]) -> list[Turn]:
    """A stored conversation as turns: what was sent where it was kept, and
    the two messages where it was not.

    ``messages`` are the conversation's rows, oldest first, each with its
    ``id``, ``role``, ``content`` and ``created_at``. ``records`` are what was
    kept of each answered run: ``question_id``, ``answer_id``, ``system_key``,
    ``parts`` (a list of part dicts) and ``created_at``.
    """
    kept = {r["question_id"]: r for r in records}
    rows = list(messages)
    turns: list[Turn] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        if row["role"] != "user":
            # An answer with no message before it: kept as a turn of its own.
            turns.append(Turn("", row["content"], at=row.get("created_at") or 0.0))
            i += 1
            continue
        answer = rows[i + 1] if i + 1 < len(rows) and rows[i + 1]["role"] == "assistant" else None
        record = kept.get(row.get("id"))
        if record is not None and answer is not None and record.get("answer_id") == answer.get("id"):
            turns.append(Turn(
                row["content"], answer["content"],
                parts=tuple(Part.from_dict(p) for p in record["parts"]),
                system_key=record.get("system_key"),
                at=record.get("created_at") or answer.get("created_at") or 0.0,
            ))
        else:
            turns.append(Turn(
                row["content"], answer["content"] if answer else "",
                at=(answer or row).get("created_at") or 0.0,
            ))
        i += 2 if answer is not None else 1
    return turns


# --------------------------------------------------------------- replaying --


def stale(part: Part) -> Part:
    """A part whose results are out of date, as it is replayed from then on.
    The same text every time, so the prompt changes once."""
    if part.content.startswith("<data"):
        tool = part.tools[0] if part.tools else ""
        return Part(part.role, part.kind, f'<data tool="{tool}">{STALE}</data>', part.tools)
    return Part(part.role, part.kind, STALE, part.tools)


def compact(turn: Turn) -> list[Part]:
    """A turn as it is replayed under another system text: what was asked,
    cut to ``TURN_ROOM``, what was found for it, and the answer."""
    parts = [Part("user", "question", clip(turn.question, TURN_ROOM))] if turn.question else []
    parts += [p for p in turn.parts if p.kind in FOUND]
    if turn.answer:
        parts.append(Part("assistant", "answer", turn.answer))
    return parts


def replayed(
    turn: Turn,
    system_key: str,
    *,
    now: float,
    fresh_for: Callable[[str], float | None] = lambda tool: None,
) -> list[Part]:
    """What the next prompt reads of an earlier turn.

    As it was sent when it was sent under this same system text, and compact
    when not. A result older than its tool says a result stays good for is
    replaced by ``STALE``.
    """
    verbatim = turn.system_key == system_key and bool(turn.parts)
    parts = list(turn.parts) if verbatim else compact(turn)

    def gone(part: Part) -> bool:
        if part.kind not in FOUND:
            return False
        lives = [fresh_for(tool) for tool in part.tools]
        return any(life is not None and now - turn.at > life for life in lives)

    return [stale(p) if gone(p) else p for p in parts]


def summary(turns: list[Turn], budget: int = SUMMARY_TOKENS) -> Part | None:
    """Turns that no longer fit, each as a line: the message, and the first
    sentence of its answer. As many of the latest as fit ``budget``."""
    lines: list[str] = []
    used = estimate(SUMMARY_HEADING) + 1
    for turn in reversed(turns):
        said = first_sentence(turn.answer.strip()) if turn.answer else ""
        line = "- " + clip(" ".join(turn.question.split()), 25)
        if said:
            line += " — " + clip(" ".join(said.split()), 25)
        cost = estimate(line) + 1
        if used + cost > budget:
            break
        lines.insert(0, line)
        used += cost
    if not lines:
        return None
    return Part("user", "summary", SUMMARY_HEADING + "\n" + "\n".join(lines))


def cap(text: str, room: int, count: Callable[[str], int]) -> tuple[str, bool]:
    """A message cut to ``room`` tokens from its start, if it is longer, and
    whether it was. Cut on an estimate first, then checked with ``count`` and
    cut again where the estimate was short of the real count."""
    if count(text) <= room:
        return text, False
    budget = room
    cut = clip(text, budget)
    for _ in range(4):
        counted = count(cut)
        if counted <= room:
            break
        budget = max(1, int(budget * room / counted * 0.95))
        cut = clip(text, budget)
    return cut, True


# ----------------------------------------------------------------- fitting --


@dataclass
class _Memo:
    """A count that asks once for each text."""

    count: Callable[[str], int]
    seen: dict[str, int] = field(default_factory=dict)

    def __call__(self, text: str) -> int:
        if text not in self.seen:
            self.seen[text] = self.count(text)
        return self.seen[text]


def blocks(sizes: list[int], step: int = TRIM_STEP) -> list[int]:
    """Where a cut may fall, given each turn's size: at the start of each
    block of at least ``step`` tokens, counted from the first turn, and after
    the last. The same turns give the same places, so a cut stays put until
    the window has outgrown it by a block."""
    starts: list[int] = []
    run = 0
    for index, size in enumerate(sizes):
        if run >= step:
            starts.append(index)
            run = 0
        run += size
    starts.append(len(sizes))
    return starts


def fit(
    system: str,
    turns: list[Turn],
    now_parts: list[Part],
    *,
    room: int,
    count: Callable[[str], int],
    now: float,
    fresh_for: Callable[[str], float | None] = lambda tool: None,
    system_key: str | None = None,
) -> Window:
    """The prompt for this message: ``system``, the conversation so far, and
    ``now_parts``, the message and whatever goes with it, in at most ``room``
    tokens.

    Everything, if it fits. If not, the oldest turns are given as a summary,
    cut at the start of a block. Where it is cut is chosen on an estimate,
    scaled by how far the estimate was off for the whole conversation, and
    checked with ``count``; a check that fails moves the cut on one block.
    ``system_key`` is the key turns sent under ``system`` were kept with; it
    is worked out from ``system`` when not given.
    """
    count = _Memo(count)
    sys_key = system_key or key(system)
    head = [Part("system", "instruction", system)]
    replays = [replayed(t, sys_key, now=now, fresh_for=fresh_for) for t in turns]

    def prompt(cut: int) -> list[Part]:
        top = summary(turns[:cut]) if cut else None
        return [*head, *([top] if top else []), *(p for r in replays[cut:] for p in r), *now_parts]

    def window(parts: list[Part], cut: int, used: int) -> Window:
        return Window(parts, merged(parts), turns=len(turns) - cut, summarised=cut, tokens=used)

    whole = prompt(0)
    used = count(render_chatml(merged(whole)))
    if used <= room or not turns:
        return window(whole, 0, used)

    sizes = [estimate(render_chatml(merged(r))) for r in replays]
    # How the estimate compares with the count, for this conversation.
    scale = used / max(1, estimate(render_chatml(merged(whole))))
    last = len(turns)
    for cut in blocks(sizes)[:-1]:
        text = render_chatml(merged(prompt(cut)))
        if estimate(text) * scale > room:
            continue
        used = count(text)
        if used <= room:
            return window(prompt(cut), cut, used)
    return window(prompt(last), last, count(render_chatml(merged(prompt(last)))))
