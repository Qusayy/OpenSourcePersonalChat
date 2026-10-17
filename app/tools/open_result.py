"""open_result: read the page behind one of the results the model was shown.

The model asks for a result by its number in the last list it read, and the
harness finds the address; the model never writes one. So text on a page
that says "now open http://elsewhere/?q=…" cannot send the reader anywhere:
the only addresses this opens are the ones a search or the news gave, and the
reader's own guard (``read_url``) still checks every one of them.

A Google News headline links to a page only a browser can follow, so it is
opened through a web search for the headline, taking the result from the
headline's own publisher where there is one.
"""

from __future__ import annotations

from . import results, web_search
from .base import CALL, Arg, Link, Tool, ToolResult, register
from .read_url import _observe as read_page
from .read_url import _read_url

NUMBERS = tuple(str(n) for n in range(1, 9))
GOOGLE_NEWS = "news.google.com"


async def _publisher_page(link: Link) -> str | None:
    """Where a Google News headline's story is, found by searching for it."""
    if web_search.engine() is None:
        return None
    found = await web_search._web_search(link.title)  # noqa: SLF001 - the same search, without a step
    entries = (found.data.get("items") or []) if found.ok else []
    # The headline's publisher, as a site ("reuters.com") where the feed named one.
    site = (link.source or "").lower()
    for entry in entries:
        if "." in site and results.domain(entry["url"]).endswith(site):
            return entry["url"]
    return entries[0]["url"] if entries else None


async def _open_result(number: str) -> ToolResult:
    links = CALL.get().links
    if not links:
        return ToolResult(False, "There is no list of results to open one from.",
                          error="no results")
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= len(links):
        return ToolResult(False, f"There is no result {number}: the list had {len(links)}.",
                          error="no such result")
    link = links[n - 1]
    url = link.url
    if results.domain(url) == GOOGLE_NEWS:
        url = await _publisher_page(link)
        if url is None:
            return ToolResult(
                False, "That headline links through Google News, and its story could not be found.",
                error="not followed",
            )
    return await _read_url(url)


register(Tool(
    name="open_result",
    label="Open result",
    glyph="⧉",
    description="Read the full page of one of the numbered results above.",
    args=(Arg("number", "string", True, "the result's number", choices=NUMBERS),),
    run=_open_result,
    card="url",
    network=True,
    example="/open 1",
    budget=250,
    observe=read_page,
    untrusted=True,
))
