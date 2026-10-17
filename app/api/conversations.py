"""Conversation history: the list, one conversation, and forgetting one."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..store import db

router = APIRouter()


@router.get("/api/conversations")
async def api_conversations():
    return {"conversations": db.list_conversations()}


@router.get("/api/conversations/{cid}")
async def api_conversation(cid: str):
    convo = db.get_conversation(cid)
    if convo is None:
        raise HTTPException(404, "No such conversation")
    return convo


@router.delete("/api/conversations/{cid}")
async def api_delete_conversation(cid: str):
    db.delete_conversation(cid)
    return {"ok": True}
