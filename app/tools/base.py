"""Tool registry, argument specs, GBNF generation, dispatch.

Two things a tool returns, and the difference matters:

* ``data``    — the structured payload a front-end card renders from. Rich.
* ``summary`` — one plain line handed back to the model. Deliberately terse,
  because a 1.5B model given a page of JSON will start reciting it instead of
  answering.

What the model reads of a result is its ``observation``: never more than the
tool's ``budget`` in tokens, and for a tool that brings back pages of text,
the part of it that bears on the question.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Protocol

from ..config import settings
from ..text import clip


@dataclass(frozen=True)
class Arg:
    name: str
    type: str = "string"  # string | number
    required: bool = True
    description: str = ""
    default: Any = None
    # The spellings this argument may be given. They are written into the
    # grammar, so a model choosing a tool cannot say anything else, and every
    # other caller is held to them when the call is checked.
    choices: tuple[str, ...] | None = None
    # Turns a spelling into the value the tool is run with: "miles" into "mi",
    # "usd" into "USD".
    canon: Callable[[str], str] | None = None
    # The most characters the grammar lets a model write here. None takes the
    # default ceiling. It bounds what a model can write, not what a person can
    # type: a tool keeps its own limits for that.
    max_len: int | None = None
    # The value is an address this server will go out to. A model may pass one
    # on; it may not make one up or take one from something it has read. So a
    # call the model chose is only made if the value is in the user's own
    # words. See `was_given`.
    quoted: bool = False


@dataclass
class ToolResult:
    ok: bool
    summary: str
    card: str | None = None
    data: dict = field(default_factory=dict)
    ms: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "card": self.card,
            "data": self.data,
            "ms": round(self.ms, 1),
            "error": self.error,
        }


@dataclass(frozen=True)
class CallContext:
    """Whom a call is made for. Set by whoever runs the message, inside the
    call's own task, so it reaches the tool and goes no further. Nothing in
    it is an argument the model can give."""

    # The conversation the message belongs to; None outside one.
    conversation_id: str | None = None


CALL: ContextVar[CallContext] = ContextVar("tool_call", default=CallContext())


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    glyph: str
    description: str
    args: tuple[Arg, ...]
    run: Callable[..., Awaitable[ToolResult]]
    card: str
    network: bool = False
    example: str = ""
    # The most tokens of a result the model is given to read, counted as a
    # byte in three. A one-line result is well inside it.
    budget: int = 80
    # How a result is read for a purpose within a budget, for a tool whose
    # results are longer than a line. Without it, the summary is read.
    observe: Callable[[ToolResult, str, int], str] | None = None
    # How long a result stays true, in seconds. Replayed later than that in a
    # conversation, it is replaced by a line saying it is out of date. None
    # for a result that does not go out of date.
    fresh_for: float | None = None
    # Its results are text from the open web, written by anybody: never
    # quoted in the harness's own voice, and never a reason to act.
    untrusted: bool = False
    # It keeps or reads notes, and is there only while notes are on.
    notes: bool = False
    # It writes: a note. Only ever on the user's own word, never after the
    # run has read text somebody else wrote.
    writes: bool = False

    def required_args(self) -> tuple[Arg, ...]:
        return tuple(a for a in self.args if a.required)

    def observation(self, result: ToolResult, purpose: str = "", budget: int | None = None) -> str:
        """What the model reads of a successful ``result``: chosen for
        ``purpose``, the question it was looked up for, and never longer than
        ``budget`` or the tool's own budget, whichever is less."""
        room = self.budget if budget is None else max(0, min(budget, self.budget))
        said = self.observe(result, purpose, room) if self.observe is not None else result.summary
        return clip(said or "", room)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "glyph": self.glyph,
            "description": self.description,
            "card": self.card,
            "network": self.network,
            "example": self.example,
            "args": [
                {"name": a.name, "type": a.type, "required": a.required,
                 "description": a.description}
                for a in self.args
            ],
        }


class Choice(Protocol):
    """Anything the model may choose: a tool, or a skill offered beside the
    tools. The menu, the grammar and its ceiling are written from these three
    alone, so a skill is chosen exactly as a tool is."""

    name: str
    args: tuple[Arg, ...]
    description: str


# Argument names are internal; a person being told what is missing should get
# a noun phrase, not a parameter.
MISSING_HINT = {
    "expression": "an expression",
    "url": "a link",
    "location": "a place",
    "query": "something to look up",
    "text": "some text",
    "value": "a value",
    "from_unit": "a unit to convert from",
    "to_unit": "a unit to convert to",
    "amount": "an amount",
    "from_currency": "a currency to convert from",
    "to_currency": "a currency to convert to",
    "timezone_name": "a timezone",
}

REGISTRY: dict[str, Tool] = {}

# Outbound calls are capped globally. A public URL should not become somebody
# else's fetch proxy, and two concurrent sockets is plenty for one visitor.
_net_semaphore = asyncio.Semaphore(2)


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def fresh_for(name: str) -> float | None:
    """How long a result of the tool called ``name`` stays true; None for
    one that does not go out of date, or a tool there is no longer."""
    tool = REGISTRY.get(name)
    return tool.fresh_for if tool is not None else None


def notes_scope() -> str:
    """Whose notes there are: the conversation's, the site's, or none."""
    scope = getattr(settings, "notes", "conversation")
    return scope if scope in ("site", "off") else "conversation"


def enabled(tool: Tool) -> bool:
    """Whether the configuration lets this tool run at all."""
    if tool.network and not settings.allow_outbound:
        return False
    return not (tool.notes and notes_scope() == "off")


def untrusted(tool: Tool) -> bool:
    """Whether what this tool returns was written by somebody other than the
    user: a page, an article, or, where every conversation shares them,
    notes."""
    return tool.untrusted or (tool.notes and not tool.writes and notes_scope() == "site")


def available() -> list[Tool]:
    """Tools the current configuration actually permits."""
    return [t for t in REGISTRY.values() if enabled(t)]


# ---------------------------------------------------------------- checking ---


@dataclass(frozen=True)
class Rejection:
    """Why a call was not made.

    Said twice, because it has two readers. ``reasons`` are for a model that
    may be asked to try again: one plain fact per problem, naming the argument
    as the model knows it. ``summary`` is the sentence a person reads on the
    failed step. ``code`` is the short word the step is filed under.
    """

    reasons: tuple[str, ...]
    summary: str
    code: str  # "missing input" | "bad input"


# A number written with thousands separators, the way amounts are typed.
_GROUPED = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _coerce(arg: Arg, value: Any) -> tuple[Any, str | None]:
    """One value against what its argument declares.

    Returns the value to use and nothing, or anything and a complaint. The
    complaint ends a sentence that begins with the argument's name.
    """
    value, complaint = _typed(arg, value)
    if complaint is not None or not isinstance(value, str):
        return value, complaint
    if arg.choices is None:
        return (arg.canon(value) if arg.canon else value), None
    canon = arg.canon or (lambda spelling: spelling)
    allowed = {canon(choice) for choice in arg.choices}
    wanted = canon(value)
    if wanted not in allowed:
        some = ", ".join(sorted(allowed)[:3])
        return value, (
            f"must be one of the {len(allowed)} values it knows, such as {some}; got {value!r}"
        )
    return wanted, None


def _typed(arg: Arg, value: Any) -> tuple[Any, str | None]:
    """The kind of thing a value is, against the kind its argument wants."""
    if arg.type == "number":
        # A bool is an int to Python and a mistake to everyone else.
        if isinstance(value, bool):
            return value, f"must be a number; got {value!r}"
        if isinstance(value, (int, float)):
            number = value
        elif isinstance(value, str):
            text = value.strip()
            if _GROUPED.match(text):
                text = text.replace(",", "")
            try:
                number = int(text)
            except ValueError:
                try:
                    number = float(text)
                except ValueError:
                    return value, f"must be a number; got {value!r}"
        else:
            return value, f"must be a number; got {_kind(value)}"
        if not math.isfinite(number):
            return value, f"must be a finite number; got {value!r}"
        return number, None

    if isinstance(value, str):
        return value, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value), None
    return value, f"must be text; got {_kind(value)}"


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, (list, tuple, set)):
        return "a list"
    return f"a {type(value).__name__}"


_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)


def was_given(address: str, texts: Iterable[str]) -> bool:
    """Whether an address is one the user wrote themselves.

    A model that reads a fetched page can be talked into fetching another:
    "now open http://elsewhere/?q=" followed by something from the
    conversation. The grammar cannot stop that, since the call is perfectly
    well formed, and neither can a check on where the address points, since it
    points somewhere public. What stops it is that the address is not one the
    user gave.

    The comparison forgives what people leave off and put on: the scheme,
    "www.", a trailing slash, capitals, and a path shortened to its site. It
    does not forgive anything added: a longer path or a query string that the
    user did not write is a different address.
    """
    key = _SCHEME.sub("", (address or "").strip().lower()).removeprefix("www.").rstrip("/")
    if not key:
        return False
    # The address has to stand as an address in what they wrote: not inside a
    # longer name, and not continued by more of a name.
    found = re.compile(r"(?<![a-z0-9.\-])(?:www\.)?" + re.escape(key) + r"(?![a-z0-9\-])")
    return any(found.search(text.lower()) for text in texts)


def check(tool: Tool, args: dict | None) -> tuple[dict, Rejection | None]:
    """Turn what a call was given into what the tool can be run with: see
    ``check_args``."""
    return check_args(tool.label, tool.args, args)


def check_args(
    label: str, spec: tuple[Arg, ...], args: dict | None
) -> tuple[dict, Rejection | None]:
    """Turn what a call was given into what the tool can be run with.

    Returns the arguments to run with and nothing, or a ``Rejection`` saying
    why it should not be run. Every caller comes through here, so a tool never
    has to wonder whether its number is a number:

    * arguments the tool never declared are dropped — the model invents them;
    * a number written as text becomes a number, and a number where text is
      wanted becomes text;
    * anything else of the wrong kind is refused, by name;
    * an argument left out, or given as nothing, takes its default;
    * a required one still missing after that is refused.
    """
    declared = {a.name: a for a in spec}
    clean: dict[str, Any] = {}
    refused: set[str] = set()
    reasons: list[str] = []
    said: list[str] = []

    for name, value in (args or {}).items():
        arg = declared.get(name)
        if arg is None or value is None:
            continue
        value, complaint = _coerce(arg, value)
        if complaint is None:
            clean[name] = value
            continue
        refused.add(name)
        reasons.append(f"{name} {complaint}")
        hint = MISSING_HINT.get(name, name)
        if arg.type == "number":
            said.append(f"{label} could not use {value!r} as {hint}: it is not a number.")
        elif isinstance(value, str):
            said.append(f"{label} does not know {value!r} as {hint}.")
        else:
            said.append(f"{label} could not use that as {hint}: it is not text.")

    # A default stands in for an argument that was left out. It does not paper
    # over one that was given and refused.
    for arg in spec:
        if arg.name not in clean and arg.name not in refused and arg.default is not None:
            clean[arg.name] = arg.default

    missing = [
        a.name for a in spec if a.required and a.name not in clean and a.name not in refused
    ]
    if missing:
        reasons += [f"{name} is required and was not given" for name in missing]
        said.append(
            f"{label} needs {' and '.join(MISSING_HINT.get(m, m) for m in missing)}."
        )

    if not reasons:
        return clean, None
    code = "bad input" if refused else "missing input"
    return clean, Rejection(tuple(reasons), " ".join(said), code)


# --------------------------------------------------------------- dispatch ---


async def call_tool(name: str, args: dict) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(False, f"There is no tool called {name!r}.", error="unknown tool")
    if tool.network and not settings.allow_outbound:
        return ToolResult(
            False,
            "This server has outgoing requests switched off, so Wikipedia, "
            "weather, currency and page reading are unavailable.",
            error="offline mode",
        )
    if not enabled(tool):
        return ToolResult(False, "Notes are switched off on this server.", error="notes off")

    clean, rejection = check(tool, args)
    if rejection is not None:
        return ToolResult(False, rejection.summary, error=rejection.code)

    t0 = time.perf_counter()
    try:
        if tool.network:
            async with _net_semaphore:
                result = await asyncio.wait_for(
                    tool.run(**clean), timeout=settings.tool_timeout
                )
        else:
            result = await asyncio.wait_for(tool.run(**clean), timeout=settings.tool_timeout)
    except asyncio.TimeoutError:
        return ToolResult(
            False,
            f"{tool.label} gave up after {settings.tool_timeout} seconds. "
            "The source may be slow or unreachable — try again.",
            ms=(time.perf_counter() - t0) * 1000,
            error="timeout",
        )
    except Exception as exc:  # noqa: BLE001 — surfaced as a failed step in the UI
        return ToolResult(
            False,
            f"{tool.label} could not finish. Try again, or ask without it.",
            ms=(time.perf_counter() - t0) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )

    result.ms = (time.perf_counter() - t0) * 1000
    if result.card is None:
        result.card = tool.card
    return result


# ------------------------------------------------------------------ GBNF ---

_GBNF_PRIMITIVES = r"""
char ::= [^"\\\x7F\x00-\x1F] | "\\" ["\\bfnrt]
number ::= "-"? ("0" | [1-9] [0-9]{0,15}) ("." [0-9]{1,6})?
"""

# The most characters a model may write for a text argument that sets no
# ceiling of its own, and for the question it may ask.
DEFAULT_MAX_LEN = 120
# The longest a number can be under the `number` rule above, in bytes: a
# sign, sixteen digits, a point and six more.
_NUMBER_BYTES = 24
# The most bytes one `char` can take: a four-byte character in UTF-8. (An
# escape is two.)
_CHAR_BYTES = 4

NONE_ACTION = '{"tool": "none"}'


def _rule_name(tool_name: str) -> str:
    return "t-" + tool_name.replace("_", "-")


def _literal(text: str) -> str:
    """Text as a GBNF literal that matches exactly that text."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _spelt(choice: str) -> str:
    """One allowed value as the model has to write it: a JSON string."""
    return json.dumps(choice, ensure_ascii=False)


ASK = Arg("question", "string", True, "one short question for the user")


class _Builder:
    """The pieces a grammar of JSON arguments is made of: a rule for each
    ceiling on text and each set of allowed spellings, made once each."""

    def __init__(self) -> None:
        self.texts: dict[int, str] = {}                 # ceiling -> rule name
        self.values: dict[tuple[str, ...], str] = {}    # allowed spellings -> rule name

    def value_rule(self, arg: Arg) -> str:
        if arg.type != "string":
            return "number"
        if arg.choices is not None:
            if arg.choices not in self.values:
                self.values[arg.choices] = f"choice-{len(self.values) + 1}"
            return self.values[arg.choices]
        ceiling = arg.max_len or DEFAULT_MAX_LEN
        return self.texts.setdefault(ceiling, f"text-{ceiling}")

    def pair(self, arg: Arg, sep: str) -> str:
        return f'{_literal(f"""{sep}"{arg.name}": """)} {self.value_rule(arg)}'

    def fields(self, args: tuple[Arg, ...]) -> list[str]:
        """What goes between an object's braces: every required argument,
        and each optional one or not."""
        required = [a for a in args if a.required]
        optional = [a for a in args if not a.required]
        parts = [self.pair(a, "" if i == 0 else ", ") for i, a in enumerate(required)]
        if optional and required:
            # Each may follow what is already there, in declaration order.
            parts += [f"({self.pair(a, ', ')})?" for a in optional]
        elif optional:
            # With nothing required, any one of them may come first, and only
            # those declared after it may follow. Written out, so that a comma
            # can never open the list or end it.
            starts = []
            for i, first in enumerate(optional):
                rest = [f"({self.pair(a, ', ')})?" for a in optional[i + 1:]]
                starts.append(" ".join([self.pair(first, ""), *rest]))
            parts.append("(" + " | ".join(starts) + ")?")
        return parts

    def rules(self) -> list[str]:
        lines = [f"{rule} ::= " + " | ".join(_literal(_spelt(s)) for s in spellings)
                 for spellings, rule in self.values.items()]
        lines += [f'{rule} ::= "\\"" char{{1,{ceiling}}} "\\""'
                  for ceiling, rule in sorted(self.texts.items())]
        return lines


def tools_to_gbnf(tools: list[Choice] | None = None, *, ask: bool = True) -> str:
    """Build a grammar that can only produce a valid action.

    This is what makes routing viable on a 1.5B model. Asked politely for JSON
    it produces trailing commas, prose preambles and invented keys; constrained
    by this grammar, malformed output is not merely unlikely, it is
    unrepresentable.

    An action is a call to one tool, a question for the user (``ask``), or
    ``none``. Within a call:

    * a required argument is always there, an optional one may be;
    * text has a ceiling, so a reply always closes instead of running on until
      the token limit cuts it off mid-string;
    * an argument with a known set of values can only be given one of them.
      The model cannot invent a currency.
    """
    tools = tools if tools is not None else available()
    build = _Builder()
    lines: list[str] = []
    alts: list[str] = []

    def call(name: str, args: tuple[Arg, ...]) -> str:
        parts = [_literal(f'{{"tool": "{name}", "args": {{'), *build.fields(args), _literal("}}")]
        return " ".join(parts)

    for tool in tools:
        rule = _rule_name(tool.name)
        alts.append(rule)
        lines.append(f"{rule} ::= {call(tool.name, tool.args)}")

    if ask:
        alts.append("t-ask")
        lines.append(f"t-ask ::= {call('ask', (ASK,))}")
    alts.append("t-none")
    lines.append(f"t-none ::= {_literal(NONE_ACTION)}")

    root = "root ::= " + " | ".join(alts)
    return "\n".join([root, *lines, *build.rules(), _GBNF_PRIMITIVES])


def args_to_gbnf(args: tuple[Arg, ...]) -> str:
    """A grammar for one JSON object of ``args`` and nothing else: what a
    pass that fills them in may write."""
    build = _Builder()
    root = "root ::= " + " ".join([_literal("{"), *build.fields(args), _literal("}")])
    return "\n".join([root, *build.rules(), _GBNF_PRIMITIVES])


def _widest(arg: Arg) -> int:
    """The most bytes a value of ``arg`` can take as the grammar writes it."""
    if arg.type != "string":
        return _NUMBER_BYTES
    if arg.choices is not None:
        return max(len(_spelt(c).encode("utf-8")) for c in arg.choices)
    return 2 + _CHAR_BYTES * (arg.max_len or DEFAULT_MAX_LEN)


def _fields_size(args: tuple[Arg, ...]) -> int:
    """Every argument present, each as long as it can be, a comma and a
    space between each pair."""
    size = sum(len(f'"{a.name}": '.encode("utf-8")) + _widest(a) for a in args)
    return size + 2 * max(len(args) - 1, 0)


def args_ceiling(args: tuple[Arg, ...]) -> int:
    """The longest object ``args_to_gbnf`` allows, in bytes, and so enough
    tokens for a pass to finish it."""
    return len(b"{}") + _fields_size(args)


def decision_ceiling(tools: list[Choice] | None = None, *, ask: bool = True) -> int:
    """The longest reply the grammar allows, in bytes.

    A token is at least one byte, so a pass given this many tokens can always
    reach the end of its reply. A ceiling on characters is not enough by
    itself: 120 characters can be more than a hundred tokens, and a reply cut
    off mid-string is not JSON.
    """
    tools = tools if tools is not None else available()

    def longest(name: str, args: tuple[Arg, ...]) -> int:
        return len(f'{{"tool": "{name}", "args": {{}}}}'.encode("utf-8")) + _fields_size(args)

    sizes = [longest(t.name, t.args) for t in tools]
    if ask:
        sizes.append(longest("ask", (ASK,)))
    return max([len(NONE_ACTION), *sizes])


def tool_menu(tools: list[Choice] | None = None) -> str:
    """The compact description block shown to the model during routing.

    An argument that may be left out is marked with a question mark.
    """
    tools = tools if tools is not None else available()
    rows = []
    for t in tools:
        arg_names = ", ".join(a.name if a.required else f"{a.name}?" for a in t.args) or "none"
        rows.append(f"- {t.name}({arg_names}): {t.description}")
    return "\n".join(rows)
