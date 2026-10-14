"""Skills — one-click workflows built on top of the tools.

A skill is data, not a code path: what it needs from the user's message (its
inputs), the lookups it makes with them, and the prompt that turns what they
found into an answer. Adding one is an entry in SKILLS, not a new branch in
the request handler.

Its inputs are read from the message by the skill's own pattern where it has
one, and otherwise the whole message is the one input it is given. A lookup is
made when every input it takes was found.

Every skill ends in exactly one model step, which is what lets the normal
streaming path do the talking — the tools run first, the answer streams last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .context import Part
from .router import URL_RE, ToolCall
from .tools.base import Arg, Choice


@dataclass(frozen=True)
class Lookup:
    """One lookup a skill makes: a tool, and which of the skill's inputs each
    of the tool's arguments is given."""

    tool: str
    # (the tool's argument, the skill's input), in the tool's order.
    given: tuple[tuple[str, str], ...]

    def call(self, values: dict[str, str]) -> ToolCall | None:
        """The call, if every input it takes has a value."""
        if any(not values.get(source) for _, source in self.given):
            return None
        return ToolCall(self.tool, {arg: values[source] for arg, source in self.given}, via="skill")


def look(tool: str, **given: str) -> Lookup:
    """``look("wikipedia", query="a")``: the tool, then each argument and the
    input it is given."""
    return Lookup(tool, tuple(given.items()))


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    glyph: str
    blurb: str
    prompt: str                        # {input} and {results} are substituted
    # What it needs from the message, and the lookups it makes with that.
    inputs: tuple[Arg, ...] = ()
    steps: tuple[Lookup, ...] = ()
    # Reads the inputs out of the message: whichever it finds.
    find: Callable[[str], dict[str, str]] | None = None
    # The input the whole message is, when nothing was found for it.
    whole: str | None = None
    # Said to the model, for a skill it may choose itself; None for one only
    # the visitor can.
    cue: str | None = None
    # What it does, in a line, for a reader who has not seen the button.
    description: str = ""
    persona: str = "agent"
    placeholder: str = ""

    @property
    def pipeline(self) -> tuple[str, ...]:
        """Tool names in the order the skill runs them, then the model."""
        return (*(step.tool for step in self.steps), "model")

    def read(self, text: str) -> dict[str, str]:
        """The inputs this message gives: what the pattern found in it, and
        the whole of it for the input that takes it when nothing else did."""
        text = (text or "").strip()
        values = {k: v for k, v in (self.find(text) if self.find else {}).items() if v}
        if self.whole and text and not values:
            values[self.whole] = text
        return values

    def missing(self, values: dict[str, str]) -> list[Arg]:
        """The inputs it must have that ``values`` does not give."""
        return [arg for arg in self.inputs if arg.required and not values.get(arg.name)]

    def calls(self, values: dict[str, str]) -> list[ToolCall]:
        """The lookups ``values`` are enough for, in order."""
        return [call for step in self.steps if (call := step.call(values)) is not None]

    def prepare(self, text: str) -> list[ToolCall]:
        return self.calls(self.read(text))

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "glyph": self.glyph,
            "blurb": self.blurb, "pipeline": list(self.pipeline),
            "persona": self.persona, "placeholder": self.placeholder,
        }


def _link(text: str) -> dict[str, str]:
    match = URL_RE.search(text)
    return {"url": match.group(0)} if match else {}


# Two things to compare, as people ask for that. Tried in order, the most
# particular first, and the first that matches is taken.
_END = r"\s*[?.!]*\s*$"
_PAIRS = tuple(re.compile(p, re.I) for p in (
    r"^(?:what(?:'s| is| are)\s+)?(?:the\s+)?differences?\s+between\s+(.+?)\s+and\s+(.+?)" + _END,
    r"^how\s+(?:does|do|is|are)\s+(.+?)\s+(?:differ|different)\s+from\s+(.+?)" + _END,
    r"^compare\s+(.+?)\s+(?:with|to|and|against|vs\.?|versus)\s+(.+?)" + _END,
    r"^(.+?),?\s+compared\s+(?:with|to)\s+(.+?)" + _END,
    r"^(.+?)\s+(?:vs\.?|versus|v\.)\s+(.+?)" + _END,
    r"^[^,]*,\s*(.+?)\s+or\s+(.+?)" + _END,
    r"^(.+?)\s+(?:and|or)\s+(.+?)" + _END,
))


def _pair(text: str) -> dict[str, str]:
    for pattern in _PAIRS:
        match = pattern.match(text)
        if match:
            a, b = (part.strip(" \t\"'“”‘’,") for part in match.groups())
            if a and b:
                return {"a": a, "b": b}
    return {}


SKILLS: dict[str, Skill] = {
    s.id: s
    for s in (
        Skill(
            id="summarise_link",
            name="Summarise a link",
            glyph="⧉",
            blurb="Read a page and boil it down",
            prompt=(
                "Summarise the page below in at most five short bullet points, "
                "then one closing sentence on why it matters.\n\n{results}"
            ),
            inputs=(Arg("url", "string", True, "the link to read", max_len=200, quoted=True),),
            steps=(look("read_url", url="url"),),
            find=_link,
            description="Read a web page and sum it up in a few points.",
            placeholder="Paste a URL…",
        ),
        Skill(
            id="fact_check",
            name="Fact-check",
            glyph="⚖",
            blurb="Weigh a claim against Wikipedia",
            prompt=(
                "The user's claim: “{input}”.\n\nReference material:\n{results}\n\n"
                "State whether the claim is supported, contradicted or unclear, "
                "in one short paragraph. Quote the reference where it decides the "
                "matter. If the reference does not cover it, say so plainly."
            ),
            inputs=(Arg("claim", "string", True, "the claim to check", max_len=200),),
            steps=(look("wikipedia", query="claim"),),
            whole="claim",
            cue=(
                "Say whether the claim is supported, contradicted or unclear by the data "
                "above, in one short paragraph, quoting it where it decides the matter. "
                "If it does not cover the claim, say so plainly."
            ),
            description="Check whether a claim is true against Wikipedia.",
            placeholder="Type a claim to check…",
        ),
        Skill(
            id="compare",
            name="Compare two things",
            glyph="⇹",
            blurb="Side-by-side from sources",
            prompt=(
                "Compare the two subjects using only the material below. Reply "
                "with a markdown table of 3-5 rows, then one sentence naming the "
                "clearest difference.\n\n{results}"
            ),
            inputs=(
                Arg("a", "string", True, "the first thing to compare", max_len=80),
                Arg("b", "string", True, "the second thing to compare", max_len=80),
            ),
            steps=(look("wikipedia", query="a"), look("wikipedia", query="b")),
            find=_pair,
            whole="a",
            cue=(
                "Compare the two using only the data above. Reply with a markdown table "
                "of 3-5 rows, then one sentence naming the clearest difference."
            ),
            description="Compare two things side by side, from Wikipedia.",
            placeholder="e.g. Python vs Rust",
        ),
        Skill(
            id="weather_brief",
            name="Weather brief",
            glyph="☁",
            blurb="Forecast, in plain words",
            prompt=(
                "Write a two-sentence brief for someone heading out today, using "
                "only these readings. Mention what to wear.\n\n{results}"
            ),
            inputs=(Arg("location", "string", True, "a town or city", max_len=80),),
            steps=(look("weather", location="location"),),
            whole="location",
            description="Today's weather for a place, and what to wear.",
            placeholder="Which town or city?",
        ),
        Skill(
            id="do_maths",
            name="Do the maths",
            glyph="⚡",
            blurb="Exact answer, then the working",
            prompt=(
                "The exact result is below. Restate it, then explain the working "
                "in two or three short steps. Do not recalculate — the figure "
                "given is correct.\n\n{results}"
            ),
            inputs=(Arg("expression", "string", True, "the sum to work out", max_len=200),),
            steps=(look("calculator", expression="expression"),),
            whole="expression",
            description="Work a sum out exactly, then show the working.",
            placeholder="e.g. (1247 * 89) / 12",
        ),
        Skill(
            id="explain_code",
            name="Explain this code",
            glyph="◇",
            blurb="Line by line, no tools",
            prompt=(
                "Explain what this code does, then name one thing that could go "
                "wrong with it.\n\n{input}"
            ),
            description="Explain what a piece of code does.",
            persona="code",
            placeholder="Paste some code…",
        ),
    )
}


@dataclass(frozen=True)
class Offer:
    """A skill as the model is offered it, beside the tools and in their
    shape: a name to choose it by, the inputs it takes, what it is for."""

    skill: Skill

    @property
    def name(self) -> str:
        return self.skill.id

    @property
    def args(self) -> tuple[Arg, ...]:
        return self.skill.inputs

    @property
    def description(self) -> str:
        return self.skill.description


def offered(tools: list) -> list[Choice]:
    """What the model may choose from: the tools, then each skill that is
    offered to it and whose lookups the tools on offer can make."""
    names = {t.name for t in tools}
    skills = [
        Offer(s) for s in SKILLS.values()
        if s.cue and s.steps and all(step.tool in names for step in s.steps)
    ]
    return [*tools, *skills]


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
