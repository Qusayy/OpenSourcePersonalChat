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

from . import __version__, bench, db
from .config import APP_DIR, settings
from .llm import engine
from .personas import DEFAULT_PERSONA, get_persona, persona_list
from .router import route
from .skills import build_prompt, get_skill, skill_list
from .tools import REGISTRY, call_tool
from .tools.base import available as available_tools

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
        "version": __version__,
        "skills": skill_list(),
        "tools": [t.as_dict() for t in available_tools()],
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
    skill: str | None = None


@app.post("/api/chat")
async def api_chat(payload: ChatRequest):
    user_text = payload.message.strip()
    skill = get_skill(payload.skill)
    persona = get_persona(skill.persona if skill else payload.persona)

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
    sampling = persona.sampling()

    async def event_stream() -> AsyncIterator[str]:
        convo = db.get_conversation(cid)
        yield sse(
            "conversation",
            {
                "id": cid,
                "title": convo["title"] if convo else "",
                "created": created,
                "skill": skill.id if skill else None,
                "persona": persona.id,
            },
        )

        calls: list = []
        stored_call_ids: list[str] = []
        summaries: list[str] = []

        try:
            # ---------------------------------------------------- routing --
            if skill is not None:
                calls = skill.prepare(user_text)
                yield sse("step", {
                    "id": "route", "kind": "route", "status": "ok",
                    "label": skill.name, "detail": "skill",
                    "glyph": skill.glyph, "tool": skill.id, "ms": 0,
                })
            elif settings.tools_enabled:
                needs_pass = persona.tools and not user_text.startswith("/")
                if needs_pass:
                    yield sse("step", {
                        "id": "route", "kind": "route", "status": "running",
                        "label": "Choosing a tool", "glyph": "◇",
                    })
                call, telemetry = await route(
                    user_text, tools_enabled=persona.tools, engine=engine
                )
                if call is not None:
                    calls = [call]
                if call is not None or needs_pass:
                    yield sse("step", {
                        "id": "route", "kind": "route", "status": "ok",
                        "label": REGISTRY[call.name].label if call else "No tool needed",
                        "detail": telemetry.get("tier") or "direct",
                        "glyph": REGISTRY[call.name].glyph if call else "◇",
                        "tool": call.name if call else None,
                        "ms": round(telemetry.get("ms") or 0, 1),
                    })

            # ------------------------------------------------ run the tools --
            for index, call in enumerate(calls):
                tool = REGISTRY.get(call.name)
                step_id = f"tool-{index}"
                yield sse("step", {
                    "id": step_id, "kind": "tool", "status": "running",
                    "label": tool.label if tool else call.name,
                    "glyph": tool.glyph if tool else "◆",
                    "tool": call.name, "args": call.args,
                })
                result = await call_tool(call.name, call.args)
                stored_call_ids.append(db.add_tool_call(
                    cid, call.name, via=call.via, args=call.args,
                    card=result.card if result.ok else None,
                    data=result.data, summary=result.summary or result.error or "",
                    ms=result.ms, ok=result.ok,
                ))
                yield sse("step", {
                    "id": step_id, "kind": "tool", "status": "ok" if result.ok else "failed",
                    "label": tool.label if tool else call.name,
                    "glyph": tool.glyph if tool else "◆",
                    "tool": call.name, "ms": round(result.ms, 1),
                    "detail": None if result.ok else (result.error or "failed"),
                })
                if result.ok:
                    summaries.append(f"{call.name}: {result.summary}")
                    yield sse("card", {
                        "step_id": step_id, "card": result.card,
                        "tool": call.name, "data": result.data,
                    })
                else:
                    # A failed tool is shown, not hidden — the model still answers.
                    yield sse("card", {
                        "step_id": step_id, "card": "error", "tool": call.name,
                        "data": {"message": result.summary or result.error},
                    })

            # ------------------------------------------------ build prompt --
            messages = [{"role": "system", "content": persona.system}]
            messages += [{"role": m["role"], "content": m["content"]} for m in history]

            if skill is not None:
                messages.append({"role": "user", "content": build_prompt(
                    skill, user_text, summaries
                )})
            else:
                if summaries:
                    messages.append({"role": "system", "content": (
                        "Tool results — use these figures exactly and do not "
                        "recompute them:\n" + "\n".join(f"- {s}" for s in summaries)
                    )})
                messages.append({"role": "user", "content": user_text})

            fitted, prompt_tokens, dropped = engine.fit_context(
                messages, sampling["max_tokens"]
            )
            if dropped:
                yield sse("trimmed", {"dropped": dropped})

            # ---------------------------------------------------- generate --
            yield sse("step", {
                "id": "answer", "kind": "model", "status": "running",
                "label": "Writing the answer", "glyph": "✦",
            })

            async for event, data in engine.stream_chat(fitted, sampling):
                if event == "done":
                    text = data.pop("text", "")
                    data["tool_calls"] = len(calls)
                    if text.strip():
                        mid = db.add_message(
                            cid, "assistant", text,
                            ttft_ms=data.get("ttft_ms"),
                            gen_tps=data.get("gen_tps"),
                            prompt_tokens=data.get("prompt_tokens"),
                            completion_tokens=data.get("completion_tokens"),
                        )
                        db.attach_tool_calls(mid, stored_call_ids)
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
                    yield sse("step", {
                        "id": "answer", "kind": "model", "status": "ok",
                        "label": "Answer", "glyph": "✦",
                        "ms": round(data.get("total_ms") or 0, 1),
                    })
                    yield sse("done", data)
                else:
                    yield sse(event, data)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/tools")
async def api_tools():
    return {
        "tools": [t.as_dict() for t in available_tools()],
        "enabled": settings.tools_enabled,
        "outbound": settings.allow_outbound,
    }


@app.get("/api/skills")
async def api_skills():
    return {"skills": skill_list()}


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
