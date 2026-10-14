"""Skills — one-click workflows built on top of the tools.

A skill is data, not a code path: a way to turn the user's message into a list
of tool calls, plus the prompt that turns those results into an answer. Adding
one is an entry in SKILLS, not a new branch in the request handler.

Every skill ends in exactly one model step, which is what lets the normal
streaming path do the talking — the tools run first, the answer streams last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .context import Part
from .router import URL_RE, ToolCall

SPLIT_RE = re.compile(r"\s+(?:vs\.?|versus|and|or)\s+", re.I)


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    glyph: str
    blurb: str
    pipeline: tuple[str, ...]          # tool names, for the UI's little graph
    prepare: Callable[[str], list[ToolCall]]
    prompt: str                        # {input} and {results} are substituted
    persona: str = "agent"
    placeholder: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "glyph": self.glyph,
            "blurb": self.blurb, "pipeline": list(self.pipeline),
            "persona": self.persona, "placeholder": self.placeholder,
        }


def _link(text: str) -> list[ToolCall]:
    match = URL_RE.search(text or "")
    if not match:
        return []
    return [ToolCall("read_url", {"url": match.group(0)}, via="skill")]


def _wiki(text: str) -> list[ToolCall]:
    query = (text or "").strip()
    return [ToolCall("wikipedia", {"query": query}, via="skill")] if query else []


def _wiki_pair(text: str) -> list[ToolCall]:
    parts = [p.strip() for p in SPLIT_RE.split(text or "", maxsplit=1) if p.strip()]
    if len(parts) < 2:
        return _wiki(text)
    return [ToolCall("wikipedia", {"query": p}, via="skill") for p in parts[:2]]


def _weather(text: str) -> list[ToolCall]:
    place = (text or "").strip()
    return [ToolCall("weather", {"location": place}, via="skill")] if place else []


def _maths(text: str) -> list[ToolCall]:
    body = (text or "").strip()
    return [ToolCall("calculator", {"expression": body}, via="skill")] if body else []


def _none(text: str) -> list[ToolCall]:
    return []


SKILLS: dict[str, Skill] = {
    s.id: s
    for s in (
        Skill(
            id="summarise_link",
            name="Summarise a link",
            glyph="⧉",
            blurb="Read a page and boil it down",
            pipeline=("read_url", "model"),
            prepare=_link,
            prompt=(
                "Summarise the page below in at most five short bullet points, "
                "then one closing sentence on why it matters.\n\n{results}"
            ),
            placeholder="Paste a URL…",
        ),
        Skill(
            id="fact_check",
            name="Fact-check",
            glyph="⚖",
            blurb="Weigh a claim against Wikipedia",
            pipeline=("wikipedia", "model"),
            prepare=_wiki,
            prompt=(
                "The user's claim: “{input}”.\n\nReference material:\n{results}\n\n"
                "State whether the claim is supported, contradicted or unclear, "
                "in one short paragraph. Quote the reference where it decides the "
                "matter. If the reference does not cover it, say so plainly."
            ),
            placeholder="Type a claim to check…",
        ),
        Skill(
            id="compare",
            name="Compare two things",
            glyph="⇹",
            blurb="Side-by-side from sources",
            pipeline=("wikipedia", "wikipedia", "model"),
            prepare=_wiki_pair,
            prompt=(
                "Compare the two subjects using only the material below. Reply "
                "with a markdown table of 3-5 rows, then one sentence naming the "
                "clearest difference.\n\n{results}"
            ),
            placeholder="e.g. Python vs Rust",
        ),
        Skill(
            id="weather_brief",
            name="Weather brief",
            glyph="☁",
            blurb="Forecast, in plain words",
            pipeline=("weather", "model"),
            prepare=_weather,
            prompt=(
                "Write a two-sentence brief for someone heading out today, using "
                "only these readings. Mention what to wear.\n\n{results}"
            ),
            placeholder="Which town or city?",
        ),
        Skill(
            id="do_maths",
            name="Do the maths",
            glyph="⚡",
            blurb="Exact answer, then the working",
            pipeline=("calculator", "model"),
            prepare=_maths,
            prompt=(
                "The exact result is below. Restate it, then explain the working "
                "in two or three short steps. Do not recalculate — the figure "
                "given is correct.\n\n{results}"
            ),
            placeholder="e.g. (1247 * 89) / 12",
        ),
        Skill(
            id="explain_code",
            name="Explain this code",
            glyph="◇",
            blurb="Line by line, no tools",
            pipeline=("model",),
            prepare=_none,
            prompt=(
                "Explain what this code does, then name one thing that could go "
                "wrong with it.\n\n{input}"
            ),
            persona="code",
            placeholder="Paste some code…",
        ),
    )
}


def get_skill(skill_id: str | None) -> Skill | None:
    return SKILLS.get(skill_id or "")


def skill_list() -> list[dict]:
    return [s.as_dict() for s in SKILLS.values()]


# What the model is asked to do about a lookup that failed. Without it, it
# fills the gap from memory and states that as though a tool had returned it.
SAY_SO = "Say plainly what could not be looked up, and do not guess at it."


def did_not_work(failures: list[str]) -> str:
    """Failed lookups as a list the model can read: one fact to a line."""
    return "\n".join(f"- {f}" for f in failures)


def build_prompt(
    skill: Skill, user_text: str, results: list[str], failures: list[str] | None = None
) -> str:
    """Fold the tool output into the skill's prompt.

    Results are joined as plain labelled text — a 1.5B model handed raw JSON
    starts reciting the JSON back instead of answering.

    A lookup that failed is put where its result would have been, as what
    happened, so the answer can say so. That is a different thing from a skill
    that found nothing to look up, which is told there is no output.
    """
    return "\n\n".join(p.content for p in prompt_parts(skill, user_text, results, failures))


def prompt_parts(
    skill: Skill,
    user_text: str,
    results: list[str],
    failures: list[str] | None = None,
    tools: tuple[str, ...] = (),
) -> list[Part]:
    """``build_prompt``'s text as parts of a prompt, each with its kind.

    The skill's prompt is cut where it has a blank line, and each piece is a
    part: the one that holds the results is what was found (by ``tools``),
    one that is only the user's text is the question, and the rest are
    instructions. Joined as a prompt joins parts, they are ``build_prompt``'s
    text to the letter.
    """
    parts = list(results)
    if failures:
        heading = "These lookups" if len(failures) > 1 else "This lookup"
        parts.append(f"{heading} did not work:\n{did_not_work(failures)}\n\n{SAY_SO}")
    joined = "\n\n".join(parts) if parts else "(no tool output available)"
    out = []
    for piece in skill.prompt.split("\n\n"):
        text = piece.format(input=user_text.strip(), results=joined)
        if "{results}" in piece:
            out.append(Part("user", "data", text, tools))
        elif piece.strip() == "{input}":
            out.append(Part("user", "question", text))
        else:
            out.append(Part("user", "instruction", text))
    return out
