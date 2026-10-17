"""The engine without a model: what it writes, so that a page can be built and
tested on a machine with no weights (``AURORA_MOCK=true``)."""

from __future__ import annotations

import time
from typing import Iterator

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


def chunks(grammar: str | None, delay: float) -> Iterator[str]:
    """What a pass writes without a model."""
    if grammar is not None:
        # A pass held to a grammar is a decision, and without a model the
        # only honest one is "no tool". It is said at once: there is nothing
        # here whose speed is worth imitating.
        yield MOCK_DECISION
        return
    for i, word in enumerate(MOCK_VOCAB):
        time.sleep(delay)  # ~11 tok/s by default
        yield ("" if i == 0 else " ") + word
