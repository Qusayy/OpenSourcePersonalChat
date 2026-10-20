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

Where the model thinks, each row says how long each thought was and how it
ended, and the summary how often a thought was cut at its budget and how the
tasks whose thought was cut fared against those whose thought was not.
"""

from __future__ import annotations

import statistics
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from app import agent
from app.agent import confirm
from app.agent.context import Turn as Sent
from app.agent.loop import NO_ANSWER
from app.config import Settings, settings
from app.llm import Engine
from app.agent.transcript import NO_MORE_CALLS
from app.projects import paths
from app.store import db, projects

from . import fixtures
from .score import NO_TOOL, REFUSED, route_match, score
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
            # The tools that were called and reached their tool, in order.
            called=[s.name for s in steps if s.kind == "tool" and s.error not in REFUSED],
            # Whether the model ended without saying anything to answer with.
            no_answer=(self.last.answer or "").strip() == NO_ANSWER,
        )
        thoughts = self.thoughts
        if thoughts:
            row.update(
                think_tokens=sum(tokens for tokens, _, _ in thoughts),
                # Each thought, in order: how long it was and how it ended.
                thinks=[[tokens, end] for tokens, end, _ in thoughts],
                think_cut=sum(end == "budget" for _, end, _ in thoughts),
                think_eos=sum(end == "eos" for _, end, _ in thoughts),
            )
        if timed:
            waiting = sum(s.queue_ms or 0 for s in models)
            reading = sum(s.first_token_ms or 0 for s in models)
            inside = sum(s.total_ms for s in models) - waiting
            writing = max(inside - reading, 0.0)
            row["ms"] = {
                "waiting": round(waiting, 1),
                "reading": round(reading, 1),
                "writing": round(writing, 1),
                "tools": round(sum(s.total_ms for s in steps if s.kind == "tool"), 1),
                "total": round(sum(s.total_ms for s in steps), 1),
            }
            # How fast the model wrote, thinking included, at the length its
            # replies really were: the speed a visitor waits on.
            row["write_tps"] = round(row["output_tokens"] / (writing / 1000), 2) if writing else None
        return row

    @property
    def thoughts(self) -> list[tuple[int, str, bool]]:
        """Every thought of every turn, in order: ``(tokens, how it ended,
        whether it was the first reply of its turn)``."""
        found: list[tuple[int, str, bool]] = []
        for turn in self.turns:
            first = True
            for s in turn.trace.steps:
                if not s.model:
                    continue
                got = s.input or {}
                if "think_end" in got:
                    found.append((int(got.get("think_tokens") or 0), got["think_end"], first))
                first = False
        return found


async def run_task(
    task: Task, *, engine: Engine, seed: int | None = None, loop: bool | None = None
) -> Outcome:
    """Play one task's messages through a run each, then score the last.

    ``loop`` is whether the model may call one tool after another, for a
    task that does not ask for the loop itself; left unset, the setting
    decides, as on the site. A task is a conversation of its own, and its
    project is that conversation's: files a turn writes are there for the
    next, and the model is told of them as the site tells it. A task may
    begin with files in it (``project``).

    Where the run asks before a change, the task answers at once with its
    ``confirm``, as the user would on the page; nobody is left to wait for.
    """
    began = time.perf_counter()
    history: list[dict] = []
    sent: list[Sent] = []
    turns: list[Turn] = []
    with fixtures.recorded(task.tools):
        scope = f"task:{task.id}"
        projects.forget(scope)
        for path, content in task.project.items():
            projects.add(scope, path, content, lang=paths.language(path), run_id="seed")
        for text in task.turns:
            trace = agent.Trace()
            events: list[tuple[str, Any]] = []
            async for event in agent.run(
                text, history=list(history),
                trace=trace, engine=engine, tools_enabled=True, seed=seed,
                loop=True if task.loop else loop, context=list(sent),
                scope=scope, project=projects.manifest(scope),
            ):
                events.append(event)
                if event[0] == "confirm":
                    confirm.answer(trace.run_id, event[1]["id"], task.confirm)
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
    config: dict | None = None,
) -> dict:
    """Run every task this engine can run, and return the report.

    ``engine`` is the loaded model for ``engine_kind="real"``. A scripted run
    makes an engine per task from the task's own script. ``loop`` is what the
    site's setting would be: unset, it is the setting; False makes every
    task that does not ask for the loop choose once, the way to see what the
    loop is worth. Scripts are written for the loop on. ``wording`` puts other
    texts in front of the model for this run only: see ``evals.wording``.
    ``config`` overrides the engine's settings for this run only (how the
    model thinks, and for how long), so one server can be measured several
    ways without its settings being edited between runs.
    """
    if engine_kind not in ("scripted", "real"):
        raise ValueError(f"unknown engine {engine_kind!r}")
    if engine_kind == "real" and engine is None:
        raise ValueError("a real run needs a loaded engine")
    wanted = set(categories) if categories else None
    scripted_cfg = _scripted(config)

    # What a task keeps (a note, a project file) goes to a file that lasts
    # for this run, never to the site's own database.
    with applied(wording), configured(engine if engine_kind == "real" else None, config), \
            tempfile.TemporaryDirectory() as scratch, db.elsewhere(Path(scratch) / "evals.db"):
        cfg = engine.cfg if engine_kind == "real" else scripted_cfg
        thinking = {"mode": cfg.think_mode, "budget": int(cfg.think_budget or 1024)}
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
            scripted = ScriptedEngine(task.script or (), cfg=scripted_cfg)
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
        # How the model thought in this run, and its budget a reply.
        "thinking": thinking,
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


def _scripted(config: dict | None) -> Settings:
    """The settings a scripted engine runs with: no model, no delay, and the
    run's overrides."""
    return settings.model_copy(update={"mock": True, "mock_delay": 0.0, **(config or {})})


@contextmanager
def configured(engine: Engine | None, config: dict | None) -> Iterator[None]:
    """The engine with ``config`` over its settings for as long as this
    lasts, and its own settings back after."""
    if engine is None or not config:
        yield
        return
    was = engine.cfg
    engine.cfg = was.model_copy(update=config)
    try:
        yield
    finally:
        engine.cfg = was


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
    tools_right = [tool for tool, _ in routed if tool is not None]
    args_right = [args for _, args in routed if args is not None]
    # The calls the model wrote, and those that named no tool there is or
    # could not be read.
    written = [s for o in ran for t in o.turns for s in t.trace.steps
               if s.kind == "tool" and s.via == "model"]
    unreadable = [s for s in written if s.error in NO_TOOL]
    repeated = [s for s in written if s.error == "repeated"]
    # What the last reply of each run read: whether a limit ended its calls.
    read = ["\n".join(m["content"] for m in (o.last.prompt or [])) for o in ran]
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
        "argument_match": _share(sum(args_right), len(args_right)),
        # Calls the model wrote that named no tool or could not be read.
        "invalid_call_rate": _share(len(unreadable), len(written)),
        # Calls it wrote that it had already made in the same message.
        "repeat_rate": _share(len(repeated), len(written)),
        # Runs that made a second tool call: what the loop is for.
        "second_step_rate": _share(sum(r["steps"] >= 2 for r in rows), len(rows)),
        # Runs whose calls were ended by a limit.
        "limit_rate": _share(sum(NO_MORE_CALLS in text for text in read), len(read)),
        # Of the answers checked against their lookups: those with a figure
        # nothing returned, and those that drew any correction at all.
        "unsupported_rate": _share(sum(bool(t.unsupported) for t in checked), len(checked)),
        "correction_rate": _share(sum(bool(t.correction) for t in checked), len(checked)),
        "steps": _spread([float(r["steps"]) for r in rows]),
        "passes": _spread([float(r["passes"]) for r in rows]),
        "prompt_tokens": _spread([float(r["prompt_tokens"]) for r in rows]),
        "output_tokens": _spread([float(r["output_tokens"]) for r in rows]),
        # Answers that were the run's own "could not finish".
        "no_answer_rate": _share(sum(bool(r.get("no_answer")) for r in rows), len(rows)),
        # How often each tool was called, over every task.
        "calls_by_tool": dict(sorted(Counter(n for r in rows for n in r.get("called", [])).items())),
        **_thinking(ran),
    }
    if timed:
        summary["write_tps"] = _spread([r["write_tps"] for r in rows if r.get("write_tps")])
        # Over the tasks whose every pass the server reported on.
        summary["read_tokens"] = _spread(
            [float(r["read_tokens"]) for r in rows if r.get("read_tokens") is not None]
        )
        summary["seconds"] = {
            part: _spread([r["ms"][part] / 1000 for r in rows])
            for part in ("waiting", "reading", "writing", "tools", "total")
        }
    return summary


def _thinking(ran: list[Outcome]) -> dict:
    """What the model's thinking came to over these tasks.

    A thinking pass is a reply that thought, however its thought ended; a
    reply given no budget did not think. A task was cut if any of its
    thoughts was cut at the budget. Success is over the tasks meant to pass.
    """
    thoughts = {id(o): [t for t in o.thoughts if t[1] != "skipped"] for o in ran}
    passes = [t for o in ran for t in thoughts[id(o)]]
    thinkers = [o for o in ran if thoughts[id(o)]]
    cut = {id(o) for o in thinkers if any(end == "budget" for _, end, _ in thoughts[id(o)])}
    spent = sum(tokens for tokens, _, _ in passes)
    later = sum(tokens for tokens, _, first in passes if not first)

    def success(outcomes: list[Outcome]) -> float | None:
        counted = [o for o in outcomes if not o.task.pending]
        return _share(sum(o.status == PASSED for o in counted), len(counted))

    return {
        "think_tokens": _spread([float(sum(t for t, _, _ in thoughts[id(o)])) for o in thinkers]),
        # Each thought, the first reply of a turn apart from the replies
        # after a tool: where a run's thinking goes.
        "think_first": _spread([float(t) for t, _, first in passes if first]),
        "think_later": _spread([float(t) for t, _, first in passes if not first]),
        "think_later_share": round(later / spent, 4) if spent else None,
        "budget_hit_rate": _share(sum(end == "budget" for _, end, _ in passes), len(passes)),
        "budget_task_rate": _share(len(cut), len(thinkers)),
        "think_eos_rate": _share(sum(end == "eos" for _, end, _ in passes), len(passes)),
        "success_when_cut": success([o for o in thinkers if id(o) in cut]),
        "success_when_uncut": success([o for o in thinkers if id(o) not in cut]),
    }


def _flat(figures: dict, into: str = "") -> dict[str, float]:
    """Every number in a summary under one name: a spread's median as
    ``think_tokens.median``, a part of the time as ``seconds.total.p95``."""
    flat: dict[str, float] = {}
    for key, value in figures.items():
        name = f"{into}{key}"
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            flat[name] = value
        elif isinstance(value, dict):
            flat.update(_flat(value, name + "."))
    return flat


def compare(report: dict, previous: dict) -> dict:
    """What changed between two reports: each headline figure beside the one
    before, and every task that ended differently."""
    figures = {}
    was_all = _flat(previous.get("summary", {}))
    for key, now in _flat(report["summary"]).items():
        was = was_all.get(key)
        if was is not None:
            figures[key] = {"was": was, "now": now, "change": round(now - was, 4)}
    before = {row["id"]: row["status"] for row in previous.get("tasks", [])}
    changed = [
        {"id": row["id"], "was": before.get(row["id"], "absent"), "now": row["status"]}
        for row in report["tasks"]
        if before.get(row["id"], "absent") != row["status"]
    ]
    gone = [task_id for task_id in before if task_id not in {r["id"] for r in report["tasks"]}]
    return {
        "previous": {
            k: previous.get(k) for k in ("engine", "seed", "created", "thinking", "rules", "task_file")
        },
        "figures": figures,
        "changed": changed,
        "removed": gone,
    }
