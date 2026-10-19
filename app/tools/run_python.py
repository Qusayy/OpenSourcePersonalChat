"""python: run a short program in the sandbox, and read what it prints.

The model writes the program as the call's ``code`` parameter: raw text in the
call format, so it needs no escaping. A program the user gave themselves
(``/python ...``) is run as they gave it.

It runs in app/sandbox: no network, no server files, limits on everything.
Where the sandbox cannot run here, the tool is not offered.
"""

from __future__ import annotations

from ..config import settings
from ..sandbox import runner as sandbox
from ..text import clip
from .base import Arg, Tool, ToolResult, register

# What a program's output is read as, at most, beside the tool's budget.
ERRORS_KEPT = 1200


def last_lines(text: str, limit: int) -> str:
    """The end of some output, where a traceback says what went wrong."""
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


async def _python(code: str) -> ToolResult:
    if not (code or "").strip():
        return ToolResult(False, "There was no program to run.", error="no program")
    done = await sandbox.runner().run(code)
    data = {
        "task": "run the program",
        "code": code,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "exit_code": done.exit_code,
        "timed_out": done.timed_out,
        "ms": round(done.ms, 1),
        "truncated": done.truncated,
    }
    if done.timed_out:
        return ToolResult(
            False, f"The program was stopped after {sandbox.WALL_SECONDS:.0f} seconds.",
            card="python", data=data, error="timeout",
        )
    printed = done.stdout.strip()
    if done.exit_code == 0:
        said = f"It printed: {clip(printed, 120)}" if printed else "It ran and printed nothing."
    else:
        failure = last_lines(done.stderr, 300).splitlines()[-1:] or ["no message"]
        said = f"It stopped with an error: {failure[0]}"
    return ToolResult(True, said, card="python", data=data)


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    """What came of the program, as the model reads it: if it failed, the
    end of what it said about why, which is where a traceback names the
    error; and as much of what it printed as is left room for."""
    data = result.data
    out = (data.get("stdout") or "").strip()
    err = (data.get("stderr") or "").strip()
    code = data.get("exit_code")
    tail = ""
    if code not in (0, None) and err:
        tail = f"\nIt exited with code {code}. The end of what it said:\n" + last_lines(
            err, min(ERRORS_KEPT, budget * 2)
        )
    left = max(20, budget - (len(tail.encode("utf-8")) // 3) - 10)
    head = ("It printed:\n" + clip(out, left)) if out else "It printed nothing."
    return head + tail


register(Tool(
    name="python",
    label="Python",
    glyph="⌨",
    description=(
        "Run a short Python program to calculate, simulate or analyse something exactly, "
        "and read what it prints."
    ),
    args=(Arg("code", "string", True, "the complete Python program; it must print its result"),),
    run=_python,
    card="python",
    example="/python print(sum(range(101)))",
    budget=500,
    observe=_observe,
    # Its own clock stops a program first, and says so.
    timeout=sandbox.WALL_SECONDS + 5,
    requires=lambda: settings.sandbox != "off" and sandbox.runner().available(),
))
