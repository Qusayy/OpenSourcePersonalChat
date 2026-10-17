"""Notes: what the Agent is asked to keep, and found again by their words.

Searched with SQLite's full-text index where the build has one, and with LIKE
where it has not.
"""

from __future__ import annotations

import re
import time

from . import db


def add_note(text: str, *, scope: str, conversation_id: str | None) -> int:
    """Keep a note. ``conversation_id`` is the conversation it belongs to,
    under the conversation scope; None under the site's."""
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO notes (scope, conversation_id, text, created_at) VALUES (?, ?, ?, ?)",
            (scope, conversation_id, text, time.time()),
        )
        if db.has_fts():
            conn.execute("INSERT INTO notes_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
        return int(cur.lastrowid)


def _terms(query: str) -> list[str]:
    return re.findall(r"\w+", query or "")[:12]


def search_notes(
    query: str, *, scope: str, conversation_id: str | None, limit: int = 5
) -> list[dict]:
    """The notes of a scope that match ``query``, best first; the latest when
    it has nothing to match. Under the conversation scope only that
    conversation's notes are ever read."""
    if scope == "conversation":
        where, params = "n.scope = 'conversation' AND n.conversation_id = ?", [conversation_id]
    else:
        where, params = "n.scope = 'site'", []
    terms = _terms(query)
    if not terms:
        return db.rows(
            f"SELECT n.id, n.text, n.created_at FROM notes n WHERE {where}"
            " ORDER BY n.created_at DESC LIMIT ?", (*params, limit),
        )
    if db.has_fts():
        # Each word quoted, so that nothing in a query is taken for syntax.
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        return db.rows(
            f"SELECT n.id, n.text, n.created_at FROM notes_fts f JOIN notes n ON n.id = f.rowid"
            f" WHERE notes_fts MATCH ? AND {where} ORDER BY f.rank LIMIT ?",
            (match, *params, limit),
        )
    likes = " OR ".join("n.text LIKE ? ESCAPE '\\'" for _ in terms)
    escaped = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
               for t in terms]
    return db.rows(
        f"SELECT n.id, n.text, n.created_at FROM notes n WHERE ({likes}) AND {where}"
        " ORDER BY n.created_at DESC LIMIT ?", (*escaped, *params, limit),
    )


def forget_conversation(cid: str) -> None:
    """Forget every note a conversation kept."""
    for row in db.rows("SELECT id FROM notes WHERE conversation_id = ?", (cid,)):
        if db.has_fts():
            db.write("DELETE FROM notes_fts WHERE rowid = ?", (row["id"],))
    db.write("DELETE FROM notes WHERE conversation_id = ?", (cid,))
