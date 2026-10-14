"""SQLite persistence. One connection, WAL, a lock around writes.

Traffic here is a handful of rows per generation — on a box that manages ~10
tokens a second, a connection pool would be ceremony without benefit.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
# A file used in place of the configured one, while `elsewhere` holds it.
_override: Path | None = None
# Whether this SQLite has full-text search. Notes are searched with LIKE
# where it has not.
_fts = False

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
    kind             TEXT NOT NULL,     -- route | decide | repair | tool | answer
    name             TEXT,              -- the tool or skill
    via              TEXT,              -- slash | heuristic | skill | model
    input_json       TEXT,
    output_text      TEXT,
    prompt_tokens    INTEGER,
    -- Prompt tokens the model actually had to read, the rest being already
    -- in its cache. Only a model server reports it, and it is never estimated.
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

-- What the model was sent for an answered message, from the message to the
-- answer, as parts that say what each one is. The next message is read with
-- it as it was. It is part of the conversation: deleted with it, and kept as
-- long as it is.
CREATE TABLE IF NOT EXISTS context_turns (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    question_id      TEXT NOT NULL,
    answer_id        TEXT NOT NULL,
    -- The system text it was sent under, by its key.
    system_key       TEXT NOT NULL,
    parts_json       TEXT NOT NULL,
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_conv ON context_turns(conversation_id, created_at);

-- Notes the Agent was asked to keep. A note made under the "conversation"
-- scope belongs to its conversation and goes with it; one made under "site"
-- belongs to the deployment. Not pruned.
CREATE TABLE IF NOT EXISTS notes (
    id               INTEGER PRIMARY KEY,
    scope            TEXT NOT NULL,
    conversation_id  TEXT,
    text             TEXT NOT NULL,
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_conv ON notes(conversation_id, created_at);
"""

# How long a step is kept when the settings do not say. A trace is a copy of
# what visitors typed and were told, on a site with no accounts.
STEPS_KEEP_DAYS = 30.0
# Old steps are cleared out when the server starts, and no more often than
# this while it runs.
PRUNE_EVERY = 3600.0
_pruned_at = 0.0


def init() -> None:
    global _conn, _fts
    path = _override or settings.resolved_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    # Columns added after a database was first made.
    _add_column("messages", "correction", "TEXT")
    # Made apart from the schema: a build of SQLite without the module raises
    # here, and the notes are then searched without it.
    try:
        _conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text)")
        _fts = True
    except sqlite3.OperationalError:
        _fts = False
    _conn.commit()
    prune_steps()


def _add_column(table: str, column: str, declaration: str) -> None:
    """Add a column to a table made before it existed. Does nothing where it
    is there already."""
    have = {row[1] for row in _conn.execute(f"PRAGMA table_info({table})")}
    if column not in have:
        _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


@contextmanager
def elsewhere(path: Path | str) -> Iterator[None]:
    """Use the database at ``path`` for what runs inside, and the one before
    afterwards. An evaluation keeps what its tasks write out of the site's
    own database this way."""
    global _conn, _override
    saved = (_conn, _override)
    _conn, _override = None, Path(path)
    try:
        yield
    finally:
        close()
        _conn, _override = saved


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
        "       completion_tokens, correction"
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
    for row in _rows("SELECT id FROM notes WHERE conversation_id = ?", (cid,)):
        if _fts:
            _write("DELETE FROM notes_fts WHERE rowid = ?", (row["id"],))
    _write("DELETE FROM notes WHERE conversation_id = ?", (cid,))
    _write("DELETE FROM context_turns WHERE conversation_id = ?", (cid,))
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
    correction: str | None = None,
) -> str:
    mid = uuid.uuid4().hex
    _write(
        "INSERT INTO messages (id, conversation_id, role, content, created_at,"
        " ttft_ms, gen_tps, prompt_tokens, completion_tokens, correction)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, cid, role, content, time.time(), ttft_ms, gen_tps, prompt_tokens,
         completion_tokens, correction),
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


# -------------------------------------------------------------------- notes --


def add_note(text: str, *, scope: str, conversation_id: str | None) -> int:
    """Keep a note. ``conversation_id`` is the conversation it belongs to,
    under the conversation scope; None under the site's."""
    # Opened before the lock is taken: opening it writes, and takes the lock.
    db = _db()
    with _lock:
        cur = db.execute(
            "INSERT INTO notes (scope, conversation_id, text, created_at) VALUES (?, ?, ?, ?)",
            (scope, conversation_id, text, time.time()),
        )
        if _fts:
            db.execute("INSERT INTO notes_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
        db.commit()
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
        return _rows(
            f"SELECT n.id, n.text, n.created_at FROM notes n WHERE {where}"
            " ORDER BY n.created_at DESC LIMIT ?", (*params, limit),
        )
    _db()  # opened, so `_fts` says what this database has
    if _fts:
        # Each word quoted, so that nothing in a query is taken for syntax.
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        return _rows(
            f"SELECT n.id, n.text, n.created_at FROM notes_fts f JOIN notes n ON n.id = f.rowid"
            f" WHERE notes_fts MATCH ? AND {where} ORDER BY f.rank LIMIT ?",
            (match, *params, limit),
        )
    likes = " OR ".join("n.text LIKE ? ESCAPE '\\'" for _ in terms)
    escaped = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
               for t in terms]
    return _rows(
        f"SELECT n.id, n.text, n.created_at FROM notes n WHERE ({likes}) AND {where}"
        " ORDER BY n.created_at DESC LIMIT ?", (*escaped, *params, limit),
    )


# ------------------------------------------------------------ what was sent --


def add_context_turn(
    cid: str,
    run_id: str,
    *,
    question_id: str,
    answer_id: str,
    system_key: str,
    parts: list[dict],
) -> str:
    """Keep what the model was sent for one answered message."""
    tid = uuid.uuid4().hex
    _write(
        "INSERT INTO context_turns (id, conversation_id, run_id, question_id, answer_id,"
        " system_key, parts_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, cid, run_id, question_id, answer_id, system_key,
         json.dumps(parts, ensure_ascii=False), time.time()),
    )
    return tid


def conversation_record(cid: str) -> tuple[list[dict], list[dict]]:
    """A conversation as the next message is read with it: its messages,
    oldest first, and what was kept of what each answer was sent."""
    messages = _rows(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ?"
        " ORDER BY created_at ASC",
        (cid,),
    )
    records = _rows(
        "SELECT question_id, answer_id, system_key, parts_json, created_at FROM context_turns"
        " WHERE conversation_id = ? ORDER BY created_at ASC",
        (cid,),
    )
    for record in records:
        record["parts"] = json.loads(record.pop("parts_json"))
    return messages, records


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
