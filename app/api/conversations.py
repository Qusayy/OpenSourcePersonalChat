"""Conversation history: the list, one conversation, and forgetting one."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..store import conversations, projects

router = APIRouter()


@router.get("/api/conversations")
async def api_conversations():
    return {"conversations": conversations.listing()}


@router.get("/api/conversations/{cid}")
async def api_conversation(cid: str):
    convo = conversations.get(cid)
    if convo is None:
        raise HTTPException(404, "No such conversation")
    # What its project has, without the files themselves; the page asks for
    # those when it shows the project.
    convo["project"] = projects.manifest(cid)
    return convo


@router.delete("/api/conversations/{cid}")
async def api_delete_conversation(cid: str):
    conversations.delete(cid)
    return {"ok": True}
