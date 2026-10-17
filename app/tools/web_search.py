"""web_search: what the web says now, from a search engine, with no API key.

Two engines, chosen by ``AURORA_SEARCH``:

* **SearXNG**, run by this server for itself on loopback (``AURORA_SEARX_URL``,
  see deploy/searxng). It asks several engines at once and answers in JSON.
* **DuckDuckGo's HTML page**, where there is no SearXNG: no setup at all, and
  no promise either, since a page meant for a browser can change or refuse.

The address searched is the operator's, never the model's: the model gives a
query and nothing else. What comes back is a numbered list of pages, each of
which the model may then ask to open (``open_result``) by its number.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import settings
from . import results
from .base import Arg, Tool, ToolResult, register

MAX_RESULTS = 5
MAX_BYTES = 1_000_000
DUCKDUCKGO = "https://html.duckduckgo.com/html/"


def engine() -> str | None:
    """The engine the configuration picks, or None for none."""
    choice = (settings.search or "auto").lower()
    if choice == "off":
        return None
    if choice == "searxng":
        return "searxng" if settings.searx_url else None
    if choice == "duckduckgo":
        return "duckduckgo"
    return "searxng" if settings.searx_url else "duckduckgo"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.tool_timeout),
        headers={"User-Agent": settings.user_agent, "Accept-Language": "en"},
        follow_redirects=True,
        max_redirects=3,
    )


async def _body(response: httpx.Response) -> bytes:
    body = b""
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) >= MAX_BYTES:
            break
    return body


# --------------------------------------------------------------- SearXNG ---


def read_searx(payload: dict) -> tuple[list[dict], str]:
    """SearXNG's JSON as entries, and the engine's own short answer, if it
    gave one (an infobox, or a line under "answers")."""
    entries = []
    for raw in payload.get("results") or []:
        url = raw.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        entries.append(results.item(
            raw.get("title") or url, url,
            published=results.when(raw.get("publishedDate")),
            snippet=raw.get("content") or "",
        ))
        if len(entries) == MAX_RESULTS:
            break
    said = []
    for answer in payload.get("answers") or []:
        said.append(answer.get("answer", "") if isinstance(answer, dict) else str(answer))
    for box in payload.get("infoboxes") or []:
        if box.get("content"):
            said.append(f"{box.get('infobox') or ''}: {box['content']}".strip(": "))
    return entries, " ".join(s for s in said if s)[:400]


async def _searx(query: str) -> tuple[list[dict], str]:
    async with _client() as client:
        res = await client.get(
            settings.searx_url.rstrip("/") + "/search",
            params={"q": query, "format": "json", "safesearch": 1},
        )
        res.raise_for_status()
        return read_searx(res.json())


# ------------------------------------------------------------ DuckDuckGo ---


class _DuckReader(HTMLParser):
    """The results on DuckDuckGo's HTML page: each result's link, its title,
    and its snippet. An advert's link goes through a tracker and is left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict] = []
        self._field: str | None = None
        self._text: list[str] = []
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if "result__a" in classes:
            self._field, self._href, self._text = "title", attrs.get("href") or "", []
        elif "result__snippet" in classes:
            self._field, self._text = "snippet", []

    def handle_data(self, data):
        if self._field:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or not self._field:
            return
        text = " ".join("".join(self._text).split())
        if self._field == "title":
            url = unwrap(self._href)
            if url:
                self.entries.append({"title": text, "url": url, "snippet": ""})
        elif self.entries and not self.entries[-1]["snippet"]:
            self.entries[-1]["snippet"] = text
        self._field = None


def unwrap(href: str) -> str | None:
    """The page a DuckDuckGo result points to. Its links go through its own
    redirect, with the page in ``uddg``; an advert's go through ``y.js``."""
    link = href if "://" in href else "https:" + href if href.startswith("//") else href
    parts = urlparse(link)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com"):
        if parts.path.startswith("/y.js"):
            return None
        target = parse_qs(parts.query).get("uddg", [""])[0]
        return target if target.startswith(("http://", "https://")) else None
    return link if link.startswith(("http://", "https://")) else None


def read_duckduckgo(html: str) -> list[dict]:
    reader = _DuckReader()
    try:
        reader.feed(html)
    except Exception:  # noqa: BLE001 - a page meant for a browser may be anything
        pass
    return [
        results.item(e["title"], e["url"], snippet=e["snippet"])
        for e in reader.entries[:MAX_RESULTS]
    ]


async def _duckduckgo(query: str) -> tuple[list[dict], str]:
    async with _client() as client:
        async with client.stream("POST", DUCKDUCKGO, data={"q": query}) as res:
            # A refusal comes as a page of its own, often with a 202.
            if res.status_code != 200:
                raise httpx.HTTPStatusError("refused", request=res.request, response=res)
            body = await _body(res)
    return read_duckduckgo(body.decode("utf-8", errors="replace")), ""


# ------------------------------------------------------------------ tool ---


async def _web_search(query: str) -> ToolResult:
    q = " ".join((query or "").split())
    if not q:
        return ToolResult(False, "There is nothing to search for.", error="empty")
    which = engine()
    if which is None:
        return ToolResult(False, "Searching the web is switched off here.", error="off")
    try:
        entries, said = await (_searx(q) if which == "searxng" else _duckduckgo(q))
    except (httpx.HTTPError, ValueError):
        return ToolResult(
            False, "The search engine did not answer. Try again in a moment.",
            error=f"{which} unreachable",
        )
    if not entries and not said:
        return ToolResult(False, f"The search for “{q}” found nothing.", error="no results")
    lead = f"Search “{q}”" + (f": {said}." if said else ":")
    return ToolResult(
        True, results.summary(lead, entries), card="search",
        data={"query": q, "engine": which, "answer": said, "items": entries},
    )


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    """The results as the model reads them: the engine's own answer, if it
    gave one, then each page numbered, with as much of its snippet as fits."""
    data = result.data
    if not data.get("items"):
        return result.summary
    said = data.get("answer") or ""
    head = f"Answer: {said}\n" if said else ""
    return head + results.numbered(data.get("items") or [], budget - len(head) // 3)


register(Tool(
    name="web_search",
    label="Web search",
    glyph="⌕",
    description="Search the web for anything current or not in your knowledge: events, prices, people, releases.",
    args=(Arg("query", "string", True, "what to search for", max_len=100),),
    run=_web_search,
    card="search",
    network=True,
    example="/search latest Python release",
    budget=300,
    observe=_observe,
    fresh_for=6 * 3600,
    untrusted=True,
    links=True,
    requires=lambda: engine() is not None,
))
