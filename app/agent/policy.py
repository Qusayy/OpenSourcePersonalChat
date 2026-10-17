"""What a run may do with a call the model chose.

Three rules, each about where something came from rather than its form:

* an address the server goes out to must be one the user wrote;
* a tool that writes only acts on the user's own word, never once the run has
  read text somebody else wrote;
* a call already made is not made again.
"""

from __future__ import annotations

import json

from ..tools import REGISTRY
from ..tools.base import Rejection, check, was_given
from .route import ToolCall


def users_words(history: list[dict], message: str) -> list[str]:
    """Everything the user has written in the conversation, this message last:
    where an address must be found for the server to go to it."""
    return [m["content"] for m in history if m["role"] == "user"] + [message]


def refusal(call: ToolCall, *, theirs: list[str], tainted: bool) -> Rejection | None:
    """Why a call the model chose cannot be made as it stands, if it cannot.

    Two things can be wrong with it. The tool cannot take what it was
    given; or it was given an address to go out to that the user never
    wrote. The second is not a mistake of form, and the dispatcher would
    run it. It is refused here because of where the address came from:
    the only other place the model could have got one is something it
    read.
    """
    tool = REGISTRY[call.name]
    clean, rejection = check(tool, call.args)
    if rejection is not None:
        return rejection
    if tool.writes and tainted:
        # A page that says "remember that…" must not be able to.
        return Rejection(
            (f"{tool.name} only follows the user's own message, not text read in this "
             "conversation from elsewhere",),
            f"{tool.label} only keeps what you ask it to yourself.",
            "not allowed",
        )
    for arg in tool.args:
        value = clean.get(arg.name)
        if arg.quoted and value is not None and not was_given(value, theirs):
            return Rejection(
                (f"{arg.name} must be an address the user gave; got {value!r}",),
                f"{tool.label} only goes to an address you gave it yourself.",
                "not allowed",
            )
    return None


def call_key(call: ToolCall) -> str:
    """A call as it is compared with the ones already made: the tool, and
    its arguments as the tool would be run with them, without regard to
    case or spacing. "Paris" and " paris" are one lookup."""
    clean, _ = check(REGISTRY[call.name], call.args)
    plain = {
        name: " ".join(value.split()).casefold() if isinstance(value, str) else value
        for name, value in clean.items()
    }
    return call.name + " " + json.dumps(plain, sort_keys=True, ensure_ascii=False)
