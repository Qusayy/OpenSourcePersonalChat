"""SQLite persistence. One connection, WAL, a lock around writes.

This module holds the connection and the schema. What is kept lives beside
it, one module to a kind of thing: ``conversations``, ``runs``, ``notes``.
Nothing outside this package writes SQL but the gate, whose own tables
nothing else reads (``app/gate.py``).

Traffic here is a handful of rows per generation — on a box that manages ~10
tokens a second, a connection pool would be ceremony without benefit.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import settings

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

-- The files of a conversation's project, every version an answer wrote. The
-- newest version of each path is the project as it stands.
CREATE TABLE IF NOT EXISTS project_files (
    conversation_id  TEXT NOT NULL,
    path             TEXT NOT NULL,
    version          INTEGER NOT NULL,
    lang             TEXT NOT NULL,
    content          TEXT NOT NULL,
    bytes            INTEGER NOT NULL,
    lines            INTEGER NOT NULL,
    run_id           TEXT,
    created_at       REAL NOT NULL,
    PRIMARY KEY (conversation_id, path, version)
);
"""


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
    # Imported here: the steps are kept with the connection this opens.
    from . import runs

    runs.prune_steps()


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


def connection() -> sqlite3.Connection:
    if _conn is None:
        init()
    assert _conn is not None
    return _conn


# The connection is opened before the lock is taken: opening it writes (it
# clears out old steps), and the lock is not one a thread can take twice.


def write(sql: str, params: tuple = ()) -> None:
    conn = connection()
    with _lock:
        conn.execute(sql, params)
        conn.commit()


def rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = connection()
    with _lock:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """The connection, for statements that are committed together."""
    conn = connection()
    with _lock:
        yield conn
        conn.commit()


def has_fts() -> bool:
    """Whether this database's SQLite has full-text search."""
    connection()  # opened, so `_fts` says what this database has
    return _fts
