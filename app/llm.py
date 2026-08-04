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
import os
import platform
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from .config import Settings, settings as default_settings

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


# ------------------------------------------------------------- utilities ----


def process_rss_mb() -> float | None:
    """Resident set size in MB, or None where it cannot be read cheaply."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:  # Windows / macOS dev machines
        import psutil  # type: ignore

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        return None


MOCK_VOCAB = (
    "Quantization stores each weight in fewer bits, so the model file shrinks "
    "and far less memory has to be read for every single token that gets "
    "produced. Because generation on a CPU is limited by memory bandwidth "
    "rather than raw arithmetic, halving the bytes very nearly doubles the "
    "speed. The trade is a small loss of precision, which a four bit K quant "
    "hides well at this size. That is the whole reason a one and a half "
    "billion parameter model feels responsive on two shared cores.\n\n"
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


class Engine:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self.state = EngineState()
        self._llm: Any = None
        self._lock = asyncio.Lock()
        self._waiting: deque[str] = deque()
        self._cancels: dict[str, threading.Event] = {}
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
            self.state.status = "error"
            self.state.error = (
                f"Model not found at {path}. Download the GGUF (see README) or "
                f"set AURORA_MOCK=true to run the UI without it."
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
            self.state.status = "error"
            self.state.error = f"{type(exc).__name__}: {exc}"

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
            "hardware": self.cfg.hardware_label,
            "mock": self.cfg.mock,
            "busy": self._busy,
            "queued": len(self._waiting),
            "completed": self._completed,
            "load_ms": self.state.load_ms,
            "uptime_s": round(time.time() - self.state.started_at, 1),
            "rss_mb": process_rss_mb(),
            "platform": f"{platform.system()} {platform.machine()}",
            "cpu_count": os.cpu_count(),
        }

    # -- cancellation -------------------------------------------------------

    def cancel(self, request_id: str) -> bool:
        ev = self._cancels.get(request_id)
        if ev is None:
            return False
        ev.set()
        return True

    # -- generation ---------------------------------------------------------

    def _iter_chunks(self, messages: list[dict], sampling: dict) -> Iterator[str]:
        """Yield text deltas from llama.cpp (or the mock generator)."""
        if self.cfg.mock:
            for i, word in enumerate(MOCK_VOCAB):
                time.sleep(0.085)  # ~11 tok/s, the shape of the real thing
                yield ("" if i == 0 else " ") + word
            return

        stream = self._llm.create_chat_completion(
            messages=messages,
            stream=True,
            temperature=sampling["temperature"],
            top_p=sampling["top_p"],
            top_k=sampling["top_k"],
            repeat_penalty=sampling["repeat_penalty"],
            max_tokens=sampling["max_tokens"],
        )
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

    async def stream_chat(
        self,
        messages: list[dict],
        sampling: dict,
        request_id: str | None = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield (event, payload) tuples: queue | start | token | done | error."""
        request_id = request_id or uuid.uuid4().hex
        cancel_ev = threading.Event()
        self._cancels[request_id] = cancel_ev

        if not self.ready:
            self._cancels.pop(request_id, None)
            yield "error", {"message": self.state.error or "Model is not ready yet."}
            return

        loop = asyncio.get_running_loop()
        queued_at = time.perf_counter()

        # --- wait for the one inference slot, reporting position while we wait
        self._waiting.append(request_id)
        acquire = asyncio.ensure_future(self._lock.acquire())
        try:
            first_notice = True
            while True:
                done, _ = await asyncio.wait({acquire}, timeout=0.75)
                if acquire in done:
                    break
                try:
                    ahead = list(self._waiting).index(request_id)
                except ValueError:
                    ahead = 0
                yield "queue", {"position": ahead, "first": first_notice}
                first_notice = False
        except (asyncio.CancelledError, GeneratorExit):
            # If the acquire had already won the race, hand the slot straight
            # back — nothing else will release it for us.
            if not acquire.cancel() and acquire.done() and not acquire.cancelled():
                if self._lock.locked():
                    self._lock.release()
            if request_id in self._waiting:
                self._waiting.remove(request_id)
            self._cancels.pop(request_id, None)
            raise
        finally:
            if request_id in self._waiting:
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
                for text in self._iter_chunks(messages, sampling):
                    if cancel_ev.is_set():
                        break
                    if t_first is None:
                        t_first = time.perf_counter()
                    produced += 1
                    pieces.append(text)
                    emit(("token", {"t": text}))

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
                emit(SENTINEL)
                # The thread owns the lock until it has truly left llama.cpp.
                loop.call_soon_threadsafe(self._release_slot)

        thread = threading.Thread(target=worker, name=f"gen-{request_id[:8]}", daemon=True)
        thread.start()

        yield "start", {
            "request_id": request_id,
            "queue_wait_ms": queue_wait_ms,
            "prompt_tokens": prompt_tok,
            "n_ctx": self.cfg.n_ctx,
        }

        try:
            while True:
                item = await events.get()
                if item is SENTINEL:
                    break
                yield item
        finally:
            # Client vanished or the caller stopped iterating: stop the thread.
            cancel_ev.set()
            self._cancels.pop(request_id, None)

    def _release_slot(self) -> None:
        self._busy = False
        self._completed += 1
        if self._lock.locked():
            self._lock.release()


engine = Engine()
