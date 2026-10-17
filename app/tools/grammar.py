"""How the tools are offered to the model: the menu it reads, and the GBNF
grammar a choice is held to, with the most tokens a choice can take.

A choice is one JSON object: a tool and its arguments, a question for the
user, or ``{"tool": "none"}``. The grammar is what makes a reply that is not
one of those impossible to write, whatever size the model.
"""

from __future__ import annotations

import json

from .base import Arg, Choice, available


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

# A program, as the pass that writes one is held to it: one fenced Python
# block, ended where its fence closes. Inside it anything but three
# backticks in a row.
PROGRAM = (
    'root ::= "```python\\n" body "```"\n'
    'body ::= ([^`] | "`" [^`] | "``" [^`])*\n'
)
# The most tokens a program may take.
PROGRAM_TOKENS = 768


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
