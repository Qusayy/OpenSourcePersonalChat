"""FastAPI application: pages, SSE chat, benchmark, conversation history."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from contextlib import aclosing, asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__, agent, bench, db, telemetry
from .config import settings
from .llm import engine
from .personas import DEFAULT_PERSONA, get_persona, persona_list
from .skills import get_skill, skill_list
from .tools.base import available as available_tools
from .web import Frontend, not_found

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
    # CPU load is a difference between two readings; take the first now so the
    # first page view has something to measure against.
    telemetry.sampler.baseline()
    # Load off the event loop so the page renders (and shows "warming") while
    # ~1 GB of weights is being faulted in.
    threading.Thread(target=engine.load_blocking, name="model-load", daemon=True).start()
    yield
    db.close()


app = FastAPI(title="Aurora", lifespan=lifespan, docs_url=None, redoc_url=None)
frontend = Frontend()


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def bench_static() -> dict:
    """What the benchmark page shows before any run: the suite, the reference
    machines, the memory estimate and how a larger model would stand here."""
    budget = bench.memory_budget(settings.n_ctx)
    return {
        "cases": bench.CASES,
        "reference": bench.REFERENCE,
        "budget": budget,
        "larger": bench.larger_model(budget, telemetry.snapshot()),
        "latest": bench.latest_summary(),
    }


def bootstrap() -> dict:
    """The first values a page shows, written into its HTML so they are on
    screen at load instead of appearing a second later.

    Everything here is also public through the API. It is what the page reads
    and no more: no system prompts, no paths.
    """
    return {
        "version": __version__,
        "info": engine.info(),
        "system": telemetry.snapshot(),
        "personas": persona_list(),
        "defaultPersona": DEFAULT_PERSONA,
        "skills": skill_list(),
        "tools": [t.as_dict() for t in available_tools()],
        "conversations": db.list_conversations(),
        "bench": bench_static(),
    }


# ------------------------------------------------------------------ pages ---

# One HTML file serves every route; the browser's router draws the page. The
# title and description are still set here, per route, so a link preview or a
# reader without JavaScript gets the right ones.


def render_page(page: str, status_code: int = 200) -> Response:
    data = bootstrap()
    info = data["info"]
    titles = {
        "chat": f"Aurora — {info['model']}",
        "bench": f"Benchmark — {info['model']}",
        "about": "About — Aurora",
    }
    description = (
        f"{info['params']} {info['model']} running locally on a {info['hardware']} "
        "— with the numbers on screen."
    )
    return frontend.page(
        page=page,
        title=titles[page],
        description=description,
        bootstrap=data,
        status_code=status_code,
    )


@app.get("/")
async def page_chat():
    return render_page("chat")


@app.get("/bench")
async def page_bench():
    return render_page("bench")


@app.get("/about")
async def page_about():
    return render_page("about")


@app.get("/assets/{name:path}")
async def asset(name: str):
    return frontend.asset(name)


# ------------------------------------------------------------------- api ----


@app.get("/api/health")
async def api_health():
    return {"engine": engine.info(), "system": telemetry.snapshot(), "chat": db.chat_stats()}


@app.get("/api/bootstrap")
async def api_bootstrap():
    """The same values a served page carries, for a page that was not served
    by this app (the Vite dev server)."""
    return bootstrap()


@app.get("/api/telemetry")
async def api_telemetry():
    """The figures a page polls once a second: small, and nothing else.

    Deliberately `async` with no awaits. It reads a few small files and updates
    one shared sample; run in the threadpool, two visitors polling at once
    would race on that sample.
    """
    info = engine.info()
    return {
        "system": telemetry.snapshot(),
        "engine": {"status": info["status"], "busy": info["busy"], "queued": info["queued"]},
    }


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
    # Whether this message answers a question the model put to the visitor.
    follows_ask = db.last_message_asked(cid)
    db.add_message(cid, "user", user_text)
    db.touch_conversation(cid, persona.id)

    # The name this run can be stopped by, from here to its last event.
    run_id = uuid.uuid4().hex

    async def event_stream() -> AsyncIterator[str]:
        # Open before the page is told the id, so a Stop sent the moment it
        # arrives finds a run to stop.
        with engine.hold(run_id):
            convo = db.get_conversation(cid)
            yield sse(
                "conversation",
                {
                    "id": cid,
                    "title": convo["title"] if convo else "",
                    "created": created,
                    "skill": skill.id if skill else None,
                    "persona": persona.id,
                    "request_id": run_id,
                },
            )

            trace = agent.Trace(run_id=run_id)
            keeper = _Keeper(cid, persona.id, trace)
            run = agent.run(
                user_text, persona=persona, skill=skill, history=history, trace=trace,
                engine=engine, request_id=run_id, follows_ask=follows_ask,
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


class _Keeper:
    """Stores what a run has done, before the page is told it happened.

    The run records each thing it finishes and then says so. ``keep`` is called
    as each event arrives and before it is sent on, so a tool call is in the
    database before its step closes, and the answer before ``done``, which the
    page answers by asking for the conversation list.
    """

    def __init__(self, cid: str, persona_id: str, trace: agent.Trace) -> None:
        self.cid = cid
        self.persona_id = persona_id
        self.trace = trace
        self.call_ids: list[str] = []
        self.steps_kept = 0
        self.answered = False

    def keep(self) -> None:
        for call, result in self.trace.calls[len(self.call_ids):]:
            self.call_ids.append(db.add_tool_call(
                self.cid, call.name, via=call.via, args=call.args,
                card=result.card if result.ok else None,
                data=result.data, summary=result.summary or result.error or "",
                ms=result.ms, ok=result.ok,
            ))

        for step in self.trace.steps[self.steps_kept:]:
            db.add_step(
                self.trace.run_id, self.cid, self.steps_kept, step.kind,
                name=step.name, via=step.via, input=step.input, output=step.output,
                prompt_tokens=step.prompt_tokens, new_tokens=step.new_tokens,
                output_tokens=step.output_tokens, queue_ms=step.queue_ms,
                first_token_ms=step.first_token_ms, total_ms=step.total_ms,
                ok=step.ok, error=step.error, created_at=step.at,
            )
            self.steps_kept += 1

        if self.trace.answer is None or self.answered:
            return
        self.answered = True
        # An answer with nothing in it is not a message. Its tool calls stay
        # with the conversation and are replayed from there.
        if not self.trace.answer.strip():
            return
        usage = self.trace.usage
        mid = db.add_message(
            self.cid, "assistant", self.trace.answer,
            ttft_ms=usage.get("ttft_ms"),
            gen_tps=usage.get("gen_tps"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        db.attach_tool_calls(mid, self.call_ids)
        db.attach_steps(mid, self.trace.run_id)
        db.touch_conversation(self.cid)
        if usage.get("asked"):
            # A question put back to the visitor was written by a routing
            # pass. It is a message, and it is not a sample of how fast this
            # machine writes answers.
            return
        db.add_run(
            "chat",
            label=self.persona_id,
            prompt_tokens=usage.get("prompt_tokens"),
            gen_tokens=usage.get("completion_tokens"),
            ttft_ms=usage.get("ttft_ms"),
            prefill_tps=usage.get("prefill_tps"),
            gen_tps=usage.get("gen_tps"),
            rss_mb=usage.get("rss_mb"),
        )


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
    return {**bench_static(), "history": bench.history(), "info": engine.info()}


@app.exception_handler(404)
async def handle_404(request: Request, exc):  # noqa: ANN001
    path = request.url.path
    if path.startswith("/api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    # A missing file is a missing file. Only something that could be a page
    # gets the page.
    if path.startswith("/assets/") or "." in path.rsplit("/", 1)[-1]:
        return not_found()
    return render_page("chat", status_code=404)
