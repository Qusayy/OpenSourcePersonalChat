"""What each run did and cost: its steps, kept for a while, and the figures of
every answer and benchmark pass.

The settings are the store's own (``db.settings``), so a test that points the
store somewhere else points this with it.
"""

from __future__ import annotations

import json
import time
import uuid

from . import db

# How long a step is kept when the settings do not say. A trace is a copy of
# what visitors typed and were told, on a site with no accounts.
STEPS_KEEP_DAYS = 30.0
# Old steps are cleared out when the server starts, and no more often than
# this while it runs.
PRUNE_EVERY = 3600.0
_pruned_at = 0.0


# -------------------------------------------------------------------- steps --


def _keep_days() -> float:
    # Read this way because a caller may hand this module settings that know
    # nothing about steps: the tests do.
    return float(getattr(db.settings, "steps_keep_days", STEPS_KEEP_DAYS))


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
    db.write(
        "INSERT INTO steps (id, run_id, conversation_id, idx, kind, name, via, input_json,"
        " output_text, prompt_tokens, new_tokens, output_tokens, queue_ms, first_token_ms,"
        " total_ms, ok, error, mock, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid, run_id, conversation_id, idx, kind, name, via,
            None if input is None else json.dumps(input, ensure_ascii=False),
            output, prompt_tokens, new_tokens, output_tokens, queue_ms, first_token_ms,
            total_ms, 1 if ok else 0, error, 1 if getattr(db.settings, "mock", False) else 0,
            time.time() if created_at is None else created_at,
        ),
    )
    return sid


def attach_steps(message_id: str, run_id: str) -> None:
    """Tie every step of a run to the answer the run produced."""
    db.write("UPDATE steps SET message_id = ? WHERE run_id = ?", (message_id, run_id))


def run_steps(run_id: str) -> list[dict]:
    """The steps of one run, in the order they happened."""
    rows = db.rows("SELECT * FROM steps WHERE run_id = ? ORDER BY idx ASC", (run_id,))
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
    # Whole runs, by their last step, so a run is never read back in part.
    db.write(
        "DELETE FROM steps WHERE run_id IN (SELECT run_id FROM steps GROUP BY run_id"
        " HAVING MAX(created_at) < ?)",
        (time.time() - days * 86400,),
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
    # Mock mode times a fake token stream paced by a sleep. Those figures
    # measure nothing, so none of them is kept: stored, they would be drawn on
    # the Benchmark page as this server's history. Rows already in a database
    # are left as they are.
    if db.settings.mock:
        return
    db.write(
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
        return db.rows(
            "SELECT * FROM runs WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        )
    return db.rows("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))


def chat_stats() -> dict:
    rows = db.rows(
        "SELECT COUNT(*) AS n, AVG(gen_tps) AS avg_tps, AVG(ttft_ms) AS avg_ttft,"
        "       SUM(completion_tokens) AS tokens"
        "  FROM messages WHERE role = 'assistant' AND gen_tps IS NOT NULL"
    )
    return rows[0] if rows else {}
