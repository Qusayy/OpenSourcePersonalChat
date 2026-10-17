"""An engine that plays back prepared output instead of running a model.

It is the real ``Engine`` in every respect but one. The lock, the queue, the
worker thread, the cancel flag and the figures a pass reports are the engine's
own code, running as it runs on the server. Only the call that would ask
llama.cpp for text is replaced: each pass is handed the next entry of a script.

That makes it the right tool for two jobs:

* Testing the rules around the model. Whether two passes can ever overlap, or
  what happens to the slot when a reader walks away, does not depend on what
  the model says, and can be settled in milliseconds on any machine.
* Testing what the server does with a given answer from the model. The script
  is what the model "said"; everything after that is real.

What it cannot tell you is anything about the model. A script is not checked
against a grammar, so it can say things the real model could never produce.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterator, Sequence

from app.config import Settings, settings as default_settings
from app.llm import Engine, render_chatml
from app.llm.server import Reading

# A piece is a word with the space after it, so the pieces of an output put
# back together are the output, to the character.
_PIECE = re.compile(r"\S+\s*|\s+")


@dataclass
class Pass:
    """One pass the engine was asked for, and what it was given to say."""

    messages: list[dict]
    sampling: dict
    grammar: str | None
    seed: int | None
    stop: list[str] | None
    output: str | BaseException
    # How many pieces of the output were handed over before the pass ended.
    yielded: int = 0

    @property
    def prompt(self) -> str:
        """The prompt as one string, the way the engine counts it."""
        return render_chatml(self.messages)

    @property
    def pieces(self) -> list[str]:
        return [] if isinstance(self.output, BaseException) else _PIECE.findall(self.output)


class ScriptedEngine(Engine):
    """Same methods as ``Engine``. Each pass says the next entry of ``script``.

    An entry is the text of that pass, or an exception for the model to raise.
    A pass asked for after the script has run out fails, so a run that makes
    one pass too many is seen and not papered over.

    ``delay`` is the pause before each piece and ``prefill`` the pause before
    the first, in seconds. ``gated`` holds every pass inside "the model" until
    ``release()`` is called, which is how a test keeps the slot occupied for
    exactly as long as it needs. ``gate_from`` lets the first few through and
    holds from that pass on (counting from zero), for a test that is about a
    later pass of a run.
    """

    def __init__(
        self,
        script: Sequence[str | BaseException] = (),
        *,
        delay: float = 0.0,
        prefill: float = 0.0,
        gated: bool = False,
        gate_from: int = 0,
        cfg: Settings | None = None,
    ) -> None:
        super().__init__(
            cfg or default_settings.model_copy(update={"mock": True, "mock_delay": 0.0})
        )
        self.state.status = "ready"
        self.script: list[str | BaseException] = list(script)
        self.delay = delay
        self.prefill = prefill
        self.passes: list[Pass] = []
        # The most passes that were ever inside the model together. One
        # inference slot means this never exceeds one.
        self.most_at_once = 0
        self._inside = 0
        self._guard = threading.Lock()
        self._gate = threading.Event()
        self._gate_from = gate_from
        if not gated:
            self._gate.set()

    # -- the model --------------------------------------------------------

    def _iter_chunks(
        self,
        messages: list[dict],
        sampling: dict,
        *,
        grammar: str | None = None,
        seed: int | None = None,
        stop: list[str] | None = None,
        reading: Reading | None = None,
    ) -> Iterator[str]:
        # A script reads nothing, so it reports nothing of what it read.
        with self._guard:
            index = len(self.passes)
            if index >= len(self.script):
                raise IndexError(f"the script has nothing for pass {index + 1}")
            record = Pass(
                messages=[dict(m) for m in messages],
                sampling=dict(sampling),
                grammar=grammar,
                seed=seed,
                stop=list(stop) if stop else None,
                output=self.script[index],
            )
            self.passes.append(record)
            self._inside += 1
            self.most_at_once = max(self.most_at_once, self._inside)
        try:
            if self.prefill:
                time.sleep(self.prefill)
            if index >= self._gate_from:
                self._gate.wait()
            if isinstance(record.output, BaseException):
                raise record.output
            for piece in record.pieces:
                if self.delay:
                    time.sleep(self.delay)
                record.yielded += 1
                yield piece
        finally:
            with self._guard:
                self._inside -= 1

    # -- holding and letting go -------------------------------------------

    def pause(self) -> None:
        """Keep every pass from here on inside the model until ``release``."""
        self._gate.clear()

    def release(self) -> None:
        """Let the passes that are being held go on."""
        self._gate.set()

    async def entered(self, n: int = 1, timeout: float = 5.0) -> None:
        """Wait until ``n`` passes have reached the model."""
        await _until(lambda: len(self.passes) >= n, timeout, f"{n} passes to reach the model")

    async def settled(self, timeout: float = 5.0) -> None:
        """Wait until nothing is inside the model and the slot has been handed
        back. Lets a held pass go first: nothing settles while one is held.

        Await this before a test's event loop closes. A worker thread that
        outlives the loop has nowhere to hand its slot back to.
        """
        self.release()
        await _until(
            lambda: self._inside == 0 and not self._lock.locked() and not self._busy,
            timeout,
            "the engine to settle",
        )

    @property
    def free(self) -> bool:
        """Nobody holds the slot and nothing is marked as running."""
        return not self._lock.locked() and not self._busy


async def _until(done, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            raise TimeoutError(f"gave up waiting for {what}")
        await asyncio.sleep(0.001)
