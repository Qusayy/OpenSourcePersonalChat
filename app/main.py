"""The FastAPI application: the gate in front, then every group of routes.

The routes themselves live in ``app/api/``, one module per group.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import bench, chat, conversations, deps, pages, projects, system
from .gate import GateMiddleware, Message, Receive, Scope, Send
from .ops import telemetry
from .store import db

# What every answer says about itself, the cover's and the gate's refusals
# included: it is never to be read as another type than it says, shown in
# another site's frame, or named to the next site in a Referer.
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
)


class SecurityHeaders:
    """Adds SECURITY_HEADERS to every response that does not set its own.

    Plain ASGI, like the gate, and outside it: only the start of a response
    is touched, and a stream's body goes through as it is sent.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or ())
                present = {bytes(name).lower() for name, _ in headers}
                headers.extend(pair for pair in SECURITY_HEADERS if pair[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, wrapped)


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
# Added last, so outermost: the gate's own answers carry these too.
app.add_middleware(SecurityHeaders)
for group in (pages, system, chat, conversations, projects, bench):
    app.include_router(group.router)
app.add_exception_handler(404, pages.handle_404)
