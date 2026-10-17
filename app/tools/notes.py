"""Notes: what the Agent is asked to keep, and to find again.

A note is a line of the user's own, kept when they ask for it and read back
when a later message needs it. Whose notes they are is the deployment's
choice (``AURORA_NOTES``): by default a conversation's notes belong to it
alone, since this site has no accounts and every visitor would otherwise read
every other's.

The conversation a note belongs to is not an argument the model can give: it
is the call's own (``CALL``), set by whoever runs the message.

``remember`` writes, so it is only made on the user's own word. A note the
model chose to make after the run had read a page is refused before it gets
here (see the agent's address rule, which this extends).
"""

from __future__ import annotations

from ..store import notes as note_store
from .base import CALL, Arg, Tool, ToolResult, notes_scope, register

# How many notes one recall reads back.
RECALLED = 5


def _owner() -> str | None:
    """The conversation a note is kept for: this one, or none for the site's."""
    return CALL.get().conversation_id if notes_scope() == "conversation" else None


async def _remember(text: str) -> ToolResult:
    text = " ".join((text or "").split())
    if not text:
        return ToolResult(False, "There is nothing to remember.", error="empty")
    scope = notes_scope()
    if scope == "conversation" and not CALL.get().conversation_id:
        return ToolResult(False, "A note can only be kept in a conversation.", error="no conversation")
    note_store.add_note(text, scope=scope, conversation_id=_owner())
    where = "this conversation" if scope == "conversation" else "this site"
    return ToolResult(
        True, f"Noted for {where}: {text}", card="note",
        data={"action": "remember", "text": text, "scope": scope},
    )


async def _recall(query: str = "") -> ToolResult:
    query = " ".join((query or "").split())
    scope = notes_scope()
    found = note_store.search_notes(query, scope=scope, conversation_id=_owner(), limit=RECALLED)
    notes = [row["text"] for row in found]
    if notes:
        summary = "Notes: " + "; ".join(notes)
    else:
        summary = f"No note matches {query!r}." if query else "There are no notes yet."
    return ToolResult(
        True, summary, card="note",
        data={"action": "recall", "query": query, "notes": notes, "scope": scope},
    )


register(Tool(
    name="remember",
    label="Remember",
    glyph="✎",
    description="Keep a short note the user asks to have remembered, in their words.",
    args=(Arg("text", "string", True, "the note, in the user's words", max_len=200),),
    run=_remember,
    card="note",
    example="/remember my flat is on the third floor",
    notes=True,
    writes=True,
))

register(Tool(
    name="recall",
    label="Recall",
    glyph="✎",
    description="Find notes the user asked to have remembered earlier.",
    args=(Arg("query", "string", False, "what to look for", default="", max_len=80),),
    run=_recall,
    card="note",
    example="/recall floor",
    notes=True,
))
