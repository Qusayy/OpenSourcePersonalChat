"""How the model's text is sampled: as prose, or as code.

There is one agent and one system text, and the model decides the shape of
its own answer. What is chosen here is only how its next tokens are drawn,
and it follows what the model is writing, never what the message says:
inside a parameter that holds code (a file, an edit, a program) it is code,
and everything else is prose.

The figures are Qwen3.5's own, for each way it runs: with its thinking on,
prose is its "general" setting and code its "precise coding" one; with it
off, prose is its "instruct" setting. Code has no presence penalty: code is
mostly words it has already used, and an edit has to copy text exactly.
"""

from __future__ import annotations

PROSE_THINKING = {
    "temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
    "presence_penalty": 1.5, "repeat_penalty": 1.0,
}
PROSE = {
    "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
    "presence_penalty": 1.5, "repeat_penalty": 1.0,
}
CODE = {
    "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
    "presence_penalty": 0.0, "repeat_penalty": 1.0,
}


def sampling(mode: str, thinking: str = "none") -> dict:
    """The settings for writing ``mode`` ("prose" or "code")."""
    if mode == "code":
        return dict(CODE)
    return dict(PROSE_THINKING if thinking == "on" else PROSE)
