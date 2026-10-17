"""read_file: one file of this conversation's project, as it stands now.

An answer that wrote a file is replayed whole for as long as the window has
room for it. When it no longer has, the model is still told which files the
project has, and reads one back with this before it changes it. The
conversation is the call's own (``CALL``), never an argument the model gives.
"""

from __future__ import annotations

from ..projects import paths
from ..store import projects
from .base import CALL, Arg, Tool, ToolResult, register


async def _read_file(path: str) -> ToolResult:
    cid = CALL.get().conversation_id
    clean = paths.safe(path)
    if not cid or clean is None:
        return ToolResult(False, f"There is no file called {path!r} in this project.", error="no such file")
    found = projects.file(cid, clean)
    if found is None:
        have = ", ".join(f["path"] for f in projects.manifest(cid)) or "none yet"
        return ToolResult(False, f"There is no file called {clean!r}; the project has: {have}.",
                          error="no such file")
    return ToolResult(
        True, f"{clean}, version {found['version']} ({found['lines']} lines)", card="file",
        data={"path": clean, "version": found["version"], "lang": found["lang"],
              "lines": found["lines"], "content": found["content"]},
    )


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    data = result.data
    head = f"{data.get('path')} as it stands:\n"
    return head + (data.get("content") or "")


register(Tool(
    name="read_file",
    label="Read file",
    glyph="▭",
    description="Read a file of this conversation's project, as it stands now.",
    args=(Arg("path", "string", True, "the file's path in the project", max_len=160),),
    run=_read_file,
    card="file",
    example="/file app.py",
    budget=900,
    observe=_observe,
))
