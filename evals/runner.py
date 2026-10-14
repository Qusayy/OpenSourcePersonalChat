"""Running the tasks and adding up what happened.

A task is played through the same ``app.agent.run`` the site uses, with
recorded tool results in place of the network. Nothing is stored: the runner
reads the trace each run leaves and never touches the database.

Two engines:

* **scripted** — each task brings what the model says. This measures the code
  around the model and nothing about the model. It needs no weights and runs
  in well under a second, so it is part of the ordinary test suite.
* **real** — the model the site uses, loaded once, with a fixed seed. This is
  the only source of any claim about what the agent can do. On a small server
  it occupies every core for the length of the suite, like the benchmark.

A scripted report says in so many words that its token counts are estimates
and carries no timings: a script's speed is the speed of a loop in Python.

How much of each prompt the model actually read, the rest coming from its
cache, is in a row only where the model server reported it for every pass.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Any, Iterable

from app import agent
from app.config import settings
from app.context import Turn as Sent
from app.llm import Engine
from app.personas import get_persona
from app.skills import get_skill

from . import fixtures
from .score import route_match, score, skill_match
from .scripted import ScriptedEngine
from .tasks import Task, Turn
from .wording import Wording, applied

# How a task can end.
PASSED = "passed"
FAILED = "failed"
PENDING = "pending"            # marked as a known gap, and it failed as marked
UNEXPECTED = "unexpected pass"  # marked as a known gap, and it passed: the mark must come off
SKIPPED = "skipped"            # cannot be run with this engine
# What makes a run of the suite a failure.
BAD = (FAILED, UNEXPECTED)


@dataclass
class Outcome:
    task: Task
    status: str
    failures: list[str]
    turns: list[Turn]
    seconds: float = 0.0

    @property
    def last(self) -> Any:
        """The trace of the turn the expectations are about."""
        return self.turns[-1].trace if self.turns else None

    def row(self, timed: bool) -> dict:
        """This task as one line of a report."""
        row: dict[str, Any] = {
            "id": self.task.id,
            "category": self.task.category,
            "status": self.status,
            "failures": self.failures,
        }
        if self.task.pending:
            row["pending"] = self.task.pending
        if not self.turns:
            return row
        if self.last.correction:
            # Read off a run on the model, it is the check's false alarms
            # and its catches, one by one.
            row["correction"] = self.last.correction
        steps = [s for turn in self.turns for s in turn.trace.steps]
        models = [s for s in steps if s.model]
        reported = [s.new_tokens for s in models]
        row.update(
            steps=sum(1 for s in steps if s.kind == "tool"),
            passes=len(models),
            prompt_tokens=sum(s.prompt_tokens or 0 for s in models),
            # Of those, what the model read. A total with a pass missing from
            # it would look like a cache that worked, so there is none.
            read_tokens=sum(reported) if models and None not in reported else None,
            output_tokens=sum(s.output_tokens or 0 for s in models),
        )
        if timed:
            waiting = sum(s.queue_ms or 0 for s in models)
            reading = sum(s.first_token_ms or 0 for s in models)
            inside = sum(s.total_ms for s in models) - waiting
            row["ms"] = {
                "waiting": round(waiting, 1),
                "reading": round(reading, 1),
                "writing": round(max(inside - reading, 0.0), 1),
                "tools": round(sum(s.total_ms for s in steps if s.kind == "tool"), 1),
                "total": round(sum(s.total_ms for s in steps), 1),
            }
        return row


async def run_task(
    task: Task, *, engine: Engine, seed: int | None = None, loop: bool | None = None
) -> Outcome:
    """Play one task's messages through a run each, then score the last.

    ``loop`` is whether a tool the model chose earns it another choice, for a
    task that does not ask for the loop itself; left unset, the setting
    decides, as on the site.
    """
    began = time.perf_counter()
    persona = get_persona(get_skill(task.skill).persona if get_skill(task.skill) else task.persona)
    history: list[dict] = []
    sent: list[Sent] = []
    turns: list[Turn] = []
    with fixtures.recorded(task.tools):
        for text in task.turns:
            trace = agent.Trace()
            events = [
                event
                async for event in agent.run(
                    text, persona=persona, skill=get_skill(task.skill), history=list(history),
                    trace=trace, engine=engine, tools_enabled=True, seed=seed,
                    loop=True if task.loop else loop, context=list(sent),
                )
            ]
            turns.append(Turn(user=text, events=events, trace=trace))
            # The next message is read with this exchange before it, as the
            # site replays a conversation: what was said, and what was sent.
            history.append({"role": "user", "content": text})
            if trace.answer and trace.answer.strip():
                history.append({"role": "assistant", "content": trace.answer})
                sent.append(trace.turn(text))
            else:
                sent.append(Sent(text, ""))

    failures = score(task, turns)
    if task.pending:
        status = PENDING if failures else UNEXPECTED
    else:
        status = FAILED if failures else PASSED
    return Outcome(task, status, failures, turns, time.perf_counter() - began)


async def run_suite(
    tasks: Iterable[Task],
    *,
    engine_kind: str,
    engine: Engine | None = None,
    seed: int | None = 0,
    categories: Iterable[str] | None = None,
    loop: bool | None = None,
    wording: Wording | None = None,
) -> dict:
    """Run every task this engine can run, and return the report.

    ``engine`` is the loaded model for ``engine_kind="real"``. A scripted run
    makes an engine per task from the task's own script. ``loop`` is what the
    site's setting would be: unset, it is the setting; False makes every
    task that does not ask for the loop choose once, the way to see what the
    loop is worth. Scripts are written for the loop on. ``wording`` puts other
    texts in front of the model for this run only: see ``evals.wording``.
    """
    if engine_kind not in ("scripted", "real"):
        raise ValueError(f"unknown engine {engine_kind!r}")
    if engine_kind == "real" and engine is None:
        raise ValueError("a real run needs a loaded engine")
    wanted = set(categories) if categories else None

    with applied(wording):
        outcomes: list[Outcome] = []
        for task in tasks:
            if wanted is not None and task.category not in wanted:
                continue
            if not task.runs_with(engine_kind):
                outcomes.append(Outcome(task, SKIPPED, [], []))
                continue
            if engine_kind == "real":
                outcomes.append(await run_task(task, engine=engine, seed=seed, loop=loop))
                continue
            scripted = ScriptedEngine(task.script or ())
            try:
                outcomes.append(await run_task(task, engine=scripted, seed=seed, loop=loop))
            finally:
                # No worker thread may outlive the loop it reports to.
                await scripted.settled()

    timed = engine_kind == "real"
    return {
        "engine": engine_kind,
        "seed": seed,
        # Whether the loop was on for the tasks that do not ask for it.
        "loop": settings.agent_loop if loop is None else loop,
        # Other texts the model was given in place of its own, if any were.
        "rules": wording.describe() if wording else None,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": _model(engine) if timed else None,
        # Without a tokenizer a token is counted as four characters.
        "tokens_are_estimates": not timed,
        "summary": summarise(outcomes, timed=timed),
        "by_category": {
            category: summarise([o for o in outcomes if o.task.category == category], timed=timed)
            for category in sorted({o.task.category for o in outcomes})
        },
        "tasks": [o.row(timed) for o in outcomes],
    }


def _model(engine: Engine) -> dict:
    info = engine.info()
    return {key: info[key] for key in ("model", "quant", "n_ctx", "n_threads", "hardware")}


def _share(hits: int, of: int) -> float | None:
    return round(hits / of, 4) if of else None


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean": round(statistics.fmean(ordered), 2),
        "median": round(statistics.median(ordered), 2),
        # The value 95% of tasks came in at or under.
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2),
        "max": round(ordered[-1], 2),
    }


def summarise(outcomes: list[Outcome], *, timed: bool) -> dict:
    """The figures a report leads with.

    Every share is over the tasks it applies to: routing accuracy over the
    tasks that say which tool was right, argument match over those of them
    that chose it and say what it should have been given.
    """
    ran = [o for o in outcomes if o.status != SKIPPED]
    counted = [o for o in ran if not o.task.pending]
    routed = [route_match(o.task, o.turns[-1]) for o in ran]
    skills = [hit for o in ran if (hit := skill_match(o.task, o.turns[-1])) is not None]
    tools_right = [tool for tool, _ in routed if tool is not None]
    args_right = [args for _, args in routed if args is not None]
    decisions = [s for o in ran for t in o.turns for s in t.trace.steps
                 if s.kind == "route" and s.model]
    repairs = [s for o in ran for t in o.turns for s in t.trace.steps if s.kind == "repair"]
    # How each run's looking-up ended, read off the last line its answer pass
    # was given.
    cues = [(o.last.prompt or [{"content": ""}])[-1]["content"] for o in ran]
    # The answers that were held against what was looked up for them.
    checked = [o.last for o in ran if o.last is not None and o.last.checked]
    rows = [o.row(timed) for o in ran]

    summary = {
        "tasks": len(outcomes),
        "ran": len(ran),
        "passed": sum(o.status == PASSED for o in ran),
        "failed": sum(o.status == FAILED for o in ran),
        "pending": sum(o.status == PENDING for o in ran),
        "unexpected_passes": sum(o.status == UNEXPECTED for o in ran),
        "skipped": len(outcomes) - len(ran),
        # Over the tasks that are meant to pass.
        "task_success": _share(sum(o.status == PASSED for o in counted), len(counted)),
        "routing_accuracy": _share(sum(tools_right), len(tools_right)),
        # Over the tasks that say which skill, if any, should have run.
        "skill_selection": _share(sum(skills), len(skills)),
        "argument_match": _share(sum(args_right), len(args_right)),
        # Decisions that did not come back as a complete JSON object. Under a
        # grammar this can only be a reply that ran out of tokens.
        "invalid_output_rate": _share(
            sum(s.error == "the reply was not a complete JSON object" for s in decisions),
            len(decisions),
        ),
        # Decisions whose call could not be made and was sent back once.
        "repair_rate": _share(len(repairs), len(decisions)),
        # Runs that made a second tool call: what the loop is for.
        "second_step_rate": _share(sum(r["steps"] >= 2 for r in rows), len(rows)),
        # Runs whose looking-up was ended by a limit, and by the model asking
        # for a lookup it had already made.
        "limit_rate": _share(sum(agent.OUT_OF_LOOKUPS in cue for cue in cues), len(cues)),
        "repeat_rate": _share(sum(agent.ALREADY_LOOKED_UP in cue for cue in cues), len(cues)),
        # Of the answers checked against their lookups: those with a figure
        # nothing returned, and those that drew any correction at all.
        "unsupported_rate": _share(sum(bool(t.unsupported) for t in checked), len(checked)),
        "correction_rate": _share(sum(bool(t.correction) for t in checked), len(checked)),
        "steps": _spread([float(r["steps"]) for r in rows]),
        "passes": _spread([float(r["passes"]) for r in rows]),
        "prompt_tokens": _spread([float(r["prompt_tokens"]) for r in rows]),
        "output_tokens": _spread([float(r["output_tokens"]) for r in rows]),
    }
    if timed:
        # Over the tasks whose every pass the server reported on.
        summary["read_tokens"] = _spread(
            [float(r["read_tokens"]) for r in rows if r.get("read_tokens") is not None]
        )
        summary["seconds"] = {
            part: _spread([r["ms"][part] / 1000 for r in rows])
            for part in ("waiting", "reading", "writing", "tools", "total")
        }
    return summary


def compare(report: dict, previous: dict) -> dict:
    """What changed between two reports: each headline figure beside the one
    before, and every task that ended differently."""
    figures = {}
    for key, now in report["summary"].items():
        was = previous.get("summary", {}).get(key)
        if isinstance(now, (int, float)) and isinstance(was, (int, float)):
            figures[key] = {"was": was, "now": now, "change": round(now - was, 4)}
    before = {row["id"]: row["status"] for row in previous.get("tasks", [])}
    changed = [
        {"id": row["id"], "was": before.get(row["id"], "absent"), "now": row["status"]}
        for row in report["tasks"]
        if before.get(row["id"], "absent") != row["status"]
    ]
    gone = [task_id for task_id in before if task_id not in {r["id"] for r in report["tasks"]}]
    return {
        "previous": {k: previous.get(k) for k in ("engine", "seed", "created")},
        "figures": figures,
        "changed": changed,
        "removed": gone,
    }
