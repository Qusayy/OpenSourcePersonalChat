"""write_file and edit_file: how the model changes the conversation's project.

``write_file`` writes one file whole: a new one, or one rewritten. ``edit_file``
replaces one exact piece of a file with another, and leaves the rest as it
was. Each change keeps a new version of that one file and of no other, so a
change asked for is that change and nothing more.

A file written again exactly as it stands is not kept: the model is told so,
in a sentence it can act on, since a fix that changes nothing is no fix. The
conversation is the call's own (``CALL``), never an argument the model gives.
"""

from __future__ import annotations

import difflib

from ..projects import paths
from ..store import projects
from .base import CALL, Arg, Tool, ToolResult, register
from .read_file import numbered

# Lines of a file shown around an edit, before and after it.
AROUND = 3


def _where(cid: str | None, path: str) -> tuple[str | None, ToolResult | None]:
    """The path as a project path, or why it cannot be one."""
    if not cid:
        return None, ToolResult(False, "There is no conversation to keep the file in.",
                                error="no conversation")
    clean = paths.safe(path)
    if clean is None:
        return None, ToolResult(
            False, f"{path!r} cannot be a file's path; give a relative path such as app/main.py.",
            error="bad path",
        )
    return clean, None


def _have(cid: str) -> str:
    return ", ".join(f["path"] for f in projects.manifest(cid)) or "no files yet"


async def _write_file(path: str, content: str) -> ToolResult:
    call = CALL.get()
    clean, refused = _where(call.conversation_id, path)
    if refused is not None:
        return refused
    cid = call.conversation_id
    lang = paths.language(clean)
    now = projects.file(cid, clean)
    data = {"path": clean, "lang": lang, "content": content, "action": "write"}
    if now is not None and now["content"] == content:
        return ToolResult(
            False,
            f"{clean} already has exactly this content, so nothing was saved; "
            "change the code that causes the problem.",
            card="file", data={**data, "version": now["version"], "lines": now["lines"]},
            error="unchanged",
        )
    kept = projects.add(cid, clean, content, lang=lang, run_id=call.run_id)
    if not kept["kept"]:
        return ToolResult(False, f"{clean} was not saved: {kept['why']}.", data=data, error="not kept")
    verb = "Created" if now is None else "Wrote"
    return ToolResult(
        True, f"{verb} {clean} (v{kept['version']}, {_count(kept['lines'])}).", card="file",
        data={**data, "version": kept["version"], "lines": kept["lines"], "created": now is None},
    )


def _plain(text: str) -> tuple[str, list[int]]:
    """Text without the spaces, tabs and carriage returns at the ends of its
    lines, which is how a piece copied loosely is matched; and for each
    character kept, where it was in ``text``."""
    kept: list[str] = []
    where: list[int] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] in " \t\r":
            j = i
            while j < n and text[j] in " \t\r":
                j += 1
            if j == n or text[j] == "\n":
                i = j  # the end of a line: not part of what is matched
                continue
            kept.extend(text[i:j])
            where.extend(range(i, j))
            i = j
            continue
        kept.append(text[i])
        where.append(i)
        i += 1
    return "".join(kept), where


def _loosely(text: str, piece: str) -> list[tuple[int, int]]:
    """Where ``piece`` is in ``text`` with the ends of lines forgiven, as
    spans of ``text`` itself, so that only what it matched is replaced."""
    plain, where = _plain(text)
    wanted, _ = _plain(piece)
    if not wanted.strip():
        return []
    spans: list[tuple[int, int]] = []
    at = plain.find(wanted)
    while at >= 0:
        start, end = where[at], where[at + len(wanted) - 1] + 1
        # What the piece matched up to the end of a line covers the spaces
        # left at the end of it too.
        stop = end
        while stop < len(text) and text[stop] in " \t":
            stop += 1
        if stop == len(text) or text[stop] in "\r\n":
            end = stop
        spans.append((start, end))
        at = plain.find(wanted, at + 1)
    return spans


def _count(lines: int) -> str:
    return f"{lines} line{'' if lines == 1 else 's'}"


def _nearest(text: str, piece: str) -> int | None:
    """The line of ``text`` most like the first line of ``piece``."""
    want = piece.strip().splitlines()[0].strip() if piece.strip() else ""
    lines = [line.strip() for line in text.splitlines()]
    best = difflib.get_close_matches(want, lines, n=1, cutoff=0.6)
    return lines.index(best[0]) + 1 if best else None


async def _edit_file(path: str, old_string: str, new_string: str) -> ToolResult:
    call = CALL.get()
    clean, refused = _where(call.conversation_id, path)
    if refused is not None:
        return refused
    cid = call.conversation_id
    now = projects.file(cid, clean)
    if now is None:
        return ToolResult(
            False, f"There is no file {clean} to edit; the project has: {_have(cid)}. "
            "Use write_file to create it.", error="no such file",
        )
    if old_string == new_string:
        return ToolResult(False, "old_string and new_string are the same, so nothing would change.",
                          error="unchanged")
    if not old_string.strip():
        return ToolResult(False, "old_string is empty; give the exact lines to replace.",
                          error="bad input")
    text = now["content"]
    found = text.count(old_string)
    if found == 1:
        at = text.index(old_string)
        span = (at, at + len(old_string))
    else:
        spans = [] if found else _loosely(text, old_string)
        found = found or len(spans)
        span = spans[0] if len(spans) == 1 else None
    if found == 0:
        near = _nearest(text, old_string)
        hint = f" (the nearest line is {near})" if near else ""
        return ToolResult(
            False, f"old_string was not found in {clean}{hint}; copy the exact lines from read_file.",
            error="not found",
        )
    if span is None:
        return ToolResult(
            False, f"old_string appears {found} times in {clean}; include more of the lines "
            "around it so that it appears once.", error="ambiguous",
        )
    at, end = span
    old = text[at:end]
    updated = text[:at] + new_string + text[end:]
    if updated == text:
        return ToolResult(False, f"{clean} would be left exactly as it is, so nothing was saved.",
                          error="unchanged")
    kept = projects.add(cid, clean, updated, lang=now["lang"], run_id=call.run_id)
    if not kept["kept"]:
        return ToolResult(False, f"{clean} was not saved: {kept['why']}.", error="not kept")
    first = text[:at].count("\n") + 1
    lines = updated.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the end of the last line, not a line of its own
    added = new_string.count("\n") + 1
    start = max(1, first - AROUND)
    end = min(len(lines), first + added - 1 + AROUND)
    return ToolResult(
        True, f"Edited {clean} (v{kept['version']}); lines {start}-{end} now read:", card="file",
        data={
            "path": clean, "lang": now["lang"], "version": kept["version"], "lines": kept["lines"],
            "content": updated, "action": "edit", "line": first,
            "removed": old.split("\n"), "added": new_string.split("\n"),
            "snippet": numbered(lines[start - 1:end], start),
        },
    )


def _observe_edit(result: ToolResult, purpose: str, budget: int) -> str:
    snippet = result.data.get("snippet") or ""
    return result.summary + ("\n" + snippet if snippet else "")


register(Tool(
    name="write_file",
    label="Write file",
    glyph="✎",
    description=(
        "Create a file in this conversation's project, or replace one whole. "
        "For a change inside an existing file, use edit_file."
    ),
    args=(
        Arg("path", "string", True, "the file's path in the project, such as app/main.py", max_len=160),
        Arg("content", "string", True, "the file's complete content"),
    ),
    run=_write_file,
    card="file",
    example="",
))

register(Tool(
    name="edit_file",
    label="Edit file",
    glyph="✎",
    description=(
        "Change part of a file in this conversation's project: replace old_string, copied "
        "exactly from the file and found once in it, with new_string."
    ),
    args=(
        Arg("path", "string", True, "the file's path in the project", max_len=160),
        Arg("old_string", "string", True, "the exact text to replace, with enough lines to be unique"),
        Arg("new_string", "string", True, "the text to put in its place"),
    ),
    run=_edit_file,
    card="file",
    example="",
    budget=400,
    observe=_observe_edit,
))
