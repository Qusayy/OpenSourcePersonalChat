"""The chat format, written out as text: the prompt the model reads.

The app writes the prompt itself and hands it to the model whole, so what is
counted, what is sent and what is replayed next turn are the same characters.
Every message is written as it is, an answer included: an answer carries its
own thinking block, as the model wrote it, so the next prompt begins with the
last one and the server reads only what is new.

What opens the answer still to be written depends on how the model thinks
(the ``thinking`` setting):

* ``on``: an open thinking block. The model thinks, closes it, and answers.
* ``off``: an empty one, the way Qwen3.5 answers with its thinking off.
* ``none``: nothing, for a model with no thinking block at all.
"""

from __future__ import annotations

from .config import settings

IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
THINK_OPEN = "<think>\n"
THINK_CLOSE = "</think>"
# How Qwen3.5 begins an answer with its thinking off: an empty block.
THINK_BLOCK = "<think>\n\n</think>\n\n"

OPENERS = {"on": THINK_OPEN, "off": THINK_BLOCK, "none": ""}


def opener(thinking: str | None = None) -> str:
    """What the model is given to begin an answer with."""
    return OPENERS[thinking or settings.think_mode]


def render_chatml(messages: list[dict], *, prefix: str = "", thinking: str | None = None) -> str:
    """A conversation as the model reads it, ending where the next answer
    begins: its opener, then ``prefix``, the part of it already written."""
    parts = [f"{IM_START}{m['role']}\n{m['content']}{IM_END}\n" for m in messages]
    parts.append(f"{IM_START}assistant\n{opener(thinking)}{prefix}")
    return "".join(parts)


def split_thought(raw: str) -> tuple[str, str]:
    """An answer as written, without its opener, as (thought, what it said).
    Text with no thinking block is all said."""
    text = raw
    if text.startswith(THINK_OPEN):
        text = text[len(THINK_OPEN):]
        if THINK_CLOSE not in text:
            return text.strip(), ""
        thought, _, said = text.partition(THINK_CLOSE)
        return thought.strip(), said.lstrip("\n")
    return "", text
