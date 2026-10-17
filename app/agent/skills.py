"""Skills: several lookups the agent may choose as one, beside the tools.

A skill is data, not a code path: the inputs it takes, the lookups it makes
with them, and how the answer is asked for once they are read (its ``cue``).
It is offered on the menu in the shape of a tool, so the model chooses it the
way it chooses a tool, and its inputs are checked the way a call's arguments
are. Adding one is an entry in SKILLS, not a new branch in the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tools.base import Arg, Choice, Rejection, check_args, was_given
from .route import ToolCall


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
    # What it needs, and the lookups it makes with that.
    inputs: tuple[Arg, ...]
    steps: tuple[Lookup, ...]
    # How the answer is asked for once its lookups are read.
    cue: str
    # What it is for, as the model reads it on the menu.
    description: str

    def missing(self, values: dict[str, str]) -> list[Arg]:
        """The inputs it must have that ``values`` does not give."""
        return [arg for arg in self.inputs if arg.required and not values.get(arg.name)]

    def calls(self, values: dict[str, str]) -> list[ToolCall]:
        """The lookups ``values`` are enough for, in order."""
        return [call for step in self.steps if (call := step.call(values)) is not None]


SKILLS: dict[str, Skill] = {
    s.id: s
    for s in (
        Skill(
            id="compare",
            name="Compare two things",
            glyph="⇹",
            inputs=(
                Arg("a", "string", True, "the first thing to compare", max_len=80),
                Arg("b", "string", True, "the second thing to compare", max_len=80),
            ),
            steps=(look("wikipedia", query="a"), look("wikipedia", query="b")),
            cue=(
                "Compare the two using only the data above. Reply with a markdown table "
                "of 3-5 rows, then one sentence naming the clearest difference."
            ),
            description="Compare two things side by side, from Wikipedia.",
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
    """What the model may choose from: the tools, then each skill whose
    lookups the tools on offer can make."""
    names = {t.name for t in tools}
    skills = [
        Offer(s) for s in SKILLS.values() if all(step.tool in names for step in s.steps)
    ]
    return [*tools, *skills]


def check_inputs(
    skill: Skill, given: dict, theirs: list[str]
) -> tuple[dict[str, str], Rejection | None]:
    """A skill's inputs as the model gave them, checked as a call's arguments
    are: the inputs it may run with, and why it cannot, if it cannot. Text
    with nothing in it is not an input given, and an address must be one the
    user wrote (``theirs``)."""
    clean, rejection = check_args(skill.name, skill.inputs, given)
    reasons = list(rejection.reasons) if rejection else []
    reasons += [
        f"{arg.name} is required and was not given"
        for arg in skill.missing({k: str(v).strip() for k, v in clean.items()})
        if arg.name in clean
    ]
    reasons += [
        f"{arg.name} must be an address the user gave; got {clean[arg.name]!r}"
        for arg in skill.inputs
        if arg.quoted and clean.get(arg.name) and not was_given(clean[arg.name], theirs)
    ]
    clean = {name: str(value) for name, value in clean.items()}
    if not reasons:
        return clean, None
    said = rejection.summary if rejection else f"{skill.name} was not given what it needs."
    return clean, Rejection(tuple(reasons), said, "bad input")


# What the model is asked to do about a lookup that failed. Without it, it
# fills the gap from memory and states that as though a tool had returned it.
SAY_SO = "Say plainly what could not be looked up, and do not guess at it."


def did_not_work(failures: list[str]) -> str:
    """Failed lookups as a list the model can read: one fact to a line."""
    return "\n".join(f"- {f}" for f in failures)
