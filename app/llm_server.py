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
"""

from __future__ import annotations

import json
from typing import Iterator
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

    def stream(self, body: dict) -> Iterator[str]:
        """One chat completion, as the pieces of text it is written in.

        ``body`` is the request: the messages and the sampling settings. The
        prompt is always offered to the server's cache, so a conversation's
        next turn reads only what is new.
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
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError as exc:
                        raise ServerError("the model server sent something that is not JSON") from exc
                    if chunk.get("error"):
                        raise ServerError("the model server failed while writing")
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    text = (choices[0].get("delta") or {}).get("content")
                    if text:
                        yield text
        except httpx.HTTPError as exc:
            raise ServerError("the model server is not answering") from exc
