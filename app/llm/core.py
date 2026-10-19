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

The model can also live in another process: with ``AURORA_LLM_URL`` set, every
pass goes to a llama-server over HTTP (``app/llm/server.py``) and nothing here
loads weights. All of the above still holds, because the slot, the queue and
the worker thread are this module's and only the three places that touch the
model change: loading, counting tokens and writing. A thread that
stops reading closes its connection, and the server stops writing at its next
token.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

from ..chatml import render_chatml
from ..config import Settings, settings as default_settings
from ..ops.telemetry import describe, follow_server, process_rss_mb, usable_cpu_count
from . import mock
from .server import LlamaServer, Reading
from .usage import blank

log = logging.getLogger("aurora.llm")

# ----------------------------------------------------------------- state ----


@dataclass
class EngineState:
    status: str = "cold"  # cold | loading | ready | error
    error: str | None = None
    loaded_at: float | None = None
    load_ms: float | None = None
    started_at: float = field(default_factory=time.time)


SAMPLING_KEYS = (
    "temperature", "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty", "max_tokens",
)

# What llama-cpp-python uses for a sampling setting a pass does not name. A
# llama-server has defaults of its own (repeat_penalty 1.1, temperature 0.8),
# and a pass that named only some settings would get those for the rest; sent
# these, a pass is the same pass on either engine.
LIBRARY_DEFAULTS = {"temperature": 0.2, "top_p": 0.95, "top_k": 40, "min_p": 0.05, "repeat_penalty": 1.0}


class Stops:
    """Ends a stream of text at the first of some strings, as a model server
    does: the string itself is not passed on, and neither is anything that
    could still turn out to be the start of one.

    Used where the app reads the model's text itself (the model in this
    process, the mock, a script). A model server stops by itself and says
    which string stopped it."""

    def __init__(self, words: list[str] | None) -> None:
        self.words = [w for w in (words or ()) if w]
        self.word: str | None = None
        self._held = ""

    def feed(self, text: str) -> str:
        """What of ``text`` may be passed on now."""
        if self.word is not None:
            return ""
        buf = self._held + text
        found = [(buf.find(w), w) for w in self.words if w in buf]
        if found:
            at, self.word = min(found)
            self._held = ""
            return buf[:at]
        keep = 0
        for w in self.words:
            for n in range(min(len(w) - 1, len(buf)), 0, -1):
                if buf.endswith(w[:n]):
                    keep = max(keep, n)
                    break
        self._held = buf[len(buf) - keep:] if keep else ""
        return buf[:len(buf) - keep]

    def flush(self) -> str:
        """What was held back, once the text has ended without a stop."""
        out, self._held = self._held, ""
        return "" if self.word is not None else out


class Stop:
    """Somebody asked for a run to stop.

    It is read from two sides. The worker thread looks at it between tokens,
    and the run's own coroutine waits on it while a tool is out. So it is two
    events set together, one for each side. It is only ever set from the event
    loop: by the cancel endpoint, or by whoever is driving the run.
    """

    def __init__(self) -> None:
        self._flag = threading.Event()
        self._asked = asyncio.Event()

    def set(self) -> None:
        self._flag.set()
        self._asked.set()

    def is_set(self) -> bool:
        return self._flag.is_set()

    async def wait(self) -> None:
        await self._asked.wait()


class Engine:
    # Seconds between the notices a waiting request is sent about its place.
    QUEUE_TICK = 0.75
    # A model server is asked whether it is up this often while the app waits
    # for it, and after this long without an answer the page says so.
    SERVER_POLL = 1.0
    SERVER_PATIENCE = 60.0

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self.state = EngineState()
        self._llm: Any = None
        # The model server, once it has answered. None while the model is in
        # this process, and while the server is still being waited for.
        self._server: Any = None
        # What the server said each recent text came to: every count is a
        # request to it, and a run counts the same prompt more than once.
        self._counted: OrderedDict[str, int] = OrderedDict()
        self._lock = asyncio.Lock()
        self._waiting: deque[str] = deque()
        self._cancels: dict[str, Stop] = {}
        self._busy = False
        self._completed = 0
        # The run that is holding the slot across its passes, if one is, and
        # whether a worker thread is inside the model at this moment.
        self._holder: str | None = None
        self._inside = False

    # -- lifecycle ----------------------------------------------------------

    def load_blocking(self, keep_waiting: bool = False) -> None:
        """Load the model. Called off the event loop at startup.

        With a model server configured, nothing is loaded here: this waits
        for the server instead. ``keep_waiting`` is for the web app, which
        must come back by itself whenever the server does; a script gives up
        after ``SERVER_PATIENCE`` and reports the error.
        """
        if self.cfg.mock:
            self.state.status = "ready"
            self.state.loaded_at = time.time()
            self.state.load_ms = 0.0
            return

        # getattr: a test loads with a config that has only the fields the
        # in-process path reads first.
        url = getattr(self.cfg, "llm_url", "")
        if url:
            self._wait_for_server(url, keep_waiting)
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
                n_threads=self.threads,
                n_threads_batch=self.threads,
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

    def _connect(self, url: str):
        return LlamaServer(url, key=getattr(self.cfg, "llm_key", ""))

    def _wait_for_server(self, url: str, keep_waiting: bool) -> None:
        """Wait until the model server answers.

        It is its own service and may still be starting, or reading and
        repacking its weights, when the app is already up. Past
        ``SERVER_PATIENCE`` the page says it is not answering; the web app
        goes on waiting, so the site is back the moment the server is.
        """
        self.state.status = "loading"
        t0 = time.perf_counter()
        server = self._connect(url)
        while not server.ready():
            late = time.perf_counter() - t0 >= self.SERVER_PATIENCE
            if late and self.state.status != "error":
                # The address goes to the log; the page says what is wrong.
                log.error("the model server at %s has not answered", url)
                self.state.status = "error"
                self.state.error = (
                    "The model server is not answering. The server log has the detail."
                )
            if late and not keep_waiting:
                return
            time.sleep(self.SERVER_POLL)
        self._server = server
        # The model's memory is the server's now; the pages count it as ours.
        follow_server(getattr(server, "port", None))
        self.state.status = "ready"
        self.state.error = None
        self.state.loaded_at = time.time()
        self.state.load_ms = round((time.perf_counter() - t0) * 1000, 1)

    @property
    def ready(self) -> bool:
        return self.state.status == "ready"

    @property
    def runtime(self) -> str:
        """What holds the model: a server beside the app, or this process."""
        return "llama-server" if getattr(self.cfg, "llm_url", "") else "llama-cpp-python"

    @property
    def threads(self) -> int:
        """Threads for the model: the number configured, or with 0 every CPU
        this process may use. Generation on a CPU slows down when it is given
        fewer cores than it has, and a count copied from an older, smaller
        machine is the usual way that happens."""
        if self.cfg.n_threads > 0:
            return self.cfg.n_threads
        return usable_cpu_count() or 1

    # -- token accounting ---------------------------------------------------

    # How many counts the server is not asked for again.
    COUNTS_KEPT = 256

    def count_tokens(self, text: str) -> int:
        if self._server is not None:
            known = self._counted.get(text)
            if known is None:
                known = self._server.tokenize(text)
                self._counted[text] = known
                if len(self._counted) > self.COUNTS_KEPT:
                    self._counted.popitem(last=False)
            else:
                self._counted.move_to_end(text)
            return known
        if self._llm is None:
            return max(1, len(text) // 4)  # mock-mode estimate
        return len(self._llm.tokenize(text.encode("utf-8"), add_bos=False, special=True))

    def render(self, messages: list[dict], prefix: str = "") -> str:
        """The prompt for ``messages``, as this engine's model reads it."""
        return render_chatml(messages, prefix=prefix, thinking=getattr(self.cfg, "think_mode", None))

    def prompt_tokens(self, messages: list[dict], prefix: str = "") -> int:
        return self.count_tokens(self.render(messages, prefix))

    # -- info ---------------------------------------------------------------

    def info(self) -> dict:
        return {
            "status": self.state.status,
            "error": self.state.error,
            "model": self.cfg.model_label,
            "quant": self.cfg.model_quant,
            "params": self.cfg.model_params,
            "n_ctx": self.cfg.n_ctx,
            "n_threads": self.threads,
            "n_batch": self.cfg.n_batch,
            # A label the operator set wins; otherwise the machine is described
            # from what was measured, never from a default that may not be true.
            "hardware": self.cfg.hardware_label or describe(),
            "mock": self.cfg.mock,
            "runtime": self.runtime,
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

    @contextmanager
    def hold(self, request_id: str) -> Iterator[Stop]:
        """Make ``request_id`` stoppable for as long as the block lasts.

        A pass registers its own id only while it runs. A run is several
        passes with tool calls in between, and Stop has to find it during all
        of that, so whoever owns the run holds its id open around the whole
        thing. Every pass given the same id then shares the one ``Stop``.

        Holds nest: an inner one finds the outer registration and leaves it
        alone, and the id is forgotten when the outermost block ends.
        """
        held = self._cancels.get(request_id)
        if held is not None:
            yield held
            return
        stop = Stop()
        self._cancels[request_id] = stop
        try:
            yield stop
        finally:
            self._cancels.pop(request_id, None)

    def cancel(self, request_id: str) -> bool:
        stop = self._cancels.get(request_id)
        if stop is None:
            return False
        stop.set()
        return True

    # -- generation ---------------------------------------------------------

    def _iter_chunks(
        self,
        messages: list[dict],
        sampling: dict,
        *,
        prefix: str = "",
        seed: int | None = None,
        stop: list[str] | None = None,
        reading: Reading | None = None,
    ) -> Iterator[str]:
        """Yield text deltas from the model, wherever it is, or from the mock
        generator, and fill in ``reading``: how the reply ended, and, from a
        model server, how much of the prompt it read and how much it had in
        its cache."""
        reading = reading if reading is not None else Reading()
        limit = sampling.get("max_tokens")
        if self.cfg.mock:
            yield from self._stopped_at(
                mock.chunks(self.cfg.mock_delay, getattr(self.cfg, "think_mode", "none"), prefix),
                stop, limit, reading,
            )
            return

        prompt = self.render(messages, prefix)
        named: dict[str, Any] = {k: sampling[k] for k in SAMPLING_KEYS if k in sampling}
        if self._server is not None:
            # The server is sent llama-cpp-python's defaults for what the pass
            # leaves unnamed. It stops at a stop string by itself, and says
            # which one: a reply cut short here would leave tokens in its
            # cache that the next prompt does not have.
            body: dict[str, Any] = {"prompt": prompt, **LIBRARY_DEFAULTS, **named}
            if "max_tokens" in body:
                body["n_predict"] = body.pop("max_tokens")
            if seed is not None:
                body["seed"] = seed
            if stop:
                body["stop"] = list(stop)
            yield from self._server.stream(body, reading)
            if reading.finish == "word" and reading.word is None and stop and len(stop) == 1:
                reading.word = stop[0]
            return

        # Only what the caller named is passed on. A pass that sets two of
        # these gets the library's own defaults for the rest, not ours.
        kwargs: dict[str, Any] = dict(named)
        if seed is not None:
            kwargs["seed"] = seed
        stream = self._llm.create_completion(prompt=prompt, stream=True, **kwargs)
        ended = {"limit": False}

        def texts() -> Iterator[str]:
            for chunk in stream:
                choice = chunk["choices"][0]
                if choice.get("finish_reason") == "length":
                    ended["limit"] = True
                if choice.get("text"):
                    yield choice["text"]

        try:
            yield from self._stopped_at(texts(), stop, None, reading)
            if reading.finish == "eos" and ended["limit"]:
                reading.finish = "limit"
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _stopped_at(
        pieces: Iterator[str], stop: list[str] | None, limit: int | None, reading: Reading,
    ) -> Iterator[str]:
        """Text the app reads itself, ended at a stop string or after
        ``limit`` pieces, with how it ended written into ``reading``."""
        stops = Stops(stop)
        count = 0
        for piece in pieces:
            if limit is not None and count >= limit:
                reading.finish = "limit"
                return
            count += 1
            out = stops.feed(piece)
            if out:
                yield out
            if stops.word is not None:
                reading.finish, reading.word = "word", stops.word
                return
        rest = stops.flush()
        if rest:
            yield rest
        reading.finish = "eos"

    async def stream_chat(
        self,
        messages: list[dict],
        sampling: dict,
        request_id: str | None = None,
        *,
        prefix: str = "",
        seed: int | None = None,
        stop: list[str] | None = None,
        held: bool = False,
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield (event, payload) tuples: queue | start | token | done | error.

        One pass through the model, whatever it is for. ``prefix`` is the part
        of the answer already written, which the pass goes on from. ``seed``
        makes the sampling repeatable and ``stop`` ends the output at any of
        the given strings; ``done`` says how it ended (``finish``: word, eos,
        limit or cancelled) and at which string (``word``).

        ``held`` says the run this pass belongs to took the slot with
        ``take_slot`` and is keeping it. The pass then does not wait for the
        slot and does not give it back. If the run is not in fact holding it,
        the pass waits its turn like any other.
        """
        request_id = request_id or uuid.uuid4().hex
        # Two reasons to stop writing, kept apart. `stopped` is a person asking
        # for it: when the caller holds this id open for a whole run it is the
        # run's, and outlives this pass. `gone` is this pass's own: nobody is
        # reading it any more. One pass ending must not stop the next.
        gone = threading.Event()
        # Who hands the slot back. Until a worker thread exists it can only be
        # this coroutine; from then on it is the thread and nobody else.
        acquire: asyncio.Future | None = None
        started = False
        with self.hold(request_id) as stopped:
            try:
                if not self.ready:
                    yield "error", {
                        "message": self.state.error
                        or "The model is still loading. Give it a moment, then send again."
                    }
                    return

                loop = asyncio.get_running_loop()
                queued_at = time.perf_counter()

                # --- wait for the one inference slot, reporting position while
                # we wait. A run that is holding the slot has done its waiting.
                held = held and self._holder == request_id
                if not held:
                    acquire = asyncio.ensure_future(self._lock.acquire())
                    async with aclosing(self._queue(request_id, acquire, stopped)) as waiting:
                        async for event in waiting:
                            yield event

                queue_wait_ms = round((time.perf_counter() - queued_at) * 1000, 1)
                if stopped.is_set():
                    # Stopped before its turn came, or as it came. Nothing was
                    # read and nothing written, and the pass says so. Its place
                    # in the queue, or the slot if it had just won it, is given
                    # up by the block at the end.
                    yield "done", blank(
                        text="", cancelled=True, queue_wait_ms=queue_wait_ms,
                        rss_mb=process_rss_mb(), finish="cancelled", word=None,
                    )
                    return
                self._busy = True

                events: asyncio.Queue = asyncio.Queue()
                SENTINEL = object()

                prompt_tok = self.prompt_tokens(messages, prefix)

                def emit(item) -> None:
                    loop.call_soon_threadsafe(events.put_nowait, item)

                def worker() -> None:
                    t_start = time.perf_counter()
                    t_first: float | None = None
                    produced = 0
                    pieces: list[str] = []
                    reading = Reading()
                    try:
                        for text in self._iter_chunks(
                            messages, sampling, prefix=prefix, seed=seed, stop=stop,
                            reading=reading,
                        ):
                            if stopped.is_set() or gone.is_set():
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
                                    "cancelled": stopped.is_set(),
                                    "ttft_ms": ttft_ms,
                                    "queue_wait_ms": queue_wait_ms,
                                    "prompt_tokens": prompt_tok,
                                    # What the model read of it: all of it, or
                                    # only what its cache did not have.
                                    "read_tokens": reading.read,
                                    "cached_tokens": reading.cached,
                                    "completion_tokens": produced,
                                    # prefill covers the prompt plus the first token
                                    "prefill_tps": round(prompt_tok / max(ttft_ms / 1000, 1e-6), 1),
                                    "gen_tps": round(max(produced - 1, 0) / gen_s, 2),
                                    "total_ms": round((now - t_start) * 1000, 1),
                                    "rss_mb": process_rss_mb(),
                                    "finish": "cancelled" if stopped.is_set() or gone.is_set()
                                    else reading.finish,
                                    "word": reading.word,
                                },
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - surfaced to the client
                        emit(("error", {"message": f"{type(exc).__name__}: {exc}"}))
                    finally:
                        # The thread owns the slot until it has truly left
                        # llama.cpp. It says so before it sends its last event,
                        # so whoever sees the stream end finds the slot where it
                        # belongs: free, or back with the run that is holding it.
                        loop.call_soon_threadsafe(self._worker_left)
                        emit(SENTINEL)

                thread = threading.Thread(target=worker, name=f"gen-{request_id[:8]}", daemon=True)
                thread.start()
                started = True
                self._inside = True

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
                # A thread that is writing is told nobody is reading.
                gone.set()
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

    async def _queue(
        self, request_id: str, acquire: asyncio.Future, stopped: Stop
    ) -> AsyncIterator[tuple[str, dict]]:
        """Wait in line for the slot until ``acquire`` wins it or the request is
        stopped, saying where in the line it stands at every tick."""
        self._waiting.append(request_id)
        first_notice = True
        while not stopped.is_set():
            done, _ = await asyncio.wait({acquire}, timeout=self.QUEUE_TICK)
            if acquire in done or stopped.is_set():
                break
            try:
                ahead = list(self._waiting).index(request_id)
            except ValueError:
                ahead = 0
            yield "queue", {"position": ahead, "first": first_notice}
            first_notice = False
        self._waiting.remove(request_id)

    def _worker_left(self) -> None:
        """Run on the event loop once a worker thread has left the model.

        A pass that took the slot for itself gives it back here. A pass of a
        run that is holding the slot leaves it with the run. And if that run
        let go while its thread was still inside, this is when the slot goes
        back: never before the thread is out.
        """
        self._inside = False
        self._completed += 1
        if self._holder is None:
            self._free()

    def _free(self) -> None:
        self._busy = False
        if self._lock.locked():
            self._lock.release()

    # -- one run, several passes ----------------------------------------------

    async def take_slot(self, request_id: str) -> AsyncIterator[tuple[str, dict]]:
        """Wait for the slot on behalf of a whole run, and keep it.

        A run of several passes that gave the slot up between them would let
        another request in each time, and that request's prompt would replace
        this run's in the model's cache: every pass would then re-read the
        whole conversation. Holding the slot keeps each pass down to reading
        what is new.

        Yields ``("queue", payload)`` while it waits, then one
        ``("slot", {"held": bool, "queue_wait_ms": float})``. It is not held
        if the run was stopped while it waited. Whoever takes the slot gives
        it back with ``give_slot``, whatever happens to the run.
        """
        queued_at = time.perf_counter()
        if self._holder == request_id:
            yield "slot", {"held": True, "queue_wait_ms": 0.0}
            return

        acquire: asyncio.Future | None = None
        taken = False
        with self.hold(request_id) as stopped:
            try:
                acquire = asyncio.ensure_future(self._lock.acquire())
                async with aclosing(self._queue(request_id, acquire, stopped)) as waiting:
                    async for event in waiting:
                        yield event

                waited = round((time.perf_counter() - queued_at) * 1000, 1)
                if stopped.is_set():
                    yield "slot", {"held": False, "queue_wait_ms": waited}
                    return
                self._holder = request_id
                self._busy = True
                taken = True
                yield "slot", {"held": True, "queue_wait_ms": waited}
            finally:
                if request_id in self._waiting:
                    self._waiting.remove(request_id)
                if acquire is not None and not taken:
                    # The wait is called off; or it was won at the last moment
                    # and is handed straight back.
                    won = not acquire.cancel() and not acquire.cancelled() and acquire.exception() is None
                    if won:
                        self._free()

    def give_slot(self, request_id: str) -> None:
        """Hand back a slot taken with ``take_slot``. Does nothing if this run
        is not holding it, so it is safe to call on every way out of a run.

        If one of the run's passes is still inside the model, the slot is not
        freed now. It goes back when that thread leaves.
        """
        if self._holder != request_id:
            return
        self._holder = None
        if not self._inside:
            self._free()

    def others_waiting(self) -> bool:
        """Whether anybody is queued for the slot."""
        return bool(self._waiting)


engine = Engine()
