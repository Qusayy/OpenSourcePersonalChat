"""The chat format, written out as text.

Used for counting tokens and fitting a prompt to the window, never to build
the prompt itself: llama-cpp-python, or llama-server, does that from the
GGUF's own chat template. The two agree closely enough that the counts shown
in the UI are trustworthy.
"""

from __future__ import annotations

IM_START = "<|im_start|>"
IM_END = "<|im_end|>"


def render_chatml(messages: list[dict]) -> str:
    """Render a conversation the way Qwen2.5-Instruct expects."""
    parts = []
    for m in messages:
        parts.append(f"{IM_START}{m['role']}\n{m['content']}{IM_END}\n")
    parts.append(f"{IM_START}assistant\n")
    return "".join(parts)
