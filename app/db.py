"""SQLite persistence. One connection, WAL, a lock around writes.

Traffic here is a handful of rows per generation — on a box that manages ~10
tokens a second, a connection pool would be ceremony without benefit.
"""

from __future__ import annotations

import json
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

-- Tool calls carry the conversation id as well as the message id, so a call
-- whose answer was cancelled still replays on the page instead of vanishing.
CREATE TABLE IF NOT EXISTS tool_calls (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    message_id       TEXT,
    tool             TEXT NOT NULL,
    via              TEXT,
    args_json        TEXT,
    card             TEXT,
    data_json        TEXT,
    summary          TEXT,
    ms               REAL,
    ok               INTEGER NOT NULL DEFAULT 1,
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_msg ON tool_calls(message_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_conv ON tool_calls(conversation_id, created_at);

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

-- What one message did on its way to an answer, a row per thing done, in
-- order, with what each cost. `run_id` is the same for every row of one
-- message. A run that was stopped or failed has rows and no message.
CREATE TABLE IF NOT EXISTS steps (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    conversation_id  TEXT NOT NULL,
    message_id       TEXT,
    idx              INTEGER NOT NULL,
    kind             TEXT NOT NULL,     -- route | repair | tool | answer
    name             TEXT,              -- the tool or skill
    via              TEXT,              -- slash | heuristic | skill | model
    input_json       TEXT,
    output_text      TEXT,
    prompt_tokens    INTEGER,
    -- Prompt tokens the model actually had to read, the rest being already
    -- in its cache. Nothing measures this yet, and it is never estimated.
    new_tokens       INTEGER,
    output_tokens    INTEGER,
    queue_ms         REAL,
    first_token_ms   REAL,
    total_ms         REAL,
    ok               INTEGER NOT NULL DEFAULT 1,
    error            TEXT,
    -- Written by a server with no model: its timings measure a sleep.
    mock             INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, idx);
CREATE INDEX IF NOT EXISTS idx_steps_conv ON steps(conversation_id, created_at);
"""

# How long a step is kept when the settings do not say. A trace is a copy of
# what visitors typed and were told, on a site with no accounts.
STEPS_KEEP_DAYS = 30.0
# Old steps are cleared out when the server starts, and no more often than
# this while it runs.
PRUNE_EVERY = 3600.0
_pruned_at = 0.0


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
    prune_steps()


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
    # Hang each tool call off the message it produced, so reloading a
    # conversation replays its cards instead of losing them.
    calls = conversation_tool_calls(cid)
    by_message: dict[str, list[dict]] = {}
    orphans = []
    for call in calls:
        if call["message_id"]:
            by_message.setdefault(call["message_id"], []).append(call)
        else:
            orphans.append(call)
    for message in convo["messages"]:
        message["tool_calls"] = by_message.get(message["id"], [])
    convo["orphan_tool_calls"] = orphans
    return convo


def delete_conversation(cid: str) -> None:
    _write("DELETE FROM steps WHERE conversation_id = ?", (cid,))
    _write("DELETE FROM tool_calls WHERE conversation_id = ?", (cid,))
    _write("DELETE FROM messages WHERE conversation_id = ?", (cid,))
    _write("DELETE FROM conversations WHERE id = ?", (cid,))


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


def add_tool_call(
    conversation_id: str,
    tool: str,
    *,
    message_id: str | None = None,
    via: str = "heuristic",
    args: dict | None = None,
    card: str | None = None,
    data: dict | None = None,
    summary: str = "",
    ms: float = 0.0,
    ok: bool = True,
) -> str:
    tid = uuid.uuid4().hex
    _write(
        "INSERT INTO tool_calls (id, conversation_id, message_id, tool, via, args_json,"
        " card, data_json, summary, ms, ok, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tid, conversation_id, message_id, tool, via,
            json.dumps(args or {}), card, json.dumps(data or {}),
            summary, ms, 1 if ok else 0, time.time(),
        ),
    )
    return tid


def attach_tool_calls(message_id: str, tool_call_ids: list[str]) -> None:
    for tid in tool_call_ids:
        _write("UPDATE tool_calls SET message_id = ? WHERE id = ?", (message_id, tid))


def conversation_tool_calls(cid: str) -> list[dict]:
    rows = _rows(
        "SELECT * FROM tool_calls WHERE conversation_id = ? ORDER BY created_at ASC", (cid,)
    )
    for r in rows:
        r["args"] = json.loads(r.pop("args_json") or "{}")
        r["data"] = json.loads(r.pop("data_json") or "{}")
        r["ok"] = bool(r["ok"])
    return rows


def conversation_messages(cid: str) -> list[dict]:
    return _rows(
        "SELECT role, content FROM messages WHERE conversation_id = ?"
        " ORDER BY created_at ASC",
        (cid,),
    )


# -------------------------------------------------------------------- steps --


def _keep_days() -> float:
    # Read this way because a caller may hand this module settings that know
    # nothing about steps: the tests do.
    return float(getattr(settings, "steps_keep_days", STEPS_KEEP_DAYS))


def add_step(
    run_id: str,
    conversation_id: str,
    idx: int,
    kind: str,
    *,
    name: str | None = None,
    via: str | None = None,
    input: dict | None = None,
    output: str | None = None,
    prompt_tokens: int | None = None,
    new_tokens: int | None = None,
    output_tokens: int | None = None,
    queue_ms: float | None = None,
    first_token_ms: float | None = None,
    total_ms: float | None = None,
    ok: bool = True,
    error: str | None = None,
    created_at: float | None = None,
) -> str | None:
    """Store one step of a run. Returns its id, or None when steps are not
    being kept at all (a retention of zero days)."""
    if _keep_days() <= 0:
        return None
    if time.time() - _pruned_at > PRUNE_EVERY:
        prune_steps()
    sid = uuid.uuid4().hex
    _write(
        "INSERT INTO steps (id, run_id, conversation_id, idx, kind, name, via, input_json,"
        " output_text, prompt_tokens, new_tokens, output_tokens, queue_ms, first_token_ms,"
        " total_ms, ok, error, mock, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid, run_id, conversation_id, idx, kind, name, via,
            None if input is None else json.dumps(input, ensure_ascii=False),
            output, prompt_tokens, new_tokens, output_tokens, queue_ms, first_token_ms,
            total_ms, 1 if ok else 0, error, 1 if getattr(settings, "mock", False) else 0,
            time.time() if created_at is None else created_at,
        ),
    )
    return sid


def attach_steps(message_id: str, run_id: str) -> None:
    """Tie every step of a run to the answer the run produced."""
    _write("UPDATE steps SET message_id = ? WHERE run_id = ?", (message_id, run_id))


def run_steps(run_id: str) -> list[dict]:
    """The steps of one run, in the order they happened."""
    rows = _rows("SELECT * FROM steps WHERE run_id = ? ORDER BY idx ASC", (run_id,))
    for r in rows:
        raw = r.pop("input_json")
        r["input"] = None if raw is None else json.loads(raw)
        r["ok"] = bool(r["ok"])
        r["mock"] = bool(r["mock"])
    return rows


def last_message_asked(cid: str) -> bool:
    """Whether the last thing said in a conversation was a question the model
    chose to put to the user in place of an answer.

    It is read from the steps kept for that message: the pass that decided
    said "ask". Where steps are not kept, the answer is always no, and a reply
    to such a question is routed like any other message.
    """
    rows = _rows(
        "SELECT s.output_text FROM steps s"
        " WHERE s.message_id = (SELECT id FROM messages WHERE conversation_id = ?"
        "                        ORDER BY created_at DESC LIMIT 1)"
        "   AND s.kind IN ('route', 'repair')"
        " ORDER BY s.idx DESC LIMIT 1",
        (cid,),
    )
    if not rows or not rows[0]["output_text"]:
        return False
    try:
        said = json.loads(rows[0]["output_text"])
    except json.JSONDecodeError:
        return False
    return isinstance(said, dict) and said.get("tool") == "ask"


def prune_steps(keep_days: float | None = None) -> None:
    """Forget steps older than the retention period."""
    global _pruned_at
    _pruned_at = time.time()
    days = _keep_days() if keep_days is None else keep_days
    _write("DELETE FROM steps WHERE created_at < ?", (time.time() - days * 86400,))


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
    # Mock mode times a fake token stream paced by a sleep. Those figures
    # measure nothing, so none of them is kept: stored, they would be drawn on
    # the Benchmark page as this server's history. Rows already in a database
    # are left as they are.
    if settings.mock:
        return
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
