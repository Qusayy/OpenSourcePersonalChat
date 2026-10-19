"""What the server tests share: an engine that plays back prepared text, a
database that lasts for one test, stand-ins for the tools that would use the
network, and a reader for the event stream.

Nothing here needs a model, and nothing here may reach the network: a test
that tries to resolve a hostname fails on the spot.

Needs the app's own requirements (FastAPI, httpx for the test client). A test
module imports this inside its own ``try``, so that without them the module is
skipped rather than broken.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock

from fastapi.testclient import TestClient

from app import main
from app.agent.calls import render_call
from app.api import deps
from app.chatml import THINK_CLOSE
from app.config import settings
from app.llm import Engine, Stops
from app.llm.server import Reading
from app.store import conversations, db
from app.tools import base as tool_base
from app.tools.base import ToolResult


# ------------------------------------------------------------- the stream ---


def events(body: str) -> list[tuple[str, Any]]:
    """An event-stream body as ``(event, payload)`` pairs, in order."""
    out: list[tuple[str, Any]] = []
    for frame in body.split("\n\n"):
        name, data = None, None
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name is not None:
            out.append((name, data))
    return out


def shape(stream: list[tuple[str, Any]]) -> list[str]:
    """The order of a stream in words.

    A step is named by its id and status. However many tokens arrive in a row,
    they are one word here: how the answer was cut into pieces is not what
    these tests are about.
    """
    out: list[str] = []
    for name, data in stream:
        if name == "step":
            out.append(f"step:{data['id']}:{data['status']}")
        elif name == "token":
            if not out or out[-1] != "token":
                out.append("token")
        else:
            out.append(name)
    return out


# The line that says when a message was sent, as the model reads it after
# the message. It is the clock's, so a test that reads a prompt to the letter
# reads it without.
_SENT = re.compile(r"\n\n\(Sent [^()\n]*\.\)")


def unsent(messages: list[dict]) -> list[dict]:
    """A prompt without the lines that say when each message was sent."""
    return [{**m, "content": _SENT.sub("", m["content"])} for m in messages]


def only(stream: list[tuple[str, Any]], name: str) -> list[Any]:
    """Every payload of one kind of event."""
    return [data for event, data in stream if event == name]


def step(stream: list[tuple[str, Any]], step_id: str, status: str) -> dict:
    """The one step event with this id and status."""
    found = [d for d in only(stream, "step") if d["id"] == step_id and d["status"] == status]
    assert len(found) == 1, f"expected one {step_id}:{status} step, found {len(found)}"
    return found[0]


async def collect(stream) -> list[tuple[str, Any]]:
    """Everything an async stream yields, once it has ended."""
    return [item async for item in stream]


async def listen(stream, heard: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Everything an async stream yields, kept in ``heard`` as it arrives, so
    that a test can wait for something part-way through before going on."""
    async for item in stream:
        heard.append(item)
    return heard


async def until(done, timeout: float = 5.0) -> None:
    """Wait for something another task or thread is about to make true."""
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            raise TimeoutError("gave up waiting")
        await asyncio.sleep(0.001)


# ------------------------------------------------------------- the engine ---


class PlayedEngine(Engine):
    """A real Engine whose model is a script.

    The lock, the queue, the worker thread and the cancel flag are the
    engine's own. Only the text is played: each request is answered from its
    prompt. The model makes ``decision`` once, as its first reply to a
    message, and answers with ``answer`` once it has read what came of it, or
    at once where there is no decision. What each request was given is kept,
    so a test can read the exact prompt a reply read.

    ``decision`` is a call: a dict ``{"tool": name, "args": {...}}`` (written
    in the model's own format), or the raw text of a reply, for replies that
    are not a well-formed call. None, or ``{"tool": "none"}``, is no call.

    It stops at the strings a request is given, as a model server does, and
    where the model thinks it has thought nothing. For a script of several
    different replies use ``evals.scripted.ScriptedEngine``.
    """

    def __init__(
        self,
        answer: str = "A short answer.",
        decision: dict | str | None = None,
        *,
        ready: bool = True,
        cfg=None,
    ) -> None:
        super().__init__(cfg or settings.model_copy(update={"mock": True, "mock_delay": 0.0}))
        if ready:
            self.state.status = "ready"
        self.answer = answer
        self.decision = decision
        # Every request: its messages, the part of the reply it went on from,
        # its sampling and the strings it stopped at.
        self.prompts: list[list[dict]] = []
        self.prefixes: list[str] = []
        self.samplings: list[dict] = []
        self.stops: list[list[str] | None] = []

    @property
    def call(self) -> str | None:
        """The call the model makes, as it writes it."""
        decision = self.decision
        if decision is None or (isinstance(decision, dict) and decision.get("tool") in (None, "none")):
            return None
        if isinstance(decision, str):
            return decision
        return render_call(decision["tool"], decision.get("args") or {})

    @property
    def answer_prompts(self) -> list[list[dict]]:
        """The prompts of the replies that answered: those that began after
        what came of the call, or with no call to make."""
        return [m for m, prefix in zip(self.prompts, self.prefixes) if self._answers(m) and not prefix]

    def _answers(self, messages: list[dict]) -> bool:
        last = messages[-1]["content"] if messages else ""
        return self.call is None or last.startswith("<tool_response>") or "\n\n<tool_response>" in last

    def _iter_chunks(
        self, messages: list[dict], sampling: dict, *, prefix: str = "", seed=None,
        stop: list[str] | None = None, reading: Reading | None = None,
    ) -> Iterator[str]:
        reading = reading if reading is not None else Reading()
        self.prompts.append([dict(m) for m in messages])
        self.prefixes.append(prefix)
        self.samplings.append(dict(sampling))
        self.stops.append(list(stop) if stop else None)
        said = self.answer if self._answers(messages) else self.call
        if getattr(self.cfg, "think_mode", "none") == "on":
            said = THINK_CLOSE + "\n\n" + said
        rest = said[len(prefix):] if said.startswith(prefix) else said
        stops = Stops(stop)
        pieces = [("" if i == 0 else " ") + word for i, word in enumerate(rest.split(" "))] if rest else []
        for piece in pieces:
            out = stops.feed(piece)
            if out:
                yield out
            if stops.word is not None:
                reading.finish, reading.word = "word", stops.word
                return
        tail = stops.flush()
        if tail:
            yield tail
        reading.finish = "eos"


# ------------------------------------------------------- the surroundings ---


def temp_database(case: unittest.TestCase, *, mock_mode: bool = False) -> SimpleNamespace:
    """Point ``db`` at a file that lasts for one test. Returns the settings it reads."""
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    fake = SimpleNamespace(mock=mock_mode, resolved_db_path=Path(folder.name) / "test.db")
    patcher = mock.patch.object(db, "settings", fake)
    patcher.start()
    case.addCleanup(patcher.stop)
    db.init()
    case.addCleanup(db.close)
    return fake


def with_settings(case: unittest.TestCase, **values: Any) -> None:
    """Hold some settings at known values for one test, whatever the
    environment or a local ``.env`` says."""
    for name, value in values.items():
        patcher = mock.patch.object(settings, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)


def no_network(case: unittest.TestCase) -> None:
    """Make any attempt to look a host up fail the test that made it."""

    def refuse(host, *args, **kwargs):
        raise AssertionError(f"a test tried to reach {host!r}")

    patcher = mock.patch.object(socket, "getaddrinfo", refuse)
    patcher.start()
    case.addCleanup(patcher.stop)


def played_tools(
    case: unittest.TestCase, results: dict[str, ToolResult | list[ToolResult]]
) -> dict[str, list[dict]]:
    """Replace what the named tools do, and keep the dispatcher that calls them.

    Each tool answers with its prepared result: one result every time, or a
    list consumed in call order. Returns ``{tool: [the arguments of each
    call]}``, filled in as the calls happen.
    """
    seen: dict[str, list[dict]] = {name: [] for name in results}
    replaced = {}
    for name, prepared in results.items():
        queue = list(prepared) if isinstance(prepared, list) else None

        async def run(_name=name, _prepared=prepared, _queue=queue, **kwargs) -> ToolResult:
            seen[_name].append(kwargs)
            result = _queue.pop(0) if _queue is not None else _prepared
            # The dispatcher writes the timing onto what it is handed.
            return dataclasses.replace(result)

        replaced[name] = dataclasses.replace(tool_base.REGISTRY[name], run=run)
    patcher = mock.patch.dict(tool_base.REGISTRY, replaced)
    patcher.start()
    case.addCleanup(patcher.stop)
    return seen


class ChatCase(unittest.TestCase):
    """A test that talks to ``/api/chat`` through the real app."""

    def setUp(self) -> None:
        self.db_settings = temp_database(self)
        no_network(self)
        with_settings(self, tools_enabled=True, allow_outbound=True)
        # No `with`: the lifespan would open the real database and start
        # loading the model.
        self.client = TestClient(main.app)
        self.engine = self.use_engine(PlayedEngine())

    def use_engine(self, engine: PlayedEngine) -> PlayedEngine:
        patcher = mock.patch.object(deps, "engine", engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = engine
        return engine

    def chat(self, message: str, **body: Any) -> list[tuple[str, Any]]:
        res = self.client.post("/api/chat", json={"message": message, **body})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers["content-type"].startswith("text/event-stream"))
        return events(res.text)

    def conversation(self, stream: list[tuple[str, Any]]) -> dict:
        """The stored conversation a stream belongs to."""
        found = conversations.get(only(stream, "conversation")[0]["id"])
        assert found is not None
        return found
