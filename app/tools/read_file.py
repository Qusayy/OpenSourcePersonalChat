"""read_file: one file of this conversation's project, as it stands now.

The model reads a file back with this before it changes it, and when the user
reports an error in it: the lines come numbered, so a traceback's line number
leads straight to the line. A file longer than the model can be given at once
is read in parts, from ``start_line``. The conversation is the call's own
(``CALL``), never an argument the model gives.
"""

from __future__ import annotations

from ..projects import paths
from ..store import projects
from .base import CALL, Arg, Tool, ToolResult, register


def numbered(lines: list[str], first: int) -> str:
    """Lines of a file as the model reads them, each with its number."""
    width = len(str(first + len(lines) - 1))
    return "\n".join(f"{n:>{width}}│ {line}" for n, line in enumerate(lines, start=first))


async def _read_file(path: str, start_line: float | None = None) -> ToolResult:
    cid = CALL.get().conversation_id
    clean = paths.safe(path)
    if not cid or clean is None:
        return ToolResult(False, f"There is no file called {path!r} in this project.", error="no such file")
    found = projects.file(cid, clean)
    if found is None:
        have = ", ".join(f["path"] for f in projects.manifest(cid)) or "no files yet"
        return ToolResult(False, f"There is no file called {clean}; the project has: {have}.",
                          error="no such file")
    first = max(1, int(start_line or 1))
    return ToolResult(
        True, f"{clean}, version {found['version']} ({found['lines']} lines)", card="file",
        data={"path": clean, "version": found["version"], "lang": found["lang"],
              "lines": found["lines"], "content": found["content"], "start": first,
              "action": "read"},
    )


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    """The file's lines, numbered, from where it was asked to start, for as
    many as the budget has room for; and where it stopped, if it did."""
    data = result.data
    lines = (data.get("content") or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    first = min(max(1, data.get("start") or 1), max(1, len(lines)))
    head = f"{data.get('path')} (v{data.get('version')}, {len(lines)} lines):\n"
    room = budget * 3 - len(head.encode("utf-8")) - 80
    shown: list[str] = []
    used = 0
    for line in lines[first - 1:]:
        cost = len(line.encode("utf-8")) + 8
        if shown and used + cost > room:
            break
        shown.append(line)
        used += cost
    last = first + len(shown) - 1
    text = head + numbered(shown, first)
    if last < len(lines):
        text += (f"\n(lines {first}-{last} of {len(lines)}; call read_file again with "
                 f"start_line {last + 1} for the rest)")
    return text


register(Tool(
    name="read_file",
    label="Read file",
    glyph="▭",
    description="Read a file of this conversation's project as it stands now, with line numbers.",
    args=(
        Arg("path", "string", True, "the file's path in the project", max_len=160),
        Arg("start_line", "number", False, "the line to start from, for a long file"),
    ),
    run=_read_file,
    card="file",
    example="/file app.py",
    budget=1500,
    observe=_observe,
    project=True,
))
