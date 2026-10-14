"""The model in another process: a client for llama-server.

The engine can hold the model itself, through llama-cpp-python, or hand every
pass to a llama-server running beside the app (ik_llama.cpp's or mainline
llama.cpp's), chosen by ``AURORA_LLM_URL``. Measured on the 4-core server this
site runs on, ik_llama.cpp's server writes about half as fast again with the
same model file, and it keeps a conversation's prompt cached for models whose
recurrent layers llama-cpp-python has to re-read on every turn.

Everything that decides who runs when stays in the engine: the one slot, the
queue, Stop. This module only speaks HTTP, and only on the engine's worker
thread, where a pass has always been read one piece at a time. That is why it
is synchronous.

Closing a stream early closes its connection, and llama-server stops writing
when the reader goes away; that is how Stop reaches it.

Every failure is a ``ServerError`` whose message can be shown to a visitor: it
says what went wrong and never where the server is.

The last piece of a completed answer says what the server did with the prompt:
how much of it it read and how much it took from its cache. That is kept, as
far as the server reports it, and nothing is guessed where it does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlsplit

import httpx

# Reaching a server on this machine takes milliseconds; one that does not
# answer in two seconds is not there.
CONNECT_TIMEOUT = 2.0
# The longest silence in a healthy pass is reading the prompt before the first
# token: a full 4,096-token context at ~60 tokens a second is about 70 s.
READ_TIMEOUT = 300.0
HEALTH_TIMEOUT = 2.0


class ServerError(RuntimeError):
    """The model server failed or could not be reached."""


@dataclass
class Reading:
    """What the server did with one prompt, as it reported it at the end of
    the answer. A figure it did not report stays None; so does every figure of
    an answer that was stopped before its end."""

    # The whole prompt, as the server's own template and tokenizer made it.
    prompt: int | None = None
    # The part it had to read; the rest it took from its cache.
    read: int | None = None
    cached: int | None = None

    def take(self, chunk: dict) -> None:
        """Keep what one piece of the answer says about the prompt.

        llama-server puts ``timings`` on its last piece (``prompt_n`` read,
        ``cache_n`` taken from the cache) and, depending on its version,
        ``usage`` there or on a piece of its own.
        """
        timings = chunk.get("timings")
        if isinstance(timings, dict):
            self.read = _count(timings.get("prompt_n"), self.read)
            self.cached = _count(timings.get("cache_n"), self.cached)
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.prompt = _count(usage.get("prompt_tokens"), self.prompt)
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                self.cached = _count(details.get("cached_tokens"), self.cached)

    def settle(self) -> None:
        """Fill in the one figure the other two give, once the answer is over:
        the prompt is what was read and what was cached, together."""
        if self.read is None:
            return
        if self.cached is None and self.prompt is not None and self.prompt >= self.read:
            self.cached = self.prompt - self.read
        elif self.prompt is None and self.cached is not None:
            self.prompt = self.read + self.cached


def _count(value: Any, current: int | None) -> int | None:
    """A token count the server sent, or what was known before if it is not
    one. llama-server reports -1 for a figure it does not have."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return current


class LlamaServer:
    def __init__(self, url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.url = url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.url,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            transport=transport,
        )

    @property
    def port(self) -> int | None:
        parts = urlsplit(self.url)
        return parts.port or {"http": 80, "https": 443}.get(parts.scheme)

    def ready(self) -> bool:
        """Whether the server has its model loaded. It answers 503 while it
        is still reading the weights, and 200 once it can take a request,
        including while it is busy with one."""
        try:
            response = self._client.get("/health", timeout=HEALTH_TIMEOUT)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def tokenize(self, text: str) -> int:
        """How many tokens the server's model makes of ``text``."""
        try:
            response = self._client.post("/tokenize", json={"content": text, "add_special": False})
        except httpx.HTTPError as exc:
            raise ServerError("the model server is not answering") from exc
        if response.status_code != 200:
            raise ServerError(f"the model server answered {response.status_code}")
        try:
            return len(response.json()["tokens"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ServerError("the model server sent a token count it could not be read from") from exc

    def stream(self, body: dict, reading: Reading | None = None) -> Iterator[str]:
        """One chat completion, as the pieces of text it is written in.

        ``body`` is the request: the messages and the sampling settings. The
        prompt is always offered to the server's cache, so a conversation's
        next turn reads only what is new. ``reading``, if given, is filled in
        with what the server says it read, once the answer has ended.
        """
        payload = {**body, "stream": True, "cache_prompt": True}
        try:
            with self._client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    raise ServerError(f"the model server answered {response.status_code}")
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError as exc:
                        raise ServerError("the model server sent something that is not JSON") from exc
                    if chunk.get("error"):
                        raise ServerError("the model server failed while writing")
                    if reading is not None:
                        reading.take(chunk)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    text = (choices[0].get("delta") or {}).get("content")
                    if text:
                        yield text
        except httpx.HTTPError as exc:
            raise ServerError("the model server is not answering") from exc
        if reading is not None:
            reading.settle()
