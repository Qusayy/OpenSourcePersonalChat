"""How an answer is written, chosen from the message itself.

There is one agent and one system text. What changes from one message to the
next is the shape of the answer: code first for a programming question, a
list for ideas, a few sentences for a quick fact, an explanation otherwise.
That is read from the message with patterns, which cost nothing, rather than
asked of the model, which would cost a pass.

A style is the sampling for the answer, the room it is given, and one line
the model reads last, just before it writes. The line goes in the answer cue
and never in the system text, so every conversation begins with the same
characters and the model server's cache of them holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    id: str
    # The one line the model reads last about the shape of its answer.
    cue: str
    temperature: float
    max_tokens: int
    top_p: float = 0.9
    top_k: int = 40
    repeat_penalty: float = 1.05

    def sampling(self, *, looked_up: bool = False) -> dict:
        """The answer pass's sampling. An answer written from what a tool
        returned is held cooler, whatever its style: it is there to state the
        data, not to improvise on it."""
        return {
            "temperature": min(self.temperature, LOOKED_UP_TEMPERATURE) if looked_up else self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repeat_penalty": self.repeat_penalty,
            "max_tokens": self.max_tokens,
        }


# The warmest an answer written from a lookup is sampled.
LOOKED_UP_TEMPERATURE = 0.35

STYLES: dict[str, Style] = {
    s.id: s
    for s in (
        Style(
            id="explain",
            cue="Explain clearly, building from what the reader already knows, with one concrete example.",
            temperature=0.6,
            max_tokens=512,
        ),
        Style(
            id="concise",
            cue="Answer directly in at most four sentences, with no preamble.",
            temperature=0.4,
            max_tokens=256,
            repeat_penalty=1.08,
        ),
        Style(
            id="code",
            cue=(
                "Lead with a correct, runnable code block in a fenced markdown block with its "
                "language tag. Explain only what the code does not."
            ),
            temperature=0.2,
            max_tokens=768,
            repeat_penalty=1.0,
        ),
        Style(
            id="project",
            cue=(
                "Write it as a project of files. Before each file put a line with its path, "
                "like ### app/main.py, then the whole file in one fenced block with its "
                "language. Give every file it needs, then one short paragraph on how to run it."
            ),
            temperature=0.2,
            max_tokens=1536,
            repeat_penalty=1.0,
        ),
        Style(
            id="list",
            cue="Reply with a numbered list of varied, specific items. Favour range over polish.",
            temperature=0.9,
            max_tokens=448,
            top_p=0.95,
            top_k=60,
            repeat_penalty=1.1,
        ),
    )
}
DEFAULT_STYLE = "explain"

# Names that are languages and are seldom anything else. Go, R and Swift are
# left out: "how to go", "listen to Swift".
_LANGUAGES = (
    r"python|javascript|typescript|java|kotlin|c\+\+|c#|golang|rust|ruby|php|perl|"
    r"bash|powershell|sql|html|css|react|node(?:\.js)?|lua|haskell|scala"
)
# Code in the message, or a stack trace.
_SHOWN = re.compile(r"```|\bTraceback \(most recent call last\)|\b\w+Error:|\bdef \w+\(|\bfunction \w+\(")
# A language named as what something is to be done in.
_IN_LANGUAGE = re.compile(rf"\b(?:in|using|with|into)\s+(?:{_LANGUAGES})\b", re.I)
# Something to be written, mended or explained, and a thing made of code.
_VERB = re.compile(
    r"\b(?:write|implement|code|build|create|make|fix|debug|refactor|convert|port|generate|"
    r"optimi[sz]e)\b", re.I,
)
_CODE_THING = re.compile(
    rf"\b(?:code|function|script|program|snippet|regex(?:p)?|query|class|method|module|api|"
    rf"endpoint|bug|unit tests?|algorithm|cli|app|web ?app|website|web ?page|bot|"
    rf"flask|django|fastapi|express|vue|svelte|numpy|pandas|{_LANGUAGES})\b", re.I,
)
# Something made of several files: an app, a site, a game, a service.
_PROJECT = re.compile(
    r"\b(?:build|create|make|scaffold|generate|write|start|set up|code)\b(?:\s+[\w'-]+){0,6}?\s+"
    r"(?:project|app|application|website|web ?site|site|game|bot|api|server|backend|frontend|"
    r"cli|extension|plugin|dashboard|library|package|service)s?\b",
    re.I,
)
# A change to something already made.
_CHANGE = re.compile(
    r"\b(?:add|change|update|fix|rename|remove|delete|refactor|edit|replace|move|split|"
    r"make (?:it|the)|now|also|instead)\b",
    re.I,
)
_LIST = re.compile(
    r"\b(?:ideas?|brainstorm\w*|suggest(?:ions?)?|names? for|ways to|tips|"
    r"list (?:of|some)|options for|alternatives to)\b"
    r"|\b\d{1,2}\s+(?:ideas|ways|names|tips|reasons|examples|things|options|alternatives)\b",
    re.I,
)
_BRIEF = re.compile(r"\b(?:briefly|in short|tl;?dr|in one (?:sentence|line)|quick(?:ly)?|short answer)\b", re.I)
_QUICK_FACT = re.compile(
    r"^(?:who|what|when|where|which|whose|is|are|was|were|does|do|did|can|how (?:many|much|old|far|long|tall))\b",
    re.I,
)
_OPEN = re.compile(r"\b(?:why|explain|how (?:does|do|is|are|can|would|should)|what if|teach)\b", re.I)


def style_for(message: str, *, project: bool = False) -> Style:
    """The style a message asks for, by what it says.

    First, a project: something made of several files to build, or, in a
    conversation that has a project already (``project``), code or a change
    asked for. Then, in order: code (code shown, a language named, or code to
    write or mend);
    a list (ideas, suggestions, "five ways to"); a quick answer (asked
    briefly, a word or three such as a greeting, or a short who/what/when
    that is not a why); and otherwise an explanation.
    """
    text = " ".join((message or "").split())
    if _PROJECT.search(text):
        return STYLES["project"]
    coded = bool(_SHOWN.search(message or "") or _IN_LANGUAGE.search(text) or (
        _VERB.search(text) and _CODE_THING.search(text)
    ))
    if project and (coded or _CHANGE.search(text)):
        return STYLES["project"]
    if _SHOWN.search(message or "") or _IN_LANGUAGE.search(text) or (
        _VERB.search(text) and _CODE_THING.search(text)
    ):
        return STYLES["code"]
    if _LIST.search(text):
        return STYLES["list"]
    words = len(text.split())
    if _BRIEF.search(text) or words <= 3 or (
        words <= 12 and _QUICK_FACT.match(text) and not _OPEN.search(text)
    ):
        return STYLES["concise"]
    return STYLES[DEFAULT_STYLE]
