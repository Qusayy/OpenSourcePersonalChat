"""How an answer is written: the sampling, the room, and a line read last.

There is one agent and one system text, and the model decides the shape of
its own answer: a sentence for a greeting, a list for ideas, code for code.
The system text says how; nothing here reads the message.

What the model can choose is whether the answer is a project. It says so in
its choice (``write_files``), and the answer is then written as files: held
to a grammar that has every path followed by its file, given the room a
project needs, and sampled as code is.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..projects.parse import GRAMMAR as FILES


@dataclass(frozen=True)
class Style:
    id: str
    # The one line the model reads last about the shape of its answer, or ""
    # where the system text has said all there is to say.
    cue: str
    temperature: float
    max_tokens: int
    top_p: float = 0.8
    top_k: int = 20
    min_p: float = 0.0
    repeat_penalty: float = 1.0
    presence_penalty: float = 0.0
    # A GBNF grammar the answer is held to, where its shape has to be exact.
    grammar: str | None = None

    def sampling(self, *, looked_up: bool = False) -> dict:
        """The answer pass's sampling. An answer written from what a tool
        returned is held cooler, whatever its style: it is there to state the
        data, not to improvise on it."""
        return {
            "temperature": min(self.temperature, LOOKED_UP_TEMPERATURE) if looked_up else self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "presence_penalty": self.presence_penalty,
            "max_tokens": self.max_tokens,
        }


# The warmest an answer written from a lookup is sampled.
LOOKED_UP_TEMPERATURE = 0.35

STYLES: dict[str, Style] = {
    s.id: s
    for s in (
        # Qwen3.5's own settings for an answer with its thinking off. The
        # presence penalty is what keeps a small quantised model from going
        # round in circles.
        Style(
            id="answer",
            cue="",
            temperature=0.7,
            max_tokens=1024,
            presence_penalty=1.5,
        ),
        # Code is mostly words it has already used, which a presence penalty
        # would push it away from; and a project takes room.
        Style(
            id="files",
            cue=(
                "Write the project as files. For each file, a line with its path, like "
                "### app/main.py, then the file's complete code in a fenced block with its "
                "language: code, not a description of it. Write every file it needs to run, "
                "then one short paragraph on how to run it."
            ),
            temperature=0.6,
            max_tokens=2048,
            top_p=0.95,
            grammar=FILES,
        ),
    )
}
DEFAULT_STYLE = "answer"
