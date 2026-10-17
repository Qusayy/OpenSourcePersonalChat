"""The chat: one message in, its run out as an event stream; and stopping a run."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import aclosing
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import agent
from ..agent.context import turns_from
from ..agent.style import asks_for_project, style_for
from ..store import conversations, projects
from ..tools import REGISTRY
from ..tools.results import links_of
from . import deps
from .keeper import Keeper
from .sse import SSE_HEADERS, sse

router = APIRouter()


class ChatRequest(BaseModel):
    """A message. Anything else a page sends with it (an older page sends a
    persona and a skill) is ignored."""

    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


@router.post("/api/chat")
async def api_chat(payload: ChatRequest):
    engine = deps.engine
    user_text = payload.message.strip()

    cid = payload.conversation_id
    created = False
    if cid and conversations.head(cid) is None:
        cid = None
    if not cid:
        cid = conversations.create(user_text)
        created = True

    # What was said, which decides which addresses the visitor gave, and what
    # was sent, which is what the model reads of the conversation.
    messages, records = conversations.record(cid)
    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    context = turns_from(messages, records)
    question_id = conversations.add_message(cid, "user", user_text)
    conversations.touch(cid)
    links = last_links(cid)
    project = projects.manifest(cid)
    # A project asked for a message or two ago, and not written yet, is still
    # the one being made: "first init a hello world fastapi" goes on with it.
    wanted = bool(project) or any(
        asks_for_project(m["content"]) for m in history[-8:] if m["role"] == "user"
    )
    style = style_for(user_text, project=wanted)

    # The name this run can be stopped by, from here to its last event.
    run_id = uuid.uuid4().hex

    async def event_stream() -> AsyncIterator[str]:
        # Open before the page is told the id, so a Stop sent the moment it
        # arrives finds a run to stop.
        with engine.hold(run_id):
            convo = conversations.head(cid)
            yield sse(
                "conversation",
                {
                    "id": cid,
                    "title": convo["title"] if convo else "",
                    "created": created,
                    "request_id": run_id,
                },
            )

            trace = agent.Trace(run_id=run_id)
            keeper = Keeper(cid, trace, question_id, label=style.id)
            run = agent.run(
                user_text, style=style, history=history, trace=trace,
                engine=engine, request_id=run_id, context=context, scope=cid, links=links,
                project=project,
            )
            try:
                # Closed with this response, not whenever it is collected: a
                # run that nobody is reading must let go of the engine.
                async with aclosing(run) as events:
                    async for event, data in events:
                        keeper.keep()
                        yield sse(event, data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


def last_links(cid: str) -> tuple:
    """The last list of results the conversation was shown: what "open the
    second one" means in the next message."""
    for call in reversed(conversations.tool_calls(cid)):
        tool = REGISTRY.get(call["tool"])
        if call["ok"] and tool is not None and tool.links:
            return links_of(call["data"])
    return ()


@router.post("/api/cancel/{request_id}")
async def api_cancel(request_id: str):
    return {"cancelled": deps.engine.cancel(request_id)}
