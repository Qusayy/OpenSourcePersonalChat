"""FastAPI application: pages, SSE chat, benchmark, conversation history."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import bench, db
from .config import APP_DIR, settings
from .llm import engine
from .personas import DEFAULT_PERSONA, get_persona, persona_list

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx must also carry `proxy_buffering off;` — without both, tokens
    # arrive in one lump at the end and the live metrics are meaningless.
    "X-Accel-Buffering": "no",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    # Load off the event loop so the page renders (and shows "warming") while
    # ~1 GB of weights is being faulted in.
    threading.Thread(target=engine.load_blocking, name="model-load", daemon=True).start()
    yield
    db.close()


app = FastAPI(title="Aurora", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def page_context(**extra) -> dict:
    ctx = {
        "info": engine.info(),
        "personas": persona_list(),
        "default_persona": DEFAULT_PERSONA,
        "settings": settings,
    }
    ctx.update(extra)
    return ctx


# ------------------------------------------------------------------ pages ---


@app.get("/", response_class=HTMLResponse)
async def page_chat(request: Request):
    return templates.TemplateResponse(
        request, "chat.html", page_context(page="chat", conversations=db.list_conversations())
    )


@app.get("/bench", response_class=HTMLResponse)
async def page_bench(request: Request):
    return templates.TemplateResponse(
        request,
        "bench.html",
        page_context(
            page="bench",
            cases=bench.CASES,
            reference=bench.REFERENCE,
            budget=bench.memory_budget(settings.n_ctx),
            latest=bench.latest_summary(),
        ),
    )


@app.get("/about", response_class=HTMLResponse)
async def page_about(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        page_context(page="about", budget=bench.memory_budget(settings.n_ctx)),
    )


# ------------------------------------------------------------------- api ----


@app.get("/api/health")
async def api_health():
    return {"engine": engine.info(), "chat": db.chat_stats()}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    persona: str | None = None


@app.post("/api/chat")
async def api_chat(payload: ChatRequest):
    persona = get_persona(payload.persona)
    user_text = payload.message.strip()

    cid = payload.conversation_id
    created = False
    if cid and db.get_conversation(cid) is None:
        cid = None
    if not cid:
        cid = db.create_conversation(persona.id, user_text)
        created = True

    history = db.conversation_messages(cid)
    db.add_message(cid, "user", user_text)
    db.touch_conversation(cid, persona.id)

    messages = [{"role": "system", "content": persona.system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_text})

    sampling = persona.sampling()
    fitted, prompt_tokens, dropped = engine.fit_context(messages, sampling["max_tokens"])

    async def event_stream() -> AsyncIterator[str]:
        convo = db.get_conversation(cid)
        yield sse(
            "conversation",
            {
                "id": cid,
                "title": convo["title"] if convo else "",
                "created": created,
                "dropped": dropped,
                "prompt_tokens": prompt_tokens,
            },
        )
        try:
            async for event, data in engine.stream_chat(fitted, sampling):
                if event == "done":
                    text = data.pop("text", "")
                    if text.strip():
                        db.add_message(
                            cid,
                            "assistant",
                            text,
                            ttft_ms=data.get("ttft_ms"),
                            gen_tps=data.get("gen_tps"),
                            prompt_tokens=data.get("prompt_tokens"),
                            completion_tokens=data.get("completion_tokens"),
                        )
                        db.touch_conversation(cid)
                        db.add_run(
                            "chat",
                            label=persona.id,
                            prompt_tokens=data.get("prompt_tokens"),
                            gen_tokens=data.get("completion_tokens"),
                            ttft_ms=data.get("ttft_ms"),
                            prefill_tps=data.get("prefill_tps"),
                            gen_tps=data.get("gen_tps"),
                            rss_mb=data.get("rss_mb"),
                        )
                    yield sse("done", data)
                else:
                    yield sse(event, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/cancel/{request_id}")
async def api_cancel(request_id: str):
    return {"cancelled": engine.cancel(request_id)}


@app.get("/api/personas")
async def api_personas():
    return {"personas": persona_list(), "default": DEFAULT_PERSONA}


# --------------------------------------------------------- conversations ----


@app.get("/api/conversations")
async def api_conversations():
    return {"conversations": db.list_conversations()}


@app.get("/api/conversations/{cid}")
async def api_conversation(cid: str):
    convo = db.get_conversation(cid)
    if convo is None:
        raise HTTPException(404, "No such conversation")
    return convo


@app.delete("/api/conversations/{cid}")
async def api_delete_conversation(cid: str):
    db.delete_conversation(cid)
    return {"ok": True}


# ----------------------------------------------------------------- bench ----


@app.post("/api/bench/run")
async def api_bench_run():
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, data in bench.run_suite():
                yield sse(event, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/bench/results")
async def api_bench_results():
    return {
        "latest": bench.latest_summary(),
        "history": bench.history(),
        "reference": bench.REFERENCE,
        "budget": bench.memory_budget(settings.n_ctx),
        "info": engine.info(),
    }


@app.exception_handler(404)
async def not_found(request: Request, exc):  # noqa: ANN001
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return templates.TemplateResponse(
        request,
        "chat.html",
        page_context(page="chat", conversations=db.list_conversations()),
        status_code=404,
    )
