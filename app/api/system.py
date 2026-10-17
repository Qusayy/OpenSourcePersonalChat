"""How the site is: the engine, the machine, and what there is to use."""

from __future__ import annotations

from fastapi import APIRouter

from ..agent.personas import DEFAULT_PERSONA, persona_list
from ..agent.skills import skill_list
from ..config import settings
from ..ops import telemetry
from ..store import db
from ..tools.base import available as available_tools
from . import deps
from .pages import bootstrap

router = APIRouter()


@router.get("/api/health")
async def api_health():
    return {"engine": deps.engine.info(), "system": telemetry.snapshot(), "chat": db.chat_stats()}


@router.get("/api/bootstrap")
async def api_bootstrap():
    """The same values a served page carries, for a page that was not served
    by this app (the Vite dev server)."""
    return bootstrap()


@router.get("/api/telemetry")
async def api_telemetry():
    """The figures a page polls once a second: small, and nothing else.

    Deliberately `async` with no awaits. It reads a few small files and updates
    one shared sample; run in the threadpool, two visitors polling at once
    would race on that sample.
    """
    info = deps.engine.info()
    return {
        "system": telemetry.snapshot(),
        "engine": {"status": info["status"], "busy": info["busy"], "queued": info["queued"]},
    }


@router.get("/api/tools")
async def api_tools():
    return {
        "tools": [t.as_dict() for t in available_tools()],
        "enabled": settings.tools_enabled,
        "outbound": settings.allow_outbound,
    }


@router.get("/api/skills")
async def api_skills():
    return {"skills": skill_list()}


@router.get("/api/personas")
async def api_personas():
    return {"personas": persona_list(), "default": DEFAULT_PERSONA}
