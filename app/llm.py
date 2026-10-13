"""The inference engine: one Llama instance, one lock, honest metrics.

Design notes that matter on a 2 vCPU box:

* The model is loaded exactly once. Creating a Llama per request would throw
  away the KV cache and re-read ~1 GB of weights every time.
* Exactly one generation runs at a time. Two concurrent generations on two
  cores are slower than the same two run back to back, so requests queue and
  the UI is told its position instead of being left to guess.
* Generation happens on a plain thread; tokens cross back to the event loop
  through an asyncio.Queue. The loop stays free to answer /api/health,
  /api/cancel and to keep other clients' queue counters ticking.
* The thread releases the lock itself, in its own finally. If the async side
  released it, a client disconnecting mid-generation could hand the lock to the
  next request while the old thread was still inside llama.cpp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import threading
import time
import uuid
from collections import deque
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from .config import Settings, settings as default_settings
from .telemetry import describe, process_rss_mb, usable_cpu_count

log = logging.getLogger("aurora.llm")

# ---------------------------------------------------------------- ChatML ----

IM_START = "<|im_start|>"
IM_END = "<|im_end|>"


def render_chatml(messages: list[dict]) -> str:
    """Render a conversation the way Qwen2.5-Instruct expects.

    Used for token accounting and trimming only — the actual prompt is built by
    llama-cpp-python from the GGUF's own chat template. The two agree closely
    enough that the counts shown in the UI are trustworthy.
    """
    parts = []
    for m in messages:
        parts.append(f"{IM_START}{m['role']}\n{m['content']}{IM_END}\n")
    parts.append(f"{IM_START}assistant\n")
    return "".join(parts)


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


# ----------------------------------------------------------------- state ----


@dataclass
class EngineState:
    status: str = "cold"  # cold | loading | ready | error
    error: str | None = None
    loaded_at: float | None = None
    load_ms: float | None = None
    started_at: float = field(default_factory=time.time)


SAMPLING_KEYS = ("temperature", "top_p", "top_k", "repeat_penalty", "max_tokens")

# What the mock engine answers when asked to choose under a grammar.
MOCK_DECISION = '{"tool": "none"}'


@dataclass(frozen=True)
class Completion:
    """What one grammar-constrained pass said, and what it cost.

    The token and timing figures are None when the pass never reached the
    model: the engine was not ready, or the pass failed on the way in.
    """

    # The reply as a JSON object, or None if it was not one.
    parsed: dict | None = None
    raw: str = ""
    # From the call to the end, the wait for the slot included.
    ms: float = 0.0
    # Why there is nothing to read, when there is nothing.
    error: str | None = None
    cancelled: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    queue_wait_ms: float | None = None
    ttft_ms: float | None = None
    # Inside the slot: reading the prompt and writing the reply.
    total_ms: float | None = None


class Engine:
    # Seconds between the notices a waiting request is sent about its place.
    QUEUE_TICK = 0.75

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self.state = EngineState()
        self._llm: Any = None
        self._lock = asyncio.Lock()
        self._waiting: deque[str] = deque()
        self._cancels: dict[str, threading.Event] = {}
        self._grammars: dict[int, Any] = {}
        self._busy = False
        self._completed = 0

    # -- lifecycle ----------------------------------------------------------

    def load_blocking(self) -> None:
        """Load the model. Called off the event loop at startup."""
        if self.cfg.mock:
            self.state.status = "ready"
            self.state.loaded_at = time.time()
            self.state.load_ms = 0.0
            return

        path = self.cfg.resolved_model_path
        if not path.exists():
            # `state.error` is shown to every visitor (the status line, the
            # health endpoint, the page itself), so it says what is wrong and
            # not where things are on this machine. The path goes to the log.
            log.error("model not found at %s", path)
            self.state.status = "error"
            self.state.error = (
                "The model file is missing on the server. Download the GGUF (see "
                "README) or set AURORA_MOCK=true to run the UI without it."
            )
            return

        self.state.status = "loading"
        t0 = time.perf_counter()
        try:
            from llama_cpp import Llama

            kwargs: dict[str, Any] = dict(
                model_path=str(path),
                n_ctx=self.cfg.n_ctx,
                n_threads=self.cfg.n_threads,
                n_threads_batch=self.cfg.n_threads,
                n_batch=self.cfg.n_batch,
                use_mmap=self.cfg.use_mmap,
                use_mlock=self.cfg.use_mlock,
                verbose=False,
            )
            # "auto" lets llama-cpp-python use the template baked into the GGUF,
            # which is what Qwen ships. Forcing "chatml" is the fallback for
            # older conversions that carry no template metadata.
            if self.cfg.chat_format and self.cfg.chat_format != "auto":
                kwargs["chat_format"] = self.cfg.chat_format

            self._llm = Llama(**kwargs)
            self.state.status = "ready"
            self.state.loaded_at = time.time()
            self.state.load_ms = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as exc:  # noqa: BLE001 - surfaced in /api/health
            # The exception's own text can carry the model path; see above.
            log.exception("model failed to load")
            self.state.status = "error"
            self.state.error = (
                f"The model could not be loaded ({type(exc).__name__}). "
                "The server log has the detail."
            )

    @property
    def ready(self) -> bool:
        return self.state.status == "ready"

    # -- token accounting ---------------------------------------------------

    def count_tokens(self, text: str) -> int:
        if self._llm is None:
            return max(1, len(text) // 4)  # mock-mode estimate
        return len(self._llm.tokenize(text.encode("utf-8"), add_bos=False, special=True))

    def prompt_tokens(self, messages: list[dict]) -> int:
        return self.count_tokens(render_chatml(messages))

    def fit_context(
        self, messages: list[dict], max_tokens: int
    ) -> tuple[list[dict], int, int]:
        """Drop oldest turns until the prompt fits. The system message stays.

        Returns (messages, prompt_tokens, dropped_count).
        """
        budget = self.cfg.n_ctx - max_tokens - self.cfg.reply_headroom
        system = [m for m in messages if m["role"] == "system"][:1]
        rest = [m for m in messages if m["role"] != "system"]
        dropped = 0

        while True:
            candidate = system + rest
            used = self.prompt_tokens(candidate)
            if used <= budget or len(rest) <= 1:
                return candidate, used, dropped
            rest.pop(0)
            dropped += 1

    # -- info ---------------------------------------------------------------

    def info(self) -> dict:
        return {
            "status": self.state.status,
            "error": self.state.error,
            "model": self.cfg.model_label,
            "quant": self.cfg.model_quant,
            "params": self.cfg.model_params,
            "n_ctx": self.cfg.n_ctx,
            "n_threads": self.cfg.n_threads,
            "n_batch": self.cfg.n_batch,
            # A label the operator set wins; otherwise the machine is described
            # from what was measured, never from a default that may not be true.
            "hardware": self.cfg.hardware_label or describe(),
            "mock": self.cfg.mock,
            "busy": self._busy,
            "queued": len(self._waiting),
            "completed": self._completed,
            "load_ms": self.state.load_ms,
            "uptime_s": round(time.time() - self.state.started_at, 1),
            "rss_mb": process_rss_mb(),
            "platform": f"{platform.system()} {platform.machine()}",
            # CPUs this process can actually use (affinity and cgroup quota),
            # not every CPU the kernel can see.
            "cpu_count": usable_cpu_count(),
        }

    # -- cancellation -------------------------------------------------------

    def cancel(self, request_id: str) -> bool:
        ev = self._cancels.get(request_id)
        if ev is None:
            return False
        ev.set()
        return True

    # -- generation ---------------------------------------------------------

    def _iter_chunks(
        self,
        messages: list[dict],
        sampling: dict,
        *,
        grammar: str | None = None,
        seed: int | None = None,
        stop: list[str] | None = None,
    ) -> Iterator[str]:
        """Yield text deltas from llama.cpp (or the mock generator)."""
        if self.cfg.mock:
            if grammar is not None:
                # A pass held to a grammar is a decision, and without a model
                # the only honest one is "no tool". It is said at once: there
                # is nothing here whose speed is worth imitating.
                yield MOCK_DECISION
                return
            for i, word in enumerate(MOCK_VOCAB):
                time.sleep(self.cfg.mock_delay)  # ~11 tok/s by default
                yield ("" if i == 0 else " ") + word
            return

        # Only what the caller named is passed on. A pass that sets two of
        # these gets the library's own defaults for the rest, not ours.
        kwargs: dict[str, Any] = {k: sampling[k] for k in SAMPLING_KEYS if k in sampling}
        if grammar is not None:
            kwargs["grammar"] = self._grammar(grammar)
        if seed is not None:
            kwargs["seed"] = seed
        if stop:
            kwargs["stop"] = stop
        stream = self._llm.create_chat_completion(messages=messages, stream=True, **kwargs)
        try:
            for chunk in stream:
                delta = chunk["choices"][0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()

    # -- constrained completion (tool routing) ------------------------------

    def _grammar(self, text: str):
        """Compile and cache a GBNF grammar."""
        from llama_cpp import LlamaGrammar

        key = hash(text)
        cached = self._grammars.get(key)
        if cached is None:
            cached = LlamaGrammar.from_string(text, verbose=False)
            self._grammars[key] = cached
        return cached

    async def stream_json(
        self,
        messages: list[dict],
        grammar_text: str,
        max_tokens: int | None = None,
        *,
        request_id: str | None = None,
        seed: int | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """One short grammar-constrained pass, with its wait made visible.

        Yields ``("queue", payload)`` while the pass waits for the slot, then
        exactly one ``("completion", Completion)``.

        It is an ordinary pass: it queues, runs on a worker thread and gives
        the slot back from that thread, like an answer. A 2 vCPU box must
        never run two inferences at once, routing included, and a caller that
        goes away mid-pass must not be able to free the slot while llama.cpp
        is still working.
        """
        t0 = time.perf_counter()
        # The grammar decides the shape; greedy picks the content.
        sampling = {
            "temperature": 0.0,
            "top_k": 1,
            "max_tokens": max_tokens or self.cfg.route_max_tokens,
        }
        result = Completion(error="the pass ended without saying anything")
        try:
            async with aclosing(
                self.stream_chat(messages, sampling, request_id, grammar=grammar_text, seed=seed)
            ) as stream:
                async for event, data in stream:
                    if event == "queue":
                        yield event, data
                    elif event == "error":
                        result = Completion(error=data["message"])
                    elif event == "done":
                        raw = data["text"].strip()
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            parsed = None
                        result = Completion(
                            parsed=parsed if isinstance(parsed, dict) else None,
                            raw=raw,
                            cancelled=data["cancelled"],
                            prompt_tokens=data["prompt_tokens"],
                            completion_tokens=data["completion_tokens"],
                            queue_wait_ms=data["queue_wait_ms"],
                            ttft_ms=data["ttft_ms"],
                            total_ms=data["total_ms"],
                        )
        except Exception as exc:  # noqa: BLE001 — a decision that fails is "no decision", never a broken chat
            result = Completion(error=f"{type(exc).__name__}: {exc}")
        yield "completion", replace(result, ms=(time.perf_counter() - t0) * 1000)

    async def complete_json(
        self,
        messages: list[dict],
        grammar_text: str,
        max_tokens: int | None = None,
        *,
        request_id: str | None = None,
        seed: int | None = None,
    ) -> Completion:
        """``stream_json`` for a caller with nowhere to show a queue."""
        result = Completion(error="the pass ended without saying anything")
        async with aclosing(
            self.stream_json(messages, grammar_text, max_tokens, request_id=request_id, seed=seed)
        ) as stream:
            async for event, data in stream:
                if event == "completion":
                    result = data
        return result

    async def stream_chat(
        self,
        messages: list[dict],
        sampling: dict,
        request_id: str | None = None,
        *,
        grammar: str | None = None,
        seed: int | None = None,
        stop: list[str] | None = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield (event, payload) tuples: queue | start | token | done | error.

        One pass through the model, whatever it is for. ``grammar`` holds the
        output to a GBNF grammar, ``seed`` makes the sampling repeatable and
        ``stop`` ends the output at any of the given strings.
        """
        request_id = request_id or uuid.uuid4().hex
        cancel_ev = threading.Event()
        self._cancels[request_id] = cancel_ev

        # Who hands the slot back. Until a worker thread exists it can only be
        # this coroutine; from then on it is the thread and nobody else.
        acquire: asyncio.Future | None = None
        started = False
        try:
            if not self.ready:
                yield "error", {
                    "message": self.state.error
                    or "The model is still loading. Give it a moment, then send again."
                }
                return

            loop = asyncio.get_running_loop()
            queued_at = time.perf_counter()

            # --- wait for the one inference slot, reporting position while we wait
            self._waiting.append(request_id)
            acquire = asyncio.ensure_future(self._lock.acquire())
            first_notice = True
            while True:
                done, _ = await asyncio.wait({acquire}, timeout=self.QUEUE_TICK)
                if acquire in done:
                    break
                try:
                    ahead = list(self._waiting).index(request_id)
                except ValueError:
                    ahead = 0
                yield "queue", {"position": ahead, "first": first_notice}
                first_notice = False
            self._waiting.remove(request_id)

            queue_wait_ms = round((time.perf_counter() - queued_at) * 1000, 1)
            self._busy = True

            events: asyncio.Queue = asyncio.Queue()
            SENTINEL = object()

            prompt_tok = self.prompt_tokens(messages)

            def emit(item) -> None:
                loop.call_soon_threadsafe(events.put_nowait, item)

            def worker() -> None:
                t_start = time.perf_counter()
                t_first: float | None = None
                produced = 0
                pieces: list[str] = []
                try:
                    for text in self._iter_chunks(
                        messages, sampling, grammar=grammar, seed=seed, stop=stop
                    ):
                        if cancel_ev.is_set():
                            break
                        payload = {"t": text}
                        if t_first is None:
                            t_first = time.perf_counter()
                            # The same figure "done" reports, sent as soon as it
                            # is known, so the page can show it while the answer
                            # is still being written instead of after it has
                            # ended.
                            payload["ttft_ms"] = round((t_first - t_start) * 1000, 1)
                        produced += 1
                        pieces.append(text)
                        emit(("token", payload))

                    now = time.perf_counter()
                    ttft_ms = round(((t_first or now) - t_start) * 1000, 1)
                    gen_s = max(now - (t_first or now), 1e-6)
                    emit(
                        (
                            "done",
                            {
                                "text": "".join(pieces),
                                "cancelled": cancel_ev.is_set(),
                                "ttft_ms": ttft_ms,
                                "queue_wait_ms": queue_wait_ms,
                                "prompt_tokens": prompt_tok,
                                "completion_tokens": produced,
                                # prefill covers the prompt plus the first token
                                "prefill_tps": round(prompt_tok / max(ttft_ms / 1000, 1e-6), 1),
                                "gen_tps": round(max(produced - 1, 0) / gen_s, 2),
                                "total_ms": round((now - t_start) * 1000, 1),
                                "rss_mb": process_rss_mb(),
                            },
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced to the client
                    emit(("error", {"message": f"{type(exc).__name__}: {exc}"}))
                finally:
                    # The thread owns the slot until it has truly left
                    # llama.cpp. It hands it back before it sends its last
                    # event, so whoever sees the stream end finds the slot free.
                    loop.call_soon_threadsafe(self._release_slot)
                    emit(SENTINEL)

            thread = threading.Thread(target=worker, name=f"gen-{request_id[:8]}", daemon=True)
            thread.start()
            started = True

            yield "start", {
                "request_id": request_id,
                "queue_wait_ms": queue_wait_ms,
                "prompt_tokens": prompt_tok,
                "n_ctx": self.cfg.n_ctx,
            }

            while True:
                item = await events.get()
                if item is SENTINEL:
                    break
                yield item
        finally:
            # Reached however the pass ends: it finished, the client vanished,
            # the caller stopped iterating, or something failed on the way in.
            # A thread that is writing is told to stop.
            cancel_ev.set()
            self._cancels.pop(request_id, None)
            if request_id in self._waiting:
                self._waiting.remove(request_id)
            if acquire is not None and not started:
                # No thread exists to hand the slot back. Either the wait for
                # it is still going and is called off, or it was won and
                # nothing reached the model, so it is returned from here.
                won = not acquire.cancel() and not acquire.cancelled() and acquire.exception() is None
                if won:
                    self._busy = False
                    if self._lock.locked():
                        self._lock.release()

    def _release_slot(self) -> None:
        self._busy = False
        self._completed += 1
        if self._lock.locked():
            self._lock.release()


engine = Engine()
