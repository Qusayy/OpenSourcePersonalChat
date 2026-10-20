"""How the model calls a tool, in the format it was trained to call one in.

Qwen3.5 is told what it can call in its system text, as JSON schemas under a
fixed header, and calls a tool by writing the call into its reply::

    <tool_call>
    <function=weather>
    <parameter=location>
    Paris
    </parameter>
    </function>
    </tool_call>

What came of the call is given back as the next user turn, wrapped in
``<tool_response>``. A parameter's value is raw text up to its closing tag, so
code needs no escaping. The header and the instructions under it are the
model's own chat template's, word for word: the closer they are to what it
was trained on, the better it calls.

Nothing here decides anything. The model chooses when to call; this module
writes the menu, reads a call back out of a reply, and wraps a result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..tools.base import Tool

CALL_OPEN = "<tool_call>"
CALL_CLOSE = "</tool_call>"

TOOLS_HEADER = "# Tools\n\nYou have access to the following functions:\n\n<tools>"
TOOLS_FOOTER = "\n</tools>"
CALL_RULES = (
    "\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:\n\n"
    "<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n"
    "</parameter>\n<parameter=example_parameter_2>\nThis is the value for the second parameter\n"
    "that can span\nmultiple lines\n</parameter>\n</function>\n</tool_call>\n\n<IMPORTANT>\n"
    "Reminder:\n- Function calls MUST follow the specified format: an inner "
    "<function=...></function> block must be nested within <tool_call></tool_call> XML tags\n"
    "- Required parameters MUST be specified\n- You may provide optional reasoning for your "
    "function call in natural language BEFORE the function call, but NOT after\n- If there is "
    "no function call available, answer the question like normal with your current knowledge "
    "and do not tell the user about function calls\n</IMPORTANT>"
)

_TYPES = {"string": "string", "number": "number"}
# The most values a parameter's list of them is shown with. A longer one (every
# spelling of every unit, every currency code) costs hundreds of tokens of
# every prompt and tells the model nothing its description does not; what it
# is given is still checked against the list.
SHOWN_CHOICES = 12


def schema(tool: Tool) -> dict:
    """One tool as the model is shown it: a function and its parameters."""
    properties: dict[str, dict] = {}
    for arg in tool.args:
        prop: dict = {"type": _TYPES.get(arg.type, "string")}
        if arg.description:
            prop["description"] = arg.description
        if arg.choices and len(arg.choices) <= SHOWN_CHOICES:
            prop["enum"] = list(arg.choices)
        properties[arg.name] = prop
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": [a.name for a in tool.args if a.required],
            },
        },
    }


def tools_block(tools: list[Tool]) -> str:
    """The menu at the top of the system text, as the chat template writes
    it: the same characters every time the same tools are on offer."""
    listed = "".join("\n" + json.dumps(schema(t), ensure_ascii=False) for t in tools)
    return TOOLS_HEADER + listed + TOOLS_FOOTER + CALL_RULES


# --------------------------------------------------------------- a call ---

_FUNCTION = re.compile(r"<function=([^>\n]+)>")
_PARAMETER = re.compile(r"<parameter=([^>\n]+)>")
PARAM_CLOSE = "</parameter>"


def _params(rest: str) -> dict[str, str]:
    """The parameters of a call, each the raw text between its tags, less
    the one line break the format puts on either side of it. A parameter
    left unclosed runs to the next one, or to the end."""
    args: dict[str, str] = {}
    pos = 0
    while True:
        opened = _PARAMETER.search(rest, pos)
        if not opened:
            return args
        start = opened.end() + (1 if rest.startswith("\n", opened.end()) else 0)
        closed = rest.find(PARAM_CLOSE, start)
        following = _PARAMETER.search(rest, start)
        if closed < 0 or (following and following.start() < closed):
            end = following.start() if following else len(rest)
            pos = end
        else:
            end = closed
            pos = closed + len(PARAM_CLOSE)
        value = rest[start:end]
        if value.endswith("\n"):
            value = value[:-1]
        args[opened.group(1).strip()] = value


@dataclass
class Call:
    """A call as the model wrote it: the tool and what it gave it, or what
    was wrong with it."""

    name: str = ""
    args: dict = field(default_factory=dict)
    error: str | None = None


def parse_call(text: str) -> Call:
    """Read a call out of what the model wrote inside ``<tool_call>``.

    Read leniently: a missing closing tag is forgiven where what it closes is
    plain. What cannot be read at all is an error, said in one sentence the
    model can act on.
    """
    body = text.split(CALL_OPEN, 1)[-1].split(CALL_CLOSE, 1)[0]
    found = _FUNCTION.search(body)
    if not found:
        return Call(error="The tool call did not name a function, so no tool was called.")
    rest = body[found.end():]
    # The function closes after its last parameter. Code inside one may say
    # "</function>" itself, so only a closing tag after the last parameter's
    # is taken as the end.
    last = rest.rfind(PARAM_CLOSE)
    tail = rest.find("</function>", last if last >= 0 else 0)
    return Call(name=found.group(1).strip(), args=_params(rest if tail < 0 else rest[:tail]))


def render_call(name: str, args: dict) -> str:
    """A call written out as the model writes one: for a call the user made
    themselves, put into the conversation as if the model had."""
    params = "".join(
        f"<parameter={key}>\n{value if isinstance(value, str) else json.dumps(value)}\n</parameter>\n"
        for key, value in args.items()
    )
    return f"{CALL_OPEN}\n<function={name}>\n{params}</function>\n{CALL_CLOSE}"


# ---------------------------------------------------------- a response ---

# Anything in a tool's output that could pass for a marker of ours: the
# call and response tags, the think tags, and the chat format's control
# tokens. A page must not be able to close its own wrapper and speak in a
# voice the model takes for the harness's, or open a call of its own.
_MARKERS = re.compile(
    r"<\s*/?\s*(?:tool_call|tool_response|think)\s*>"
    r"|<\s*/?\s*(?:function|parameter)\s*(?:=[^<>\n]{0,80})?>"
    r"|<\|[^|>\n]{0,40}\|>",
    re.I,
)


# The brackets of the chat format's control tokens. Split by a zero-width
# space, the text still reads the same and can no longer be one.
_BRACKETS = re.compile(r"<\||\|>")
_BREAK = "​"


def clean(text: str) -> str:
    """A tool's output with every look-alike of our markers taken out.

    Taken out until none is left: a page can nest one inside another
    (``<|im_<|x|>end|>``), so that removing the inner one leaves the outer
    whole. Then whatever could still begin or end a control token is broken
    apart, so that nothing a page wrote can be read as one.
    """
    out = text or ""
    while True:
        stripped = _MARKERS.sub("", out)
        if stripped == out:
            break
        out = stripped
    return _BRACKETS.sub(lambda m: m.group(0)[0] + _BREAK + m.group(0)[1], out).strip()


def response(text: str) -> str:
    """What came of a call, as the model reads it in the next turn."""
    return f"<tool_response>\n{clean(text)}\n</tool_response>"


# ----------------------------------------------------- reading a stream ---


class Splitter:
    """A reply as it streams, split into what is said to the reader and the
    call it may end in.

    Text before ``<tool_call>`` is said as it comes, but for a tail that may
    be the start of the tag, held until it is known. Everything from the tag
    on is the call, kept and never shown.
    """

    def __init__(self) -> None:
        self.said = ""
        self.call = ""
        self.calling = False
        self._held = ""

    def feed(self, text: str) -> str:
        """What of ``text`` is said now."""
        if self.calling:
            self.call += text
            return ""
        buf = self._held + text
        at = buf.find(CALL_OPEN)
        if at >= 0:
            self.calling = True
            self._held = ""
            self.call = buf[at:]
            out = buf[:at]
            self.said += out
            return out
        keep = 0
        for n in range(min(len(CALL_OPEN) - 1, len(buf)), 0, -1):
            if buf.endswith(CALL_OPEN[:n]):
                keep = n
                break
        self._held = buf[len(buf) - keep:] if keep else ""
        out = buf[:len(buf) - keep]
        self.said += out
        return out

    def flush(self) -> str:
        """What was held back, once the reply has ended."""
        out, self._held = self._held, ""
        if self.calling:
            return ""
        self.said += out
        return out


def writing(call_text: str) -> tuple[str | None, str | None]:
    """The function a call being written names, and the path it gives, as
    far as either has been written yet."""
    name = _FUNCTION.search(call_text)
    args = _params(call_text[name.end():]) if name else {}
    path = args.get("path") if "path" in args and PARAM_CLOSE in call_text.split("<parameter=path>", 1)[-1] else None
    return (name.group(1).strip() if name else None), (path.strip() if path else None)
