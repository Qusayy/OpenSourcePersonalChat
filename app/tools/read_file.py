"""read_file: one file of this conversation's project, as it stands now.

The model reads a file back with this before it changes it, and when the user
reports an error in it: the lines come numbered, so a traceback's line number
leads straight to the line. A file longer than the model can be given at once
is read in parts, from ``start_line``, or only where it matters: with
``find``, the lines that contain some text, each with a few lines around it.
The conversation is the call's own (``CALL``), never an argument the model
gives.
"""

from __future__ import annotations

from ..projects import paths
from ..store import projects
from ..text import terms
from .base import CALL, Arg, Tool, ToolResult, register

# Lines shown before and after each line found, and the most lines found
# that are shown at all.
AROUND = 3
MATCHES = 12


def numbered(lines: list[str], first: int) -> str:
    """Lines of a file as the model reads them, each with its number."""
    width = len(str(first + len(lines) - 1))
    return "\n".join(f"{n:>{width}}│ {line}" for n, line in enumerate(lines, start=first))


def matching(lines: list[str], find: str) -> list[int]:
    """The numbers of the lines that contain ``find``, whatever its case; or,
    where none does, of those that contain every word of it."""
    wanted = find.strip().casefold()
    if not wanted:
        return []
    exact = [n for n, line in enumerate(lines, start=1) if wanted in line.casefold()]
    if exact:
        return exact
    words = set(terms(find))
    if not words:
        return []
    return [n for n, line in enumerate(lines, start=1) if words <= set(terms(line))]


async def _read_file(path: str, start_line: float | None = None, find: str = "") -> ToolResult:
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
              "find": " ".join((find or "").split()), "action": "read"},
    )


def _lines(data: dict) -> list[str]:
    lines = (data.get("content") or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    """The file's lines, numbered, from where it was asked to start, for as
    many as the budget has room for; and where it stopped, if it did. With
    ``find``, only the lines that contain it, and those around them."""
    data = result.data
    lines = _lines(data)
    if data.get("find"):
        return _found(data, lines, budget)
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


def _found(data: dict, lines: list[str], budget: int) -> str:
    """The lines that contain what was asked for, each with ``AROUND`` lines
    either side, numbered, for as many as the budget has room for."""
    find = data["find"]
    path = data.get("path")
    hits = matching(lines, find)
    if not hits:
        return (f"No line of {path} ({len(lines)} lines) contains {find!r}. "
                f"Try fewer or other words, or read it from a line with start_line.")
    head = (f"{path} (v{data.get('version')}, {len(lines)} lines): "
            f"{len(hits)} line{'' if len(hits) == 1 else 's'} with {find!r}:")
    room = budget * 3 - len(head.encode("utf-8")) - 120
    blocks: list[str] = []
    used = 0
    last = 0
    for hit in hits[:MATCHES]:
        start = max(hit - AROUND, last + 1, 1)
        end = min(hit + AROUND, len(lines))
        if start > end:
            continue
        block = numbered(lines[start - 1:end], start)
        cost = len(block.encode("utf-8")) + 4
        if blocks and used + cost > room:
            break
        # A block that follows straight on from the last is joined to it; a
        # gap between them is marked.
        gap = "\n…\n" if blocks and start > last + 1 else "\n"
        blocks.append(gap + block)
        used += cost
        last = end
    told = sum(1 for hit in hits if hit <= last)
    text = head + "".join(blocks)
    if told < len(hits):
        text += (f"\n({told} of {len(hits)} shown; call read_file with start_line "
                 f"{hits[told]} for the next)")
    return text


register(Tool(
    name="read_file",
    label="Read file",
    glyph="▭",
    description=(
        "Read a file of this conversation's project as it stands now, with line numbers. "
        "For a long file, give find to see only the lines that contain some text."
    ),
    args=(
        Arg("path", "string", True, "the file's path in the project", max_len=160),
        Arg("start_line", "number", False, "the line to start from, for a long file"),
        Arg("find", "string", False,
            "text to find: only the lines with it are shown, with a few around each",
            max_len=120),
    ),
    run=_read_file,
    card="file",
    example="/file app.py",
    budget=1500,
    observe=_observe,
    project=True,
))
