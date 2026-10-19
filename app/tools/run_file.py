"""run_file: run one Python file of this conversation's project, in the sandbox.

The project's files as they stand are put in the jail, read-only, at
/work/project, and the file is run there as a script, so that it can import
the others. The conversation is the call's own (``CALL``). Like the python
tool, it is only offered where the sandbox can run a program.
"""

from __future__ import annotations

from ..config import settings
from ..projects import paths
from ..sandbox import runner as sandbox
from ..store import projects
from .base import CALL, Arg, Tool, ToolResult, register
from .run_python import _observe, last_lines


def launcher(path: str) -> str:
    """The program that runs ``path`` from the project, as ``python path`` would."""
    return (
        "import os, runpy, sys\n"
        "os.chdir('/work/project')\n"
        "sys.path.insert(0, '/work/project')\n"
        f"sys.argv = [{path!r}]\n"
        f"runpy.run_path({path!r}, run_name='__main__')\n"
    )


async def _run_file(path: str) -> ToolResult:
    cid = CALL.get().conversation_id
    clean = paths.safe(path)
    files = projects.latest(cid) if cid else {}
    if clean is None or clean not in files:
        have = ", ".join(p for p in files if p.endswith(".py")) or "none"
        return ToolResult(False, f"There is no Python file {path!r} in this project; it has: {have}.",
                          error="no such file")
    if not clean.endswith(".py"):
        return ToolResult(False, f"{clean} is not a Python file.", error="not python")
    done = await sandbox.runner().run(
        launcher(clean), project={p: f["content"] for p, f in files.items()},
    )
    data = {
        "task": f"run {clean}", "code": files[clean]["content"], "path": clean,
        "stdout": done.stdout, "stderr": done.stderr, "exit_code": done.exit_code,
        "timed_out": done.timed_out, "ms": round(done.ms, 1), "truncated": done.truncated,
    }
    if done.timed_out:
        return ToolResult(False, f"{clean} was stopped after {sandbox.WALL_SECONDS:.0f} seconds.",
                          card="python", data=data, error="timeout")
    if done.exit_code == 0:
        printed = done.stdout.strip()
        said = f"{clean} printed: {printed[:120]}" if printed else f"{clean} ran and printed nothing."
    else:
        failure = last_lines(done.stderr, 300).splitlines()[-1:] or ["no message"]
        said = f"{clean} stopped with an error: {failure[0]}"
    return ToolResult(True, said, card="python", data=data)


register(Tool(
    name="run_file",
    label="Run file",
    glyph="▶",
    description="Run a Python file of this conversation's project in the sandbox, and read what it prints.",
    args=(Arg("path", "string", True, "the .py file's path in the project", max_len=160),),
    run=_run_file,
    card="python",
    example="/runfile app.py",
    budget=500,
    observe=_observe,
    timeout=sandbox.WALL_SECONDS + 5,
    requires=lambda: settings.sandbox != "off" and sandbox.runner().available(),
))
