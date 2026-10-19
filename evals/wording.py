"""Another wording of what the model reads, for one run.

The system text says who the model is and when to use a tool, under the menu
of tools, each with its description. With a small model a reworded line can
move what it does in either direction, and the only way to know which is to
run the tasks. A wording file lets a run try one without a change to the
code:

    @system: You are ...
    @tool clock: The date, the time and the day of the week ...

    Use a tool when ...

A line that begins ``@system`` replaces who the model is; ``@tool`` replaces
that tool's description. Everything else, if there is anything, replaces the
rules for using tools. Nothing is changed outside the ``with`` block it is
applied in.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from unittest import mock

from app.agent import transcript
from app.tools import base

_OVERRIDE = re.compile(r"^@tool\s+([\w-]+):[ \t]?(.*)$")
_SYSTEM = re.compile(r"^@system:[ \t]?(.*)$")


@dataclass(frozen=True)
class Wording:
    path: str
    # The rules for using tools, or None to keep them.
    rules: str | None = None
    # Who the model is, in place of the system text's first line.
    system: str | None = None
    tools: dict[str, str] = field(default_factory=dict)
    digest: str = ""

    def describe(self) -> dict:
        """What a report says of it: which file, and which version of it."""
        return {"file": self.path, "sha256": self.digest}


def load(path: str | Path) -> Wording:
    """Read a wording file. Raises ``ValueError`` saying what is wrong with
    one that could not be used."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    tools: dict[str, str] = {}
    system: str | None = None
    body: list[str] = []
    for line in raw.splitlines():
        match = _OVERRIDE.match(line)
        said = _SYSTEM.match(line)
        if said:
            system = said.group(1).strip()
        elif match:
            name, text = match.groups()
            tools[name] = text.strip()
        else:
            body.append(line)
    text = "\n".join(body).strip("\n")
    return Wording(
        path=path.as_posix(),
        rules=text if text.strip() else None,
        system=system,
        tools=tools,
        digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12],
    )


@contextmanager
def applied(wording: Wording | None) -> Iterator[None]:
    """Run what is inside with ``wording`` in place of the texts it names,
    and put them back afterwards. ``None`` changes nothing."""
    if wording is None:
        yield
        return
    unknown = sorted(set(wording.tools) - set(base.REGISTRY))
    if unknown:
        raise ValueError(f"{wording.path}: there is no tool {unknown[0]!r}")
    with ExitStack() as stack:
        if wording.rules is not None:
            stack.enter_context(mock.patch.object(transcript, "TOOL_RULES", wording.rules))
        if wording.system is not None:
            stack.enter_context(mock.patch.object(transcript, "WHO", wording.system))
        stack.enter_context(mock.patch.dict(base.REGISTRY, {
            name: dataclasses.replace(base.REGISTRY[name], description=text)
            for name, text in wording.tools.items()
        }))
        yield
