"""The engine without a model: what it writes, so that a page can be built and
tested on a machine with no weights (``AURORA_MOCK=true``)."""

from __future__ import annotations

import time
from typing import Iterator

from ..chatml import THINK_CLOSE

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

# What it thinks first, where the model thinks.
MOCK_THOUGHT = "The question is about speed on a CPU, so I will explain what quantization saves.".split(" ")


def chunks(delay: float, thinking: str = "none", prefix: str = "") -> Iterator[str]:
    """What a pass writes without a model: a short thought where the model
    thinks and has not yet, then the same paragraph every time."""
    if thinking == "on" and THINK_CLOSE not in prefix:
        for i, word in enumerate(MOCK_THOUGHT):
            time.sleep(delay)
            yield ("" if i == 0 else " ") + word
        yield "\n" + THINK_CLOSE + "\n\n"
    for i, word in enumerate(MOCK_VOCAB):
        time.sleep(delay)  # ~11 tok/s by default
        yield ("" if i == 0 else " ") + word
