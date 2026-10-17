"""The chat format, written out as text.

Used for counting tokens and fitting a prompt to the window, never to build
the prompt itself: llama-cpp-python, or llama-server, does that from the
GGUF's own chat template, or from the one it is given. The two agree closely
enough that the counts shown in the UI are trustworthy.
"""

from __future__ import annotations

from .config import settings

IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
# How Qwen3.5 begins an answer with its thinking off: an empty block. The
# model's own template leaves it out of every earlier answer, so each new
# message would no longer begin with the last one and the server would read
# the whole conversation again; deploy/templates/qwen3.5.jinja writes it into
# every answer, and so does this.
THINK_BLOCK = "<think>\n\n</think>\n\n"


def render_chatml(messages: list[dict], *, think: bool | None = None) -> str:
    """Render a conversation the way a ChatML model reads it. With ``think``
    (by default the ``think_block`` setting), every answer, and the one to
    come, begins with an empty thinking block."""
    opened = THINK_BLOCK if (settings.think_block if think is None else think) else ""
    parts = []
    for m in messages:
        lead = opened if m["role"] == "assistant" else ""
        parts.append(f"{IM_START}{m['role']}\n{lead}{m['content']}{IM_END}\n")
    parts.append(f"{IM_START}assistant\n{opened}")
    return "".join(parts)
