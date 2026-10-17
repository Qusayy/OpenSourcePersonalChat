"""wikipedia: search, then the summary of the page found. Keyless."""

from __future__ import annotations

from ..text import estimate, excerpt
from .base import Arg, Tool, ToolResult, register
from .client import api_client


async def _wikipedia(query: str) -> ToolResult:
    q = (query or "").strip()
    if not q:
        return ToolResult(False, "Nothing to look up.", error="empty")

    async with api_client() as client:
        search = await client.get(
            "https://en.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": q, "limit": 1},
        )
        if search.status_code == 403:
            # Wikimedia blocks anonymous-looking clients. The visitor cannot
            # act on that; the operator can, so the fix lives in the step label
            # and the logs rather than in the sentence they read.
            return ToolResult(
                False,
                "Wikipedia turned this server away. Lookups are unavailable "
                "until the server operator sets a contact address.",
                error="wikipedia 403 — set AURORA_USER_AGENT with a contact URL",
            )
        search.raise_for_status()
        pages = (search.json() or {}).get("pages") or []
        if not pages:
            return ToolResult(
                False,
                f"Wikipedia has no article for {q!r}. Try a different spelling "
                "or a broader term.",
                error="no article",
            )

        key = pages[0].get("key") or pages[0].get("title", "").replace(" ", "_")
        summary = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{key}")
        summary.raise_for_status()
        page = summary.json()

    extract = (page.get("extract") or "").strip()
    title = page.get("title") or q
    url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page") or (
        f"https://en.wikipedia.org/wiki/{key}"
    )
    # The model gets a trimmed extract; the card keeps the whole thing.
    short = extract if len(extract) <= 700 else extract[:700].rsplit(" ", 1)[0] + "…"

    return ToolResult(
        True,
        f"Wikipedia — {title}: {short}",
        card="wikipedia",
        data={
            "title": title,
            "description": page.get("description") or "",
            "extract": extract,
            "url": url,
            "lang": page.get("lang", "en"),
        },
    )


def _observe_article(result: ToolResult, purpose: str, budget: int) -> str:
    """An article as the model reads it: its title, then the sentences of
    its summary that bear most on the question. A result without the
    article's text is read by its own summary."""
    data = result.data
    if not data.get("extract"):
        return result.summary
    head = f"Wikipedia — {data.get('title') or ''}: "
    return head + excerpt(data["extract"], budget - estimate(head), purpose)


register(Tool(
    name="wikipedia",
    label="Wikipedia",
    glyph="◍",
    description="Look up a factual summary of a person, place, thing or event.",
    args=(Arg("query", "string", True, "what to look up", max_len=80),),
    run=_wikipedia,
    card="wikipedia",
    network=True,
    example="/wiki Ada Lovelace",
    budget=175,
    observe=_observe_article,
    untrusted=True,
))
