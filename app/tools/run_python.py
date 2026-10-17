"""python: run a short program in the sandbox, and read what it prints.

The model chooses this tool with the task in words. It does not write the
program inside the JSON of its choice, where a small model's code comes out
mangled by escaping: the program is written in a pass of its own, as a fenced
block under a grammar, and handed to the call by the run (``CALL.program``).
A program the user gave themselves ("run this", or ``/python …``) is run as
they gave it.

It runs in app/sandbox: no network, no server files, limits on everything.
Where the sandbox cannot run here, the tool is not offered.
"""

from __future__ import annotations

from ..config import settings
from ..sandbox import runner as sandbox
from ..text import clip
from .base import CALL, Arg, Tool, ToolResult, register

# What a program's output is read as, at most, beside the tool's budget.
ERRORS_KEPT = 900


def last_lines(text: str, limit: int) -> str:
    """The end of some output, where a traceback says what went wrong."""
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


async def _python(task: str) -> ToolResult:
    code = CALL.get().program
    if not code.strip():
        return ToolResult(False, "There was no program to run.", error="no program")
    done = await sandbox.runner().run(code)
    data = {
        "task": " ".join((task or "").split()),
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
    """What came of the program, as the model reads it: what it printed,
    and, if it failed, the end of what it said about why."""
    data = result.data
    out = (data.get("stdout") or "").strip()
    err = (data.get("stderr") or "").strip()
    code = data.get("exit_code")
    head = "It printed:\n" if out else "It printed nothing.\n"
    read = head + (clip(out, max(20, budget * 2 // 3)) if out else "")
    if code not in (0, None) and err:
        read += f"\nIt exited with code {code}:\n" + last_lines(err, ERRORS_KEPT)
    return read


register(Tool(
    name="python",
    label="Python",
    glyph="⌨",
    description=(
        "Run a short Python program to calculate, simulate or analyse something exactly. "
        "Say the task; you write the program next."
    ),
    args=(Arg("task", "string", True, "what the program must work out", max_len=160),),
    run=_python,
    card="python",
    example="/python print(sum(range(101)))",
    budget=200,
    observe=_observe,
    program=True,
    # Its own clock stops a program first, and says so.
    timeout=sandbox.WALL_SECONDS + 5,
    requires=lambda: settings.sandbox != "off" and sandbox.runner().available(),
))
