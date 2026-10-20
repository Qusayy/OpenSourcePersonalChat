"""Holding what a task did against what it was expected to do.

Everything here reads the trace a run leaves behind, not the page's event
stream: the trace is what the server itself keeps, so a property checked here
is a property of the record, and the record is what will be looked at when a
run in production goes wrong.
"""

from __future__ import annotations

from typing import Any

from .tasks import Task, Turn

# What a call that never reached its tool fails with: a call refused before it
# was made, by the run or by the dispatcher; one the model wrote that named no
# tool there is or could not be read; and one it had already made.
REFUSED = frozenset({
    "not allowed", "bad input", "missing input", "bad call", "unknown tool", "repeated",
})
# Of those, the calls that named no tool at all.
NO_TOOL = frozenset({"bad call", "unknown tool"})


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
    """The first thing a turn did: ``(tool, arguments, tier)``.

    The tool is the first one called, by the user's command (tier "slash") or
    by the model ("model"): the first call that was made, or where every one
    was refused, the first that named a tool. A call that named no tool is no
    tool. With no call at all the tool is None and the tier is "model" if the
    model replied: it chose to answer. The arguments are what the tool was
    called with.
    """
    steps = turn.trace.steps
    tools = [s for s in steps if s.kind == "tool" and s.error not in NO_TOOL]
    ran = next((s for s in tools if s.error not in REFUSED), tools[0] if tools else None)
    if ran is not None:
        return ran.name, ran.input, ran.via
    replied = any(s.model for s in steps)
    return None, None, "model" if replied else None


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

    # The calls that were made. One the run refused, or the dispatcher did,
    # never reached its tool; the refusal is on the step that chose it.
    called = [s.name for s in trace.steps if s.kind == "tool" and s.error not in REFUSED]
    if "calls" in expect and called != expect["calls"]:
        failed.append(f"calls: {called}, expected {expect['calls']}")
    if "max_steps" in expect and len(called) > expect["max_steps"]:
        failed.append(f"steps: {len(called)} tool calls, at most {expect['max_steps']} expected")

    passes = sum(1 for s in trace.steps if s.model)
    if "max_model_passes" in expect and passes > expect["max_model_passes"]:
        failed.append(
            f"passes: {passes} through the model, at most {expect['max_model_passes']} expected"
        )

    for path, wanted in (expect.get("file_contains") or {}).items():
        content = written(turns, path)
        if content is None:
            failed.append(f"file: {path} was never written")
            continue
        failed += [f"file: {path} does not contain {t!r}" for t in wanted if t not in content]
    left = project(turns)
    failed += [f"file: {path} is still there" for path in expect.get("files_absent") or []
               if path in left]

    outcome = expect.get("outcome", "answered")
    if trace.outcome != outcome:
        failed.append(f"outcome: {trace.outcome!r}, expected {outcome!r}")

    wanted_in = expect.get("prompt_contains") or []
    wanted_out = expect.get("prompt_not_contains") or []
    if wanted_in or wanted_out:
        if not trace.prompt:
            failed.append("prompt: no reply was made, so there is no prompt to read")
        else:
            # All of it, not only the last turn: what an earlier turn carried
            # forward is as much a part of what the model read.
            read = "\n".join(m["content"] for m in trace.prompt)
            failed += [f"prompt: does not contain {t!r}" for t in wanted_in if t not in read]
            failed += [f"prompt: contains {t!r}" for t in wanted_out if t in read]

    if "corrected" in expect and bool(trace.correction) != expect["corrected"]:
        failed.append(
            f"corrected: {trace.correction!r}" if trace.correction
            else "corrected: no correction, one expected"
        )

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


def project(turns: list[Turn]) -> dict[str, str]:
    """The project as the task's turns left it, file by file: every call
    that wrote, edited, moved or deleted a file and was kept, in order."""
    files: dict[str, str] = {}
    for turn in turns:
        for call, result in turn.trace.calls:
            if not result.ok:
                continue
            data = result.data or {}
            if call.name in ("write_file", "edit_file") and data.get("path"):
                files[data["path"]] = data.get("content") or ""
            elif data.get("action") == "move":
                moving = {m["from"]: files.pop(m["from"], "") for m in data["moves"]}
                for m in data["moves"]:
                    files[m["to"]] = moving[m["from"]]
            elif data.get("action") == "delete":
                for gone in data["paths"]:
                    files.pop(gone, None)
    return files


def written(turns: list[Turn], path: str) -> str | None:
    """A project file as the task's turns left it. None if it is not there."""
    return project(turns).get(path)
