"""The FastAPI application: the gate in front, then every group of routes.

The routes themselves live in ``app/api/``, one module per group.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import bench, chat, conversations, deps, pages, system
from .gate import GateMiddleware
from .ops import telemetry
from .store import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    # CPU load is a difference between two readings; take the first now so the
    # first page view has something to measure against.
    telemetry.sampler.baseline()
    # Load off the event loop so the page renders (and shows "warming") while
    # ~1 GB of weights is being faulted in, or while a model server starts.
    # The site waits for that server as long as it takes.
    threading.Thread(
        target=deps.engine.load_blocking, kwargs={"keep_waiting": True},
        name="model-load", daemon=True,
    ).start()
    yield
    db.close()


app = FastAPI(title="Aurora", lifespan=lifespan, docs_url=None, redoc_url=None)
# Before every route and every error page: see app/gate.py.
app.add_middleware(GateMiddleware)
for group in (pages, system, chat, conversations, bench):
    app.include_router(group.router)
app.add_exception_handler(404, pages.handle_404)
