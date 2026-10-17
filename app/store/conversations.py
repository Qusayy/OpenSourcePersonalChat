"""Conversations: their messages, the tool calls each answer made, and what
each answer was sent, kept so the next message can be read with it."""

from __future__ import annotations

import json
import time
import uuid

from ..ledger import ledger
from . import db, notes


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


# What a conversation's persona column holds now that there is one agent.
# Older rows keep the persona they were made under; nothing reads it.
AGENT = "agent"


def create(first_message: str) -> str:
    cid = uuid.uuid4().hex
    now = time.time()
    db.write(
        "INSERT INTO conversations (id, title, persona, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (cid, make_title(first_message), AGENT, now, now),
    )
    return cid


def touch(cid: str) -> None:
    db.write("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), cid))


def listing(limit: int = 60) -> list[dict]:
    return db.rows(
        "SELECT c.id, c.title, c.persona, c.created_at, c.updated_at,"
        "       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n"
        "  FROM conversations c ORDER BY c.updated_at DESC LIMIT ?",
        (limit,),
    )


def get(cid: str) -> dict | None:
    rows = db.rows("SELECT * FROM conversations WHERE id = ?", (cid,))
    if not rows:
        return None
    convo = rows[0]
    convo["messages"] = db.rows(
        "SELECT id, role, content, created_at, ttft_ms, gen_tps, prompt_tokens,"
        "       completion_tokens, correction"
        "  FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (cid,),
    )
    # Hang each tool call off the message it produced, so reloading a
    # conversation replays its cards instead of losing them.
    calls = tool_calls(cid)
    by_message: dict[str, list[dict]] = {}
    orphans = []
    for call in calls:
        if call["message_id"]:
            by_message.setdefault(call["message_id"], []).append(call)
        else:
            orphans.append(call)
    for message in convo["messages"]:
        message["tool_calls"] = by_message.get(message["id"], [])
    # And each answer's run, as it was kept, for as long as its steps are.
    runs = _runs_by_message(cid)
    for message in convo["messages"]:
        if message["role"] == "assistant":
            message["run"] = runs.get(message["id"])
    convo["orphan_tool_calls"] = orphans
    return convo


def head(cid: str) -> dict | None:
    """A conversation's own row, without its messages: whether it is there,
    and what it is called."""
    rows = db.rows("SELECT id, title, persona FROM conversations WHERE id = ?", (cid,))
    return rows[0] if rows else None


def _runs_by_message(cid: str) -> dict[str, dict]:
    """Each answered run of a conversation, read back from its steps, by the
    message it answered with. Fetched by conversation, which is indexed."""
    rows = db.rows(
        "SELECT run_id, message_id, idx, kind, name, via, prompt_tokens, new_tokens,"
        "       output_tokens, queue_ms, first_token_ms, total_ms, ok, error"
        "  FROM steps WHERE conversation_id = ? ORDER BY created_at ASC, idx ASC",
        (cid,),
    )
    grouped: dict[str, list[dict]] = {}
    run_of: dict[str, str] = {}
    for row in rows:
        if row["message_id"]:
            grouped.setdefault(row["message_id"], []).append(row)
            run_of[row["message_id"]] = row["run_id"]
    out = {}
    for mid, steps in grouped.items():
        steps.sort(key=lambda r: r["idx"])
        out[mid] = {"id": run_of[mid], "outcome": None, **ledger(steps), "wall_ms": None}
    return out


def delete(cid: str) -> None:
    notes.forget_conversation(cid)
    db.write("DELETE FROM context_turns WHERE conversation_id = ?", (cid,))
    db.write("DELETE FROM steps WHERE conversation_id = ?", (cid,))
    db.write("DELETE FROM tool_calls WHERE conversation_id = ?", (cid,))
    db.write("DELETE FROM messages WHERE conversation_id = ?", (cid,))
    db.write("DELETE FROM conversations WHERE id = ?", (cid,))


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
    db.write(
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
    db.write(
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
        db.write("UPDATE tool_calls SET message_id = ? WHERE id = ?", (message_id, tid))


def tool_calls(cid: str) -> list[dict]:
    rows = db.rows(
        "SELECT * FROM tool_calls WHERE conversation_id = ? ORDER BY created_at ASC", (cid,)
    )
    for r in rows:
        r["args"] = json.loads(r.pop("args_json") or "{}")
        r["data"] = json.loads(r.pop("data_json") or "{}")
        r["ok"] = bool(r["ok"])
    return rows



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
    db.write(
        "INSERT INTO context_turns (id, conversation_id, run_id, question_id, answer_id,"
        " system_key, parts_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, cid, run_id, question_id, answer_id, system_key,
         json.dumps(parts, ensure_ascii=False), time.time()),
    )
    return tid


def record(cid: str) -> tuple[list[dict], list[dict]]:
    """A conversation as the next message is read with it: its messages,
    oldest first, and what was kept of what each answer was sent."""
    messages = db.rows(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ?"
        " ORDER BY created_at ASC",
        (cid,),
    )
    records = db.rows(
        "SELECT question_id, answer_id, system_key, parts_json, created_at FROM context_turns"
        " WHERE conversation_id = ? ORDER BY created_at ASC",
        (cid,),
    )
    for record in records:
        record["parts"] = json.loads(record.pop("parts_json"))
    return messages, records
