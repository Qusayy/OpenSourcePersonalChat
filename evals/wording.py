"""Another wording of what the model reads when it chooses, for one run.

A choice reads the persona's system text, the loop's rules and a menu of
tools and skills. With a model this small a reworded line can move routing in
either direction, and the only way to know which is to run the tasks. A
wording file lets a run try one without a change to the code:

    @persona agent: You are …
    @tool clock: The date, the time and the day of the week …
    @skill compare: Compare two things …

    Most messages need no tool …
    Tools:
    {menu}
    …

A line that begins ``@persona``, ``@tool`` or ``@skill`` replaces that one
text. Everything else, if there is anything, replaces the loop's rules; it is
written as the model will read it, braces and all, with ``{menu}`` where the
menu goes. Nothing is changed outside the ``with`` block it is applied in.
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

from app import personas, skills, transcript
from app.tools import base

_OVERRIDE = re.compile(r"^@(persona|tool|skill)\s+([\w-]+):[ \t]?(.*)$")
_MENU = "{menu}"


@dataclass(frozen=True)
class Wording:
    path: str
    # The loop's rules as a format template, or None to keep them.
    rules: str | None = None
    personas: dict[str, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    skills: dict[str, str] = field(default_factory=dict)
    digest: str = ""

    def describe(self) -> dict:
        """What a report says of it: which file, and which version of it."""
        return {"file": self.path, "sha256": self.digest}


def load(path: str | Path) -> Wording:
    """Read a wording file. Raises ``ValueError`` saying what is wrong with
    one that could not be used."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    found: dict[str, dict[str, str]] = {"persona": {}, "tool": {}, "skill": {}}
    body: list[str] = []
    for line in raw.splitlines():
        match = _OVERRIDE.match(line)
        if match:
            kind, name, text = match.groups()
            found[kind][name] = text.strip()
        else:
            body.append(line)
    text = "\n".join(body).strip("\n")
    rules = None
    if text.strip():
        if text.count(_MENU) != 1:
            raise ValueError(f"{path}: the rules must hold {_MENU} exactly once")
        # Written as the model reads it: every brace is the model's except
        # the one that says where the menu goes.
        rules = text.replace("{", "{{").replace("}", "}}").replace("{{menu}}", _MENU)
        rules.format(menu="")  # a template that cannot be filled fails here, not mid-run
    return Wording(
        path=path.as_posix(),
        rules=rules,
        personas=found["persona"],
        tools=found["tool"],
        skills=found["skill"],
        digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12],
    )


@contextmanager
def applied(wording: Wording | None) -> Iterator[None]:
    """Run what is inside with ``wording`` in place of the texts it names,
    and put them back afterwards. ``None`` changes nothing."""
    if wording is None:
        yield
        return
    for kind, names, known in (
        ("persona", wording.personas, personas.PERSONAS),
        ("tool", wording.tools, base.REGISTRY),
        ("skill", wording.skills, skills.SKILLS),
    ):
        unknown = sorted(set(names) - set(known))
        if unknown:
            raise ValueError(f"{wording.path}: there is no {kind} {unknown[0]!r}")
    with ExitStack() as stack:
        if wording.rules is not None:
            stack.enter_context(mock.patch.object(transcript, "LOOP_RULES", wording.rules))
        stack.enter_context(mock.patch.dict(personas.PERSONAS, {
            name: dataclasses.replace(personas.PERSONAS[name], system=text)
            for name, text in wording.personas.items()
        }))
        stack.enter_context(mock.patch.dict(base.REGISTRY, {
            name: dataclasses.replace(base.REGISTRY[name], description=text)
            for name, text in wording.tools.items()
        }))
        stack.enter_context(mock.patch.dict(skills.SKILLS, {
            name: dataclasses.replace(skills.SKILLS[name], description=text)
            for name, text in wording.skills.items()
        }))
        yield
