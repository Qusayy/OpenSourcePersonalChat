"""Results that are lists of pages: search results and headlines.

A list is kept as items, each with a title, an address, where it is from and
when, and the model reads it numbered, so that it can ask for one of them by
its number (``open_result``) without ever writing an address itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from ..text import clip, estimate
from .base import Link


def domain(url: str) -> str:
    """The site an address is on, without "www."."""
    host = urlparse(url or "").hostname or ""
    return host.removeprefix("www.")


def when(raw: str | None) -> datetime | None:
    """A date as feeds and search engines write one (ISO 8601 or RFC 822), or
    None for one that cannot be read."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def ago(moment: datetime | None, now: datetime | None = None) -> str:
    """How long ago, as a person says it: "40 min ago", "5 h ago", "3 days ago",
    or the date for anything older than a week. Empty for no date."""
    if moment is None:
        return ""
    now = now or datetime.now(timezone.utc)
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        return moment.strftime("%d %b %Y")
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min ago"
    if seconds < 86400:
        return f"{round(seconds / 3600)} h ago"
    if seconds < 7 * 86400:
        days = round(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    return moment.strftime("%d %b %Y")


def item(title: str, url: str, *, source: str = "", published: datetime | None = None,
         snippet: str = "") -> dict:
    """One entry of a list as it is kept and drawn."""
    return {
        "title": " ".join((title or "").split()),
        "url": url,
        "source": source or domain(url),
        "published": published.isoformat() if published else None,
        "age": ago(published),
        "snippet": " ".join((snippet or "").split()),
    }


def links_of(data: dict) -> tuple[Link, ...]:
    """A list result's entries as the pages the model may open, in order."""
    return tuple(
        Link(e["url"], e.get("title") or "", e.get("site") or e.get("source") or "")
        for e in data.get("items") or [] if isinstance(e, dict) and e.get("url")
    )


def headline(entry: dict, number: int) -> str:
    """An entry as one numbered line, without its snippet."""
    where = ", ".join(part for part in (entry.get("source"), entry.get("age")) if part)
    return f"{number}. {entry.get('title') or entry.get('url')}" + (f" ({where})" if where else "")


def summary(lead: str, items: list[dict]) -> str:
    """The list in a line or two: what it is, then each entry's headline."""
    return lead + " " + "; ".join(headline(e, i) for i, e in enumerate(items, 1))


def numbered(items: list[dict], budget: int) -> str:
    """The list as the model reads it within ``budget`` tokens: one numbered
    line to an entry, its snippet cut to an even share of what is left."""
    heads = [headline(e, i) for i, e in enumerate(items, 1)]
    left = budget - sum(estimate(h) + 1 for h in heads)
    share = max(0, left // max(1, len(items)))
    lines = []
    for head, entry in zip(heads, items):
        snippet = entry.get("snippet") or ""
        lines.append(head + (": " + clip(snippet, share) if snippet and share > 8 else ""))
    return "\n".join(lines)
