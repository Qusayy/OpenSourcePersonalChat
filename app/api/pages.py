"""The pages: one HTML file for every route, with the first values a page
shows written into it.

The browser's router draws the page. The title and description are still set
here, per route, so a link preview or a reader without JavaScript gets the
right ones.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..agent.personas import DEFAULT_PERSONA, persona_list
from ..agent.skills import skill_list
from ..ops import telemetry
from ..store import db
from ..tools.base import available as available_tools
from . import deps
from .bench import bench_static
from .frontend import Frontend, not_found

router = APIRouter()
frontend = Frontend()


def bootstrap() -> dict:
    """The first values a page shows, written into its HTML so they are on
    screen at load instead of appearing a second later.

    Everything here is also public through the API. It is what the page reads
    and no more: no system prompts, no paths.
    """
    return {
        "version": __version__,
        "info": deps.engine.info(),
        "system": telemetry.snapshot(),
        "personas": persona_list(),
        "defaultPersona": DEFAULT_PERSONA,
        "skills": skill_list(),
        "tools": [t.as_dict() for t in available_tools()],
        "conversations": db.list_conversations(),
        "bench": bench_static(),
    }


def render_page(page: str, status_code: int = 200) -> Response:
    data = bootstrap()
    info = data["info"]
    titles = {
        "chat": f"Aurora — {info['model']}",
        "bench": f"Benchmark — {info['model']}",
        "about": "About — Aurora",
    }
    description = (
        f"{info['model']} on a {info['hardware']}: quick answers, "
        "with tools for sums, weather and links."
    )
    return frontend.page(
        page=page,
        title=titles[page],
        description=description,
        bootstrap=data,
        status_code=status_code,
    )


@router.get("/")
async def page_chat():
    return render_page("chat")


@router.get("/bench")
async def page_bench():
    return render_page("bench")


@router.get("/about")
async def page_about():
    return render_page("about")


@router.get("/assets/{name:path}")
async def asset(name: str):
    return frontend.asset(name)


async def handle_404(request: Request, exc):  # noqa: ANN001
    path = request.url.path
    if path.startswith("/api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    # A missing file is a missing file. Only something that could be a page
    # gets the page.
    if path.startswith("/assets/") or "." in path.rsplit("/", 1)[-1]:
        return not_found()
    return render_page("chat", status_code=404)
