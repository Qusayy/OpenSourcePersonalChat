"""A conversation's project: every version of every file the model wrote.

A file is kept when the tool that writes it (``write_file``, ``edit_file``)
is called. Writing the same path again keeps a new version beside the old
ones, and only of that path; the newest of each path is the project as it
stands, and is what the panel shows and the zip holds. A file written again
exactly as it stands is not a new version. A project is held to the limits
in ``app/projects/paths.py``: a file past them is not kept, and says so.
"""

from __future__ import annotations

import time

from ..projects import paths
from . import db


def add(cid: str, path: str, content: str, *, lang: str, run_id: str | None = None) -> dict:
    """Keep one version of a file. Returns what was kept (with its version),
    or ``{"kept": False, "why": ...}`` where a limit stopped it."""
    size = len(content.encode("utf-8"))
    if size > paths.MAX_FILE_BYTES:
        return {"kept": False, "why": f"{path} is larger than {paths.MAX_FILE_BYTES // 1024} KB"}
    now = latest(cid)
    if path in now and now[path]["content"] == content:
        return {"kept": False, "why": f"{path} already has exactly this content"}
    if path not in now and len(now) >= paths.MAX_FILES:
        return {"kept": False, "why": f"the project already has {paths.MAX_FILES} files"}
    total = sum(f["bytes"] for p, f in now.items() if p != path) + size
    if total > paths.MAX_PROJECT_BYTES:
        return {"kept": False, "why": f"the project would be larger than {paths.MAX_PROJECT_BYTES // (1024 * 1024)} MB"}
    version = (now[path]["version"] + 1) if path in now else 1
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    db.write(
        "INSERT INTO project_files (conversation_id, path, version, lang, content, bytes, lines,"
        " run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, path, version, lang, content, size, lines, run_id, time.time()),
    )
    return {"kept": True, "path": path, "version": version, "bytes": size, "lines": lines}


def latest(cid: str) -> dict[str, dict]:
    """The project as it stands: the newest version of each path, by path."""
    rows = db.rows(
        "SELECT f.path, f.version, f.lang, f.content, f.bytes, f.lines, f.created_at"
        "  FROM project_files f JOIN (SELECT path, MAX(version) AS version FROM project_files"
        "   WHERE conversation_id = ? GROUP BY path) newest"
        "    ON newest.path = f.path AND newest.version = f.version"
        " WHERE f.conversation_id = ? ORDER BY f.path",
        (cid, cid),
    )
    return {row["path"]: row for row in rows}


def file(cid: str, path: str, version: int | None = None) -> dict | None:
    """One file: its newest version, or the version asked for."""
    if version is None:
        return latest(cid).get(path)
    rows = db.rows(
        "SELECT path, version, lang, content, bytes, lines, created_at FROM project_files"
        " WHERE conversation_id = ? AND path = ? AND version = ?",
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
