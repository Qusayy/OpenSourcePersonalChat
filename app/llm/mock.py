"""The engine without a model: what it writes, so that a page can be built and
tested on a machine with no weights (``AURORA_MOCK=true``)."""

from __future__ import annotations

import time
from typing import Iterator

from ..projects.parse import GRAMMAR as FILES

MOCK_VOCAB = (
    "Quantization stores each weight in fewer bits, so the model file shrinks "
    "and far less memory has to be read for every single token that gets "
    "produced. Because generation on a CPU is limited by memory bandwidth "
    "rather than raw arithmetic, halving the bytes very nearly doubles the "
    "speed. The trade is a small loss of precision, which a four bit K quant "
    "hides well at this size. That is the whole reason a one and a half "
    "billion parameter model feels responsive without a graphics card.\n\n"
    "In short: fewer bytes per weight, fewer bytes moved, more tokens per "
    "second.\n"
).split(" ")

# What the mock engine answers when asked to choose under a grammar.
MOCK_DECISION = '{"tool": "none"}'
# What it writes when the answer is a project, held to the file-block grammar.
MOCK_PROJECT = (
    "A small app to start from.\n\n"
    "### app/main.py\n```python\nfrom fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
    "@app.get(\"/\")\ndef root():\n    return {\"message\": \"Hello, World!\"}\n```\n\n"
    "### requirements.txt\n```text\nfastapi\nuvicorn\n```\n\n"
    "Install the requirements, then run `uvicorn app.main:app --reload`."
)


def chunks(grammar: str | None, delay: float) -> Iterator[str]:
    """What a pass writes without a model."""
    if grammar == FILES:
        for i, line in enumerate(MOCK_PROJECT.splitlines(keepends=True)):
            time.sleep(delay)
            yield line
        return
    if grammar is not None:
        # Any other pass held to a grammar is a decision, and without a model the
        # only honest one is "no tool". It is said at once: there is nothing
        # here whose speed is worth imitating.
        yield MOCK_DECISION
        return
    for i, word in enumerate(MOCK_VOCAB):
        time.sleep(delay)  # ~11 tok/s by default
        yield ("" if i == 0 else " ") + word
