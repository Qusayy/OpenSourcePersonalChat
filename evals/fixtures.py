"""Tool results played back from files, so no task touches the network.

A fixture is one JSON file under ``evals/fixtures``::

    {
      "tool": "weather",
      "args": {"location": "Lisbon"},
      "recorded": null,
      "source": "written by hand",
      "result": {"ok": true, "summary": "...", "card": "weather", "data": {...}, "error": null}
    }

``recorded`` is the date a real response was captured, or null with a
``source`` saying where the content came from instead. ``args`` is what the
recording was made with; it documents the fixture and is not matched against.

Only what a tool *does* is replaced. The dispatcher that calls it is the real
one, so argument checking, the time limit and the offline switch are all still
in the path, and the grammar and menu are built from the real registry. Tools
that need no network run for real.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock

from app.config import settings
from app.tools import base
from app.tools.base import ToolResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> dict:
    """One fixture file, by its name under ``evals/fixtures``."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _result(fixture: dict) -> ToolResult:
    r = fixture["result"]
    return ToolResult(
        ok=bool(r["ok"]),
        summary=r.get("summary") or "",
        card=r.get("card"),
        data=r.get("data") or {},
        error=r.get("error"),
    )


@contextmanager
def recorded(tools: dict[str, str | list[str]] | None = None) -> Iterator[dict[str, list[dict]]]:
    """Play back recorded results for the named tools for as long as the block lasts.

    ``tools`` maps a tool to one fixture file, used for every call, or to a
    list of files used in call order. A tool that goes out and was given no
    fixture answers that there is no recording, which is a failed call and not
    an exception: a task that calls the wrong tool should fail its
    expectations, not crash the suite.

    Yields ``{tool: [the arguments of each call]}``, filled in as calls happen.
    """
    tools = tools or {}
    seen: dict[str, list[dict]] = {}
    replaced = {}

    for name, tool in base.REGISTRY.items():
        files = tools.get(name)
        if files is None and not tool.network:
            continue  # nothing to go wrong offline: it runs for real
        fixtures = [load(f) for f in ([files] if isinstance(files, str) else files or [])]
        seen[name] = []

        async def run(_name=name, _fixtures=fixtures, _left=list(fixtures), **kwargs) -> ToolResult:
            seen[_name].append(kwargs)
            if not _fixtures:
                return ToolResult(
                    False, f"There is no recorded response for {_name}.", error="no recording"
                )
            # One file answers every call; a list is used up in order, and its
            # last entry goes on answering.
            fixture = _left.pop(0) if len(_left) > 1 else _left[0]
            if fixture.get("delay_ms"):
                await asyncio.sleep(fixture["delay_ms"] / 1000)
            return _result(fixture)

        # A tool with a recording stands in for what it needs (the sandbox,
        # say), so it is on offer wherever the suite runs.
        replaced[name] = dataclasses.replace(
            tool, run=run, requires=None if files is not None else tool.requires,
        )

    # The recordings stand in for the network, so the tools that use it must
    # be on offer whatever this machine's settings say.
    with mock.patch.dict(base.REGISTRY, replaced), \
            mock.patch.object(settings, "allow_outbound", True), \
            mock.patch.object(settings, "tools_enabled", True):
        yield seen
