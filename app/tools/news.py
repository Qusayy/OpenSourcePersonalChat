"""news: the headlines now, from Google News, with no API key.

The top stories of an edition (``AURORA_NEWS_REGION``), or the news about a
topic from the last few days. Each headline names its publisher and when it
was published; the model reads them numbered, and may ask for one to be
opened (``open_result``), which goes through a web search for the story,
since Google News's own links lead to a page that only a browser can follow.

The feed is XML from the open web. It is read with a cap on its size, and a
feed that declares a DOCTYPE is refused outright, so no entity in it is ever
expanded.
"""

from __future__ import annotations

import dataclasses
import time
import xml.etree.ElementTree as ET

import httpx

from ..config import settings
from . import results
from .base import Arg, Tool, ToolResult, register

FEED = "https://news.google.com/rss"
MAX_BYTES = 1_000_000
MAX_ITEMS = 8
# How recent the news about a topic is, in Google News's own words.
RECENT = "when:3d"
# A feed fetched this recently is not fetched again.
CACHE_SECONDS = 300

_cache: dict[tuple[str, str], tuple[float, ToolResult]] = {}


def edition(region: str) -> dict[str, str]:
    """The query string that picks an edition: "GB:en" is British English."""
    country, _, language = (region or "US:en").partition(":")
    country, language = country.upper() or "US", (language or "en").lower()
    return {"hl": f"{language}-{country}", "gl": country, "ceid": f"{country}:{language}"}


def read_feed(xml: bytes) -> list[dict]:
    """A Google News RSS feed as entries, newest first. A headline's own
    " - Publisher" ending is taken off, since the publisher is kept apart."""
    if b"<!DOCTYPE" in xml[:2048].upper():
        raise ValueError("a feed with a DOCTYPE is not read")
    root = ET.fromstring(xml)
    entries = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        url = (node.findtext("link") or "").strip()
        source = node.find("source")
        publisher = (source.text or "").strip() if source is not None else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        if not title or not url.startswith(("http://", "https://")):
            continue
        entry = results.item(
            title, url, source=publisher,
            published=results.when(node.findtext("pubDate")),
        )
        if source is not None and source.get("url"):
            entry["site"] = results.domain(source.get("url"))
        entries.append(entry)
    entries.sort(key=lambda e: e["published"] or "", reverse=True)
    return entries[:MAX_ITEMS]


async def _news(topic: str = "") -> ToolResult:
    topic = " ".join((topic or "").split())
    key = (topic.casefold(), settings.news_region)
    kept = _cache.get(key)
    if kept and time.monotonic() - kept[0] < CACHE_SECONDS:
        # A copy: whoever calls a tool writes its timing onto what it returns.
        return dataclasses.replace(kept[1])
    url = f"{FEED}/search" if topic else FEED
    params = {**({"q": f"{topic} {RECENT}"} if topic else {}), **edition(settings.news_region)}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.tool_timeout),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True, max_redirects=3,
        ) as client:
            async with client.stream("GET", url, params=params) as res:
                res.raise_for_status()
                body = b""
                async for chunk in res.aiter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
        entries = read_feed(body)
    except (httpx.HTTPError, ET.ParseError, ValueError):
        return ToolResult(False, "The news service did not answer. Try again in a moment.",
                          error="unreachable")
    if not entries:
        about = f" about {topic}" if topic else ""
        return ToolResult(False, f"There is no news{about} from the last few days.",
                          error="no news")
    lead = f"Headlines about {topic}:" if topic else "Top headlines:"
    result = ToolResult(
        True, results.summary(lead, entries), card="news",
        data={"topic": topic, "region": settings.news_region, "items": entries},
    )
    _cache[key] = (time.monotonic(), dataclasses.replace(result))
    return result


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    """The headlines numbered, each with its publisher and how old it is."""
    items = result.data.get("items") or []
    return results.numbered(items, budget) if items else result.summary


register(Tool(
    name="news",
    label="News",
    glyph="▤",
    description="Today's headlines, or the latest news about a topic.",
    args=(Arg("topic", "string", False, "what the news is about; leave out for the top stories",
              default="", max_len=60),),
    run=_news,
    card="news",
    network=True,
    example="/news",
    budget=300,
    observe=_observe,
    fresh_for=30 * 60,
    untrusted=True,
    links=True,
))
