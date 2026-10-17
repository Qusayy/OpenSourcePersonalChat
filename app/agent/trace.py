"""What a run did, kept as it happens: each step and what it cost, the calls
it made, and how it ended.

The run writes here before it announces anything, and stores nothing itself;
whoever asked for the run decides what to keep.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from ..ledger import ledger
from ..tools.base import ToolResult
from .context import Part, Turn
from .route import ToolCall


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
    # Whether the answer was checked against what was looked up, and what
    # was found wrong with it: the figures in it that nothing returned, and
    # the line the visitor is shown about them.
    checked: bool = False
    unsupported: list[str] = field(default_factory=list)
    correction: str | None = None
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
        """The run in a line, and each of its steps: what the page is told
        when it ends. The same as a stored answer's run reads back, from the
        same function (``app.ledger``), with the run's own stopwatch beside
        it."""
        return {
            "id": self.run_id,
            "outcome": self.outcome,
            **ledger(self.steps),
            "wall_ms": None if self.wall_ms is None else round(self.wall_ms, 1),
        }


def pass_ms(queue_wait_ms: float | None, total_ms: float | None) -> float:
    """What a pass cost: its wait for the slot and its time inside it."""
    return (queue_wait_ms or 0.0) + (total_ms or 0.0)


def total(figures) -> int | None:
    """Token counts added up. None if none of the passes ever ran."""
    known = [f for f in figures if f is not None]
    return sum(known) if known else None
