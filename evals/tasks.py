r"""The tasks: what to send, and what must be true afterwards.

One JSON object per line in ``tasks.jsonl``, so a task can be added by hand and
a change to one shows up as one line in a diff::

    {"id": "weather-is-looked-up", "category": "single-tool",
     "turns": ["weather in Lisbon?"],
     "tools": {"weather": "weather_lisbon.json"},
     "script": ["<tool_call>\n<function=weather>\n<parameter=location>\nLisbon\n"
                "</parameter>\n</function>\n</tool_call>", "Light rain, 18 degrees."],
     "expect": {"route": {"tool": "weather", "via": "model",
                          "args": {"location": "Lisbon"}},
                "max_model_passes": 2}}

``script`` is what the model says, message by message, when the task is run
without one: what it says and the call it may end in, in its own format. A
task with no script can only be run against the real model. ``expect``
holds properties of what happened, never a whole expected answer: with a model
that samples, two correct answers are rarely the same text.

A task marked ``pending`` is expected to fail, and says why. It is how a known
gap is written down: the day it starts passing the suite says so, and the mark
has to come off.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TASKS = Path(__file__).resolve().parent / "tasks.jsonl"

# What `expect` may hold. A key outside this is a typo, and a typo in an
# expectation is an expectation that silently never fails.
EXPECTATIONS = {
    "route",                # {"tool": name or null, "via": tier, "args": {...}}; any part may be left out
    "calls",                # the tools called, in order
    "max_steps",            # at most this many tool calls
    "max_model_passes",     # at most this many replies of the model
    "outcome",              # answered | stopped | failed; "answered" if left out
    "file_contains",        # {path: [text, ...]}: the project file as the turns left it
    "files_absent",         # paths that are not in the project once the turns are done
    "prompt_contains",      # each of these is somewhere in the prompt the last reply read
    "prompt_not_contains",
    "corrected",            # whether the answer drew a correction: a figure nothing returned
    "answer_contains",      # each of these is in the answer, whatever its case
    "answer_not_contains",
}
FIELDS = {"id", "category", "turns", "tools", "script", "scripted_only", "loop", "expect",
          "pending"}


@dataclass(frozen=True)
class Task:
    id: str
    category: str
    # What the visitor sends, message by message. Expectations are about the last.
    turns: tuple[str, ...]
    # tool -> fixture file, or a list of them in call order
    tools: dict[str, Any] = field(default_factory=dict)
    script: tuple[str, ...] | None = None
    # The script is the point of the task (it says something a model should
    # not say), so there is nothing to learn from running it with a real one.
    scripted_only: bool = False
    # Run with the loop on, whatever the settings say: the task is about what
    # happens after a tool the model chose.
    loop: bool = False
    expect: dict[str, Any] = field(default_factory=dict)
    pending: str | None = None

    def runs_with(self, engine_kind: str) -> bool:
        if engine_kind == "scripted":
            return self.script is not None
        return not self.scripted_only


@dataclass
class Turn:
    """What one message of a task did."""

    user: str
    events: list[tuple[str, Any]]
    trace: Any  # app.agent.Trace


def load_tasks(path: Path | str = TASKS) -> list[Task]:
    """Every task in the file. A line that is not a well-formed task stops the
    load and says which line: a suite that quietly skips a task it could not
    read reports a better score than it earned."""
    tasks: list[Task] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{Path(path).name}:{number}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{where}: not JSON ({exc.msg})") from None
        unknown = set(raw) - FIELDS
        if unknown:
            raise ValueError(f"{where}: unknown field {sorted(unknown)[0]!r}")
        for needed in ("id", "category", "turns"):
            if not raw.get(needed):
                raise ValueError(f"{where}: {needed!r} is missing")
        if raw["id"] in seen:
            raise ValueError(f"{where}: the id {raw['id']!r} is used twice")
        seen.add(raw["id"])
        expect = raw.get("expect") or {}
        unknown = set(expect) - EXPECTATIONS
        if unknown:
            raise ValueError(f"{where}: unknown expectation {sorted(unknown)[0]!r}")
        tasks.append(Task(
            id=raw["id"],
            category=raw["category"],
            turns=tuple(raw["turns"]),
            tools=raw.get("tools") or {},
            script=tuple(raw["script"]) if raw.get("script") is not None else None,
            scripted_only=bool(raw.get("scripted_only")),
            loop=bool(raw.get("loop")),
            expect=expect,
            pending=raw.get("pending"),
        ))
    return tasks
