"""SQLite persistence. One connection, WAL, a lock around writes.

Traffic here is a handful of rows per generation — on a box that manages ~10
tokens a second, a connection pool would be ceremony without benefit.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    persona     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                 TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role               TEXT NOT NULL,
    content            TEXT NOT NULL,
    created_at         REAL NOT NULL,
    ttft_ms            REAL,
    gen_tps            REAL,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    label          TEXT,
    prompt_tokens  INTEGER,
    gen_tokens     INTEGER,
    ttft_ms        REAL,
    prefill_tps    REAL,
    gen_tps        REAL,
    rss_mb         REAL,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_kind ON runs(kind, created_at);
"""


def init() -> None:
    global _conn
    path = settings.resolved_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _conn.commit()


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _db() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


def _write(sql: str, params: tuple = ()) -> None:
    with _lock:
        _db().execute(sql, params)
        _db().commit()


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in _db().execute(sql, params).fetchall()]


# ------------------------------------------------------------ conversations --


def make_title(text: str, words: int = 6) -> str:
    """First few words of the opening message. Deliberately not a model call —
    on this hardware a title would cost as much as a short answer."""
    clean = " ".join(text.strip().split())
    if not clean:
        return "New conversation"
    parts = clean.split(" ")[:words]
    title = " ".join(parts)
    return (title + "…") if len(parts) < len(clean.split(" ")) else title


def create_conversation(persona: str, first_message: str) -> str:
    cid = uuid.uuid4().hex
    now = time.time()
    _write(
        "INSERT INTO conversations (id, title, persona, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (cid, make_title(first_message), persona, now, now),
    )
    return cid


def touch_conversation(cid: str, persona: str | None = None) -> None:
    if persona:
        _write(
            "UPDATE conversations SET updated_at = ?, persona = ? WHERE id = ?",
            (time.time(), persona, cid),
        )
    else:
        _write("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), cid))


def list_conversations(limit: int = 60) -> list[dict]:
    return _rows(
        "SELECT c.id, c.title, c.persona, c.created_at, c.updated_at,"
        "       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n"
        "  FROM conversations c ORDER BY c.updated_at DESC LIMIT ?",
        (limit,),
    )


def get_conversation(cid: str) -> dict | None:
    rows = _rows("SELECT * FROM conversations WHERE id = ?", (cid,))
    if not rows:
        return None
    convo = rows[0]
    convo["messages"] = _rows(
        "SELECT id, role, content, created_at, ttft_ms, gen_tps, prompt_tokens,"
        "       completion_tokens"
        "  FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (cid,),
    )
    return convo


def delete_conversation(cid: str) -> None:
    _write("DELETE FROM messages WHERE conversation_id = ?", (cid,))
    _write("DELETE FROM conversations WHERE id = ?", (cid,))


def rename_conversation(cid: str, title: str) -> None:
    _write("UPDATE conversations SET title = ? WHERE id = ?", (title.strip()[:120], cid))


# ----------------------------------------------------------------- messages --


def add_message(
    cid: str,
    role: str,
    content: str,
    *,
    ttft_ms: float | None = None,
    gen_tps: float | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> str:
    mid = uuid.uuid4().hex
    _write(
        "INSERT INTO messages (id, conversation_id, role, content, created_at,"
        " ttft_ms, gen_tps, prompt_tokens, completion_tokens)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, cid, role, content, time.time(), ttft_ms, gen_tps, prompt_tokens, completion_tokens),
    )
    return mid


def conversation_messages(cid: str) -> list[dict]:
    return _rows(
        "SELECT role, content FROM messages WHERE conversation_id = ?"
        " ORDER BY created_at ASC",
        (cid,),
    )


# --------------------------------------------------------------------- runs --


def add_run(
    kind: str,
    *,
    label: str | None = None,
    prompt_tokens: int | None = None,
    gen_tokens: int | None = None,
    ttft_ms: float | None = None,
    prefill_tps: float | None = None,
    gen_tps: float | None = None,
    rss_mb: float | None = None,
) -> None:
    _write(
        "INSERT INTO runs (id, kind, label, prompt_tokens, gen_tokens, ttft_ms,"
        " prefill_tps, gen_tps, rss_mb, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex,
            kind,
            label,
            prompt_tokens,
            gen_tokens,
            ttft_ms,
            prefill_tps,
            gen_tps,
            rss_mb,
            time.time(),
        ),
    )


def recent_runs(kind: str | None = None, limit: int = 200) -> list[dict]:
    if kind:
        return _rows(
            "SELECT * FROM runs WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        )
    return _rows("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))


def chat_stats() -> dict:
    rows = _rows(
        "SELECT COUNT(*) AS n, AVG(gen_tps) AS avg_tps, AVG(ttft_ms) AS avg_ttft,"
        "       SUM(completion_tokens) AS tokens"
        "  FROM messages WHERE role = 'assistant' AND gen_tps IS NOT NULL"
    )
    return rows[0] if rows else {}
