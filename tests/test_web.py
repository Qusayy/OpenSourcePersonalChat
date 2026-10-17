"""Serving the built front-end: the page, its first values, its files, its 404s.

Needs the app's own requirements (FastAPI, httpx for the test client); without
them the whole module is skipped. It does not need Node or a build: the tests
make a small `dist` of their own.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from app import main
    from app.api import frontend, pages
    from app.llm import Engine
    from app.store import db
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

REPO = Path(__file__).resolve().parent.parent

INDEX = f"""<!doctype html>
{frontend.MARK_PAGE}
<head>
{frontend.MARK_DESCRIPTION}
{frontend.MARK_TITLE}
<script type="module" src="/assets/index-abc123.js"></script>
</head>
<body><div id="root"></div>
{frontend.MARK_BOOTSTRAP}
</body>
</html>
"""


def make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "assets" / "face-abc123.woff2").write_bytes(b"wOF2")
    (root / "secret.txt").write_text("not for serving", encoding="utf-8")
    return dist


def embedded(html_text: str) -> dict:
    """The bootstrap a page carries, read back the way the browser reads it."""
    found = re.search(r'<script type="application/json" id="bootstrap">(.*?)</script>', html_text, re.S)
    assert found, "the page has no bootstrap element"
    return json.loads(found.group(1))


class ScriptJsonTests(unittest.TestCase):
    def test_a_closing_script_tag_cannot_end_the_element(self) -> None:
        out = frontend.script_json({"title": "</script><script>alert(1)</script>"})
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        self.assertEqual(json.loads(out)["title"], "</script><script>alert(1)</script>")

    def test_comment_openers_and_ampersands_are_escaped(self) -> None:
        out = frontend.script_json({"a": "<!-- & -->"})
        self.assertNotIn("<!--", out)
        self.assertNotIn("&", out)
        self.assertEqual(json.loads(out)["a"], "<!-- & -->")

    def test_line_separators_are_escaped_and_survive(self) -> None:
        text = "one\u2028two\u2029three"
        out = frontend.script_json({"t": text})
        self.assertNotIn("\u2028", out)
        self.assertNotIn("\u2029", out)
        self.assertEqual(json.loads(out)["t"], text)

    def test_other_text_is_left_readable(self) -> None:
        self.assertEqual(frontend.script_json({"name": "Zoë — 東京"}), '{"name":"Zoë — 東京"}')


class SourceMarkerTests(unittest.TestCase):
    """The server fills in four strings in index.html. They must be there."""

    def test_the_source_page_has_every_marker(self) -> None:
        source = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        for name in ("MARK_BOOTSTRAP", "MARK_TITLE", "MARK_DESCRIPTION", "MARK_PAGE"):
            with self.subTest(marker=name):
                self.assertEqual(source.count(getattr(frontend, name)), 1)

    def test_the_notice_for_no_scripts_is_inside_the_sheet(self) -> None:
        # <body> is a three-row grid. As a child of <body> the notice would be
        # a fourth row, under the status line; inside <main> it is on the sheet.
        source = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(source.count("<noscript>"), 1)
        self.assertIn("<main><noscript>", source)


class FrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.front = frontend.Frontend(make_dist(self.root))

    def page(self, **over) -> str:
        args = dict(page="chat", title="Aurora", description="d", bootstrap={"ok": True})
        args.update(over)
        return self.front.page(**args).body.decode("utf-8")

    def test_the_page_carries_its_first_values(self) -> None:
        body = self.page(bootstrap={"version": "9.9.9", "n": [1, 2]})
        self.assertEqual(embedded(body), {"version": "9.9.9", "n": [1, 2]})

    def test_title_description_and_page_are_set_and_escaped(self) -> None:
        body = self.page(page="bench", title="Benchmark — <Q&A>", description='a "quoted" <thing>')
        self.assertIn("<title>Benchmark — &lt;Q&amp;A&gt;</title>", body)
        self.assertIn('content="a &quot;quoted&quot; &lt;thing&gt;"', body)
        self.assertIn('<html lang="en" data-page="bench">', body)

    def test_the_page_is_never_cached_and_its_files_always_are(self) -> None:
        page = self.front.page(page="chat", title="t", description="d", bootstrap={})
        self.assertEqual(page.headers["cache-control"], "no-cache")
        file = self.front.asset("index-abc123.js")
        self.assertEqual(file.status_code, 200)
        self.assertEqual(file.headers["cache-control"], frontend.CACHE_FOREVER)

    def test_the_typeface_is_sent_as_a_font(self) -> None:
        self.assertEqual(self.front.asset("face-abc123.woff2").media_type, "font/woff2")

    def test_a_missing_file_is_a_plain_404_not_the_page(self) -> None:
        res = self.front.asset("index-old999.js")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(res.headers["content-type"].startswith("text/plain"))

    def test_a_path_cannot_climb_out_of_assets(self) -> None:
        for name in ("../index.html", "../../secret.txt", "..\\..\\secret.txt", "/etc/passwd", "C:/Windows/win.ini"):
            with self.subTest(name=name):
                self.assertEqual(self.front.asset(name).status_code, 404)

    def test_a_directory_is_not_a_file(self) -> None:
        self.assertEqual(self.front.asset("").status_code, 404)

    def test_without_a_build_the_page_says_so(self) -> None:
        front = frontend.Frontend(self.root / "nowhere")
        self.assertFalse(front.built())
        res = front.page(page="chat", title="t", description="d", bootstrap={})
        self.assertEqual(res.status_code, 503)
        self.assertIn("has not been built", res.body.decode("utf-8"))
        self.assertEqual(front.asset("index-abc123.js").status_code, 404)

    def test_a_build_that_appears_later_is_picked_up(self) -> None:
        front = frontend.Frontend(self.root / "later" / "dist")
        self.assertEqual(front.page(page="chat", title="t", description="d", bootstrap={}).status_code, 503)
        make_dist(self.root / "later")
        self.assertEqual(front.page(page="chat", title="t", description="d", bootstrap={}).status_code, 200)


class AppRoutingTests(unittest.TestCase):
    """The routes, through the real app, against a temporary build and database."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)

        settings = SimpleNamespace(mock=True, resolved_db_path=root / "web.db")
        patcher = mock.patch.object(db, "settings", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init()
        self.addCleanup(db.close)

        patcher = mock.patch.object(pages, "frontend", frontend.Frontend(make_dist(root)))
        patcher.start()
        self.addCleanup(patcher.stop)

        # No `with`: the lifespan would open the real database and start
        # loading the model. The routes under test need neither.
        self.client = TestClient(main.app)

    def test_each_route_gets_the_page_with_its_own_title(self) -> None:
        for path, page, start in (("/", "chat", "Aurora — "), ("/bench", "bench", "Benchmark — "), ("/about", "about", "About — Aurora")):
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200)
                self.assertIn(f'data-page="{page}"', res.text)
                self.assertIn(f"<title>{start}", res.text)
                self.assertEqual(res.headers["cache-control"], "no-cache")

    def test_the_bootstrap_has_what_the_page_reads(self) -> None:
        data = embedded(self.client.get("/").text)
        self.assertEqual(
            set(data),
            {"version", "info", "system", "personas", "defaultPersona", "skills", "tools", "conversations", "bench"},
        )
        self.assertEqual(set(data["bench"]), {"cases", "reference", "budget", "larger", "latest"})
        # The dev server's page asks for the same thing over the API.
        api = self.client.get("/api/bootstrap").json()
        self.assertEqual(set(api), set(data))
        for key in ("version", "personas", "defaultPersona", "skills", "tools", "conversations"):
            with self.subTest(key=key):
                self.assertEqual(api[key], data[key])

    def test_the_bootstrap_carries_no_prompts_and_no_paths(self) -> None:
        text = self.client.get("/").text
        data = embedded(text)
        for persona in data["personas"]:
            self.assertEqual(set(persona), {"id", "name", "blurb"})
        self.assertNotIn(str(REPO), text)
        self.assertNotIn(str(REPO).replace("\\", "\\\\"), text)
        self.assertNotIn(".gguf", text)

    def test_an_unknown_page_is_the_chat_page_with_a_404(self) -> None:
        res = self.client.get("/no/such/page")
        self.assertEqual(res.status_code, 404)
        self.assertIn('data-page="chat"', res.text)
        self.assertIn('id="bootstrap"', res.text)

    def test_an_unknown_api_path_is_json(self) -> None:
        res = self.client.get("/api/no-such-thing")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json(), {"error": "not found"})

    def test_a_missing_file_is_never_answered_with_the_page(self) -> None:
        for path in ("/assets/index-old999.js", "/assets/", "/favicon.ico", "/static/js/app.js", "/robots.txt"):
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 404)
                self.assertNotIn("<html", res.text)

    def test_a_built_file_is_served_for_good(self) -> None:
        res = self.client.get("/assets/index-abc123.js")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["cache-control"], frontend.CACHE_FOREVER)

    def test_benchmark_results_include_what_the_template_used_to_print(self) -> None:
        data = self.client.get("/api/bench/results").json()
        for key in ("cases", "reference", "budget", "larger", "latest", "history", "info"):
            with self.subTest(key=key):
                self.assertIn(key, data)


class PublicErrorTests(unittest.TestCase):
    """What a visitor is told when the model is not there."""

    def test_a_missing_model_is_reported_without_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private" / "weights.gguf"
            cfg = SimpleNamespace(mock=False, resolved_model_path=path)
            llm = Engine.__new__(Engine)
            llm.cfg = cfg
            llm.state = SimpleNamespace(status="cold", error=None)
            with self.assertLogs("aurora.llm", level="ERROR") as logs:
                llm.load_blocking()
            self.assertEqual(llm.state.status, "error")
            self.assertNotIn("private", llm.state.error)
            self.assertNotIn("weights.gguf", llm.state.error)
            self.assertIn("weights.gguf", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
