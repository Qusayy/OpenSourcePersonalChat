"""A conversation's project: every version of every file the model wrote.

A file is kept when the tool that writes it (``write_file``, ``edit_file``)
is called. Writing the same path again keeps a new version beside the old
ones, and only of that path; the newest of each path is the project as it
stands, and is what the panel shows and the zip holds. A file written again
exactly as it stands is not a new version. A project is held to the limits
in ``app/projects/paths.py``: a file past them is not kept, and says so.

A file deleted (``delete_file``), or moved away from its path (``move_file``),
is a version of that path too: one that says the file is gone. Its earlier
versions are kept, and a file written there later goes on from its number.
Nothing that reads the project as it stands sees a deleted file.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Iterable

from ..projects import paths
from . import db

_NEWEST = (
    "SELECT f.path, f.version, f.lang, f.content, f.bytes, f.lines, f.created_at, f.deleted"
    "  FROM project_files f JOIN (SELECT path, MAX(version) AS version FROM project_files"
    "   WHERE conversation_id = ? GROUP BY path) newest"
    "    ON newest.path = f.path AND newest.version = f.version"
    " WHERE f.conversation_id = ? ORDER BY f.path"
)
_INSERT = (
    "INSERT INTO project_files (conversation_id, path, version, lang, content, bytes, lines,"
    " run_id, created_at, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _lines(content: str) -> int:
    return content.count("\n") + (0 if content.endswith("\n") or not content else 1)


def _newest(cid: str, conn: sqlite3.Connection | None = None) -> dict[str, dict]:
    """The newest version of every path, deleted or not."""
    if conn is None:
        found = db.rows(_NEWEST, (cid, cid))
    else:
        found = [dict(r) for r in conn.execute(_NEWEST, (cid, cid)).fetchall()]
    return {row["path"]: row for row in found}


def _live(newest: dict[str, dict]) -> dict[str, dict]:
    return {path: row for path, row in newest.items() if not row["deleted"]}


def add(cid: str, path: str, content: str, *, lang: str, run_id: str | None = None) -> dict:
    """Keep one version of a file. Returns what was kept (with its version),
    or ``{"kept": False, "why": ...}`` where a limit stopped it."""
    size = len(content.encode("utf-8"))
    if size > paths.MAX_FILE_BYTES:
        return {"kept": False, "why": f"{path} is larger than {paths.MAX_FILE_BYTES // 1024} KB"}
    newest = _newest(cid)
    now = _live(newest)
    if path in now and now[path]["content"] == content:
        return {"kept": False, "why": f"{path} already has exactly this content"}
    if path not in now and len(now) >= paths.MAX_FILES:
        return {"kept": False, "why": f"the project already has {paths.MAX_FILES} files"}
    total = sum(f["bytes"] for p, f in now.items() if p != path) + size
    if total > paths.MAX_PROJECT_BYTES:
        return {"kept": False, "why": f"the project would be larger than {paths.MAX_PROJECT_BYTES // (1024 * 1024)} MB"}
    version = (newest[path]["version"] + 1) if path in newest else 1
    lines = _lines(content)
    db.write(_INSERT, (cid, path, version, lang, content, size, lines, run_id, time.time(), 0))
    return {"kept": True, "path": path, "version": version, "bytes": size, "lines": lines}


def remove(cid: str, doomed: Iterable[str], *, run_id: str | None = None) -> dict:
    """Delete files, all together or none: each gets a version that says it
    is gone. Returns ``{"kept": True, "removed": [{path, version}]}``, or
    ``{"kept": False, "why": ...}`` naming a file that is not there."""
    doomed = list(dict.fromkeys(doomed))
    stamp = time.time()
    with db.transaction() as conn:
        newest = _newest(cid, conn)
        live = _live(newest)
        missing = [p for p in doomed if p not in live]
        if missing or not doomed:
            return {"kept": False, "why": f"there is no file {missing[0] if missing else ''}".strip()}
        removed = []
        try:
            for path in doomed:
                version = newest[path]["version"] + 1
                conn.execute(_INSERT, (cid, path, version, live[path]["lang"], "", 0, 0, run_id, stamp, 1))
                removed.append({"path": path, "version": version})
        except Exception:
            conn.rollback()
            raise
    return {"kept": True, "removed": removed}


def move(cid: str, pairs: Iterable[tuple[str, str]], *, run_id: str | None = None) -> dict:
    """Move files to new paths, all together or none. A file keeps its
    content; its old path gets a version that says it is gone, and its new
    one the next version there. No file is moved onto one that is there.
    Returns ``{"kept": True, "moved": [{from, to, version, lang}]}``, or
    ``{"kept": False, "why": ...}``."""
    pairs = list(pairs)
    stamp = time.time()
    with db.transaction() as conn:
        newest = _newest(cid, conn)
        live = _live(newest)
        sources = [src for src, _ in pairs]
        targets = [dst for _, dst in pairs]
        missing = [src for src in sources if src not in live]
        if missing or not pairs:
            return {"kept": False, "why": f"there is no file {missing[0] if missing else ''}".strip()}
        if len(set(targets)) != len(targets):
            return {"kept": False, "why": "two files would be moved to the same path"}
        # A target may be a file that is itself moving away in the same move.
        taken = [dst for dst in targets if dst in live and dst not in sources]
        if taken:
            return {"kept": False, "why": "there is a file there already: " + ", ".join(taken)}
        moved = []
        try:
            for src in sources:
                version = newest[src]["version"] + 1
                conn.execute(_INSERT, (cid, src, version, live[src]["lang"], "", 0, 0, run_id, stamp, 1))
                newest[src] = {**newest[src], "version": version}
            for src, dst in pairs:
                row = live[src]
                version = (newest[dst]["version"] + 1) if dst in newest else 1
                lang = paths.language(dst)
                conn.execute(_INSERT, (cid, dst, version, lang, row["content"], row["bytes"],
                                       row["lines"], run_id, stamp, 0))
                newest[dst] = {**row, "version": version}
                moved.append({"from": src, "to": dst, "version": version, "lang": lang})
        except Exception:
            conn.rollback()
            raise
    return {"kept": True, "moved": moved}


def latest(cid: str) -> dict[str, dict]:
    """The project as it stands: the newest version of each path that is
    there, by path."""
    return {
        path: {k: v for k, v in row.items() if k != "deleted"}
        for path, row in _live(_newest(cid)).items()
    }


def under(cid: str, folder: str) -> dict[str, dict]:
    """The files of the project below a folder, by path."""
    prefix = folder.rstrip("/") + "/"
    return {path: row for path, row in latest(cid).items() if path.startswith(prefix)}


def file(cid: str, path: str, version: int | None = None) -> dict | None:
    """One file: its newest version, or the version asked for. None for a
    file that is not there, or a version that is its deletion."""
    if version is None:
        return latest(cid).get(path)
    rows = db.rows(
        "SELECT path, version, lang, content, bytes, lines, created_at FROM project_files"
        " WHERE conversation_id = ? AND path = ? AND version = ? AND deleted = 0",
        (cid, path, version),
    )
    return rows[0] if rows else None


def manifest(cid: str) -> list[dict]:
    """The project's files without their contents: what the model is told is
    there, and what a page lists."""
    return [
        {k: f[k] for k in ("path", "version", "lang", "bytes", "lines", "created_at")}
        for f in latest(cid).values()
    ]


def forget(cid: str) -> None:
    """Forget a conversation's project, every version of it."""
    db.write("DELETE FROM project_files WHERE conversation_id = ?", (cid,))
