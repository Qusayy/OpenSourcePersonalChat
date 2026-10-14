"""Holding what a task did against what it was expected to do.

Everything here reads the trace a run leaves behind, not the page's event
stream: the trace is what the server itself keeps, so a property checked here
is a property of the record, and the record is what will be looked at when a
run in production goes wrong.
"""

from __future__ import annotations

from typing import Any

from app.skills import SKILLS

from .tasks import Task, Turn


def _plain(value: Any) -> Any:
    """A value as it is compared: text without regard to case or spacing,
    numbers as numbers."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value


def chosen(turn: Turn) -> tuple[str | None, dict | None, str | None]:
    """The first thing a turn decided to do: ``(tool, arguments, tier)``.

    For a skill the tool is the first one its plan called; otherwise it is the
    tool the routing step named, which is None when it named none. The
    arguments are what the tool was actually run with.
    """
    steps = turn.trace.steps
    route = next((s for s in steps if s.kind == "route"), None)
    if route is None:
        return None, None, None
    ran = next((s for s in steps if s.kind == "tool"), None)
    tool = (ran.name if ran else None) if route.via == "skill" else route.name
    args = ran.input if ran else (route.input if route.via in ("slash", "heuristic") else None)
    return tool, args, route.via


def skill_of(turn: Turn) -> str | None:
    """The skill a turn ran, whoever chose it: the visitor, by its button, or
    the model, by the last choice it made that held. None for none."""
    steps = turn.trace.steps
    armed = next((s for s in steps if s.kind == "route" and s.via == "skill"), None)
    if armed is not None:
        return armed.name
    chose = [s.name for s in steps
             if s.model and s.kind in ("route", "decide", "repair") and s.ok and s.name in SKILLS]
    return chose[-1] if chose else None


def skill_match(task: Task, turn: Turn) -> bool | None:
    """Whether the run ran the skill the task says it should have; None when
    the task does not say."""
    if "skill" not in task.expect:
        return None
    return skill_of(turn) == task.expect["skill"]


def route_match(task: Task, turn: Turn) -> tuple[bool | None, bool | None]:
    """Whether the right tool was chosen, and whether it was given the right
    arguments. Each is None when the task does not say what was right, and the
    second is None when the tool itself was wrong: there is nothing to compare.
    """
    want = task.expect.get("route")
    if want is None or "tool" not in want:
        return None, None
    tool, args, _ = chosen(turn)
    if tool != want["tool"]:
        return False, None
    if "args" not in want:
        return True, None
    got = args or {}
    return True, all(_plain(got.get(k)) == _plain(v) for k, v in want["args"].items())


def score(task: Task, turns: list[Turn]) -> list[str]:
    """Everything expected of a task that did not hold. Empty means it passed."""
    if not turns:
        return ["nothing was run"]
    turn = turns[-1]
    trace = turn.trace
    expect = task.expect
    failed: list[str] = []

    tool, args, via = chosen(turn)
    want = expect.get("route")
    if want is not None:
        if "tool" in want and tool != want["tool"]:
            failed.append(f"route: chose {tool!r}, expected {want['tool']!r}")
        if "via" in want and via != want["via"]:
            failed.append(f"route: decided by {via!r}, expected {want['via']!r}")
        if "args" in want and ("tool" not in want or tool == want["tool"]):
            got = args or {}
            for key, value in want["args"].items():
                if _plain(got.get(key)) != _plain(value):
                    failed.append(f"route: {key} was {got.get(key)!r}, expected {value!r}")

    if skill_match(task, turn) is False:
        failed.append(f"skill: ran {skill_of(turn)!r}, expected {expect['skill']!r}")

    called = [s.name for s in trace.steps if s.kind == "tool"]
    if "calls" in expect and called != expect["calls"]:
        failed.append(f"calls: {called}, expected {expect['calls']}")
    if "max_steps" in expect and len(called) > expect["max_steps"]:
        failed.append(f"steps: {len(called)} tool calls, at most {expect['max_steps']} expected")

    passes = sum(1 for s in trace.steps if s.model)
    if "max_model_passes" in expect and passes > expect["max_model_passes"]:
        failed.append(
            f"passes: {passes} through the model, at most {expect['max_model_passes']} expected"
        )

    outcome = expect.get("outcome", "answered")
    if trace.outcome != outcome:
        failed.append(f"outcome: {trace.outcome!r}, expected {outcome!r}")

    wanted_in = expect.get("prompt_contains") or []
    wanted_out = expect.get("prompt_not_contains") or []
    if wanted_in or wanted_out:
        if not trace.prompt:
            failed.append("prompt: no answer pass was made, so there is no prompt to read")
        else:
            # All of it, not only the last turn: what an earlier turn carried
            # forward is as much a part of what the model read.
            read = "\n".join(m["content"] for m in trace.prompt)
            failed += [f"prompt: does not contain {t!r}" for t in wanted_in if t not in read]
            failed += [f"prompt: contains {t!r}" for t in wanted_out if t in read]

    answer = (trace.answer or "").casefold()
    failed += [
        f"answer: does not contain {t!r}"
        for t in expect.get("answer_contains") or [] if t.casefold() not in answer
    ]
    failed += [
        f"answer: contains {t!r}"
        for t in expect.get("answer_not_contains") or [] if t.casefold() in answer
    ]
    return failed
