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
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock

from fastapi.testclient import TestClient

from app import db, main
from app.config import settings
from app.llm import Completion, Engine
from app.tools import base as tool_base
from app.tools.base import ToolResult

# What a played routing pass reports as its time. Fixed, so a test can tell the
# figure on the page came from the pass and not from somewhere else.
ROUTE_MS = 12.5
# And as the number of tokens it wrote.
ROUTE_TOKENS = 7


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

    The lock, the queue, the worker thread and the cancel flag are the engine's
    own for the answer. Choosing a tool is answered directly, with a fixed
    time, so a test can tell which figure on the page came from that pass. What
    each was asked is kept, so a test can read the exact prompt a pass was
    given.

    For a routing pass that really goes through the slot, use
    ``evals.scripted.ScriptedEngine``.
    """

    def __init__(
        self,
        answer: str = "A short answer.",
        decision: dict | str | None = None,
        *,
        ready: bool = True,
    ) -> None:
        super().__init__(settings.model_copy(update={"mock": True, "mock_delay": 0.0}))
        if ready:
            self.state.status = "ready"
        self.answer = answer
        # A dict is what the model chose. A string is its raw reply, for the
        # replies that are not JSON at all. None is "no tool".
        self.decision = decision
        self.answer_prompts: list[list[dict]] = []
        self.samplings: list[dict] = []
        self.route_prompts: list[list[dict]] = []
        self.grammars: list[str] = []
        self.route_limits: list[int | None] = []

    def _iter_chunks(self, messages: list[dict], sampling: dict, *args, **kwargs) -> Iterator[str]:
        self.answer_prompts.append([dict(m) for m in messages])
        self.samplings.append(dict(sampling))
        if not self.answer:
            return
        for i, word in enumerate(self.answer.split(" ")):
            yield ("" if i == 0 else " ") + word

    async def complete_json(self, messages, grammar_text, max_tokens=None, **kwargs) -> Completion:
        if not self.ready:
            return Completion(error="The model is still loading.")
        self.route_prompts.append([dict(m) for m in messages])
        self.grammars.append(grammar_text)
        self.route_limits.append(max_tokens)
        decision = {"tool": "none"} if self.decision is None else self.decision
        raw = decision if isinstance(decision, str) else json.dumps(decision)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        return Completion(
            parsed=parsed, raw=raw, ms=ROUTE_MS,
            prompt_tokens=self.prompt_tokens(messages), completion_tokens=ROUTE_TOKENS,
            queue_wait_ms=0.0, ttft_ms=ROUTE_MS, total_ms=ROUTE_MS,
        )


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
        patcher = mock.patch.object(main, "engine", engine)
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
        found = db.get_conversation(only(stream, "conversation")[0]["id"])
        assert found is not None
        return found
