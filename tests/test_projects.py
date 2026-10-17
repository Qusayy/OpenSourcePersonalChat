"""Code projects: the files an answer writes, kept as they close, listed for
the next message, and downloaded as a zip.

The cases of what counts as a file are shared with the page, which reads
answers the same way: web/src/lib/__fixtures__/file-blocks.json.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path

try:
    from app import agent
    from app.agent.style import STYLES
    from app.agent.verify import check
    from app.projects import paths
    from app.projects.parse import GRAMMAR, FileBlocks, files_in
    from app.store import projects, runs
    from app.tools import REGISTRY
    from app.tools.base import CALL, CallContext, call_tool
    from evals.scripted import ScriptedEngine
    from tests import support
    from tests.support import ChatCase, PlayedEngine, collect, only
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

CASES = json.loads(
    (Path(__file__).resolve().parent.parent / "web" / "src" / "lib" / "__fixtures__" / "file-blocks.json")
    .read_text(encoding="utf-8")
)

ANSWER = (
    "Here is the app.\n\n### app.py\n```python\nfrom flask import Flask\napp = Flask(__name__)\n```\n\n"
    "### templates/index.html\n```html\n<h1>Todo</h1>\n```\n\nRun it with `flask run`."
)


class GrammarTests(unittest.TestCase):
    """What an answer the model chose to write as files is held to."""

    def setUp(self) -> None:
        from tests.gbnf import Grammar

        self.grammar = Grammar(GRAMMAR)

    def test_files_with_a_line_before_between_and_after_are_allowed(self) -> None:
        from app.llm.mock import MOCK_PROJECT

        for answer in (
            ANSWER, MOCK_PROJECT, "### a.py\n```python\nprint(1)\n```\n",
            "### a.py\n```python\ndef f():\n\n    return 1\n```\n\nNow its test:\n"
            "### test_a.py\n```python\nimport a\n```\n\nRun it.\n\nThen open it.",
        ):
            with self.subTest(answer[:30]):
                self.assertTrue(self.grammar.accepts(answer))
                self.assertTrue(files_in(answer))

    def test_what_a_small_model_wrote_instead_of_files_is_not(self) -> None:
        for answer in (
            # What the live site was given: the whole answer in one fence, and
            # a description under each path.
            "```text\n### app/main.py\nThis is the main application file.\n```\n",
            "### app/main.py\nThis is the main application file.\n",
            "Here:\n\n```python\nprint(1)\n```\n",
            "What should the project do?",
            "### app.py (the main file)\n```python\nx\n```\n",
        ):
            with self.subTest(answer[:30]):
                self.assertFalse(self.grammar.accepts(answer))


class ReadingTests(unittest.TestCase):
    def test_every_shared_case_reads_as_the_page_reads_it(self) -> None:
        for case in CASES:
            with self.subTest(case["name"]):
                found = [{k: f[k] for k in ("path", "lang", "content")} for f in files_in(case["answer"])]
                self.assertEqual(found, case["files"])

    def test_the_same_files_are_found_however_the_answer_arrives(self) -> None:
        for case in CASES:
            with self.subTest(case["name"]):
                reader, events = FileBlocks(), []
                for ch in case["answer"]:
                    events += reader.feed(ch)
                events += reader.finish()
                self.assertEqual(
                    [e["content"] for e in events if e["type"] == "close"],
                    [f["content"] for f in case["files"]],
                )

    def test_a_file_opens_then_arrives_line_by_line_then_closes(self) -> None:
        reader = FileBlocks()
        events = reader.feed("### a.py\n```python\nx = 1\n") + reader.feed("y = 2\n```\n")
        self.assertEqual([e["type"] for e in events], ["open", "line", "line", "close"])
        self.assertEqual([e.get("text") for e in events if e["type"] == "line"], ["x = 1\n", "y = 2\n"])


class PathTests(unittest.TestCase):
    def test_a_path_is_relative_plain_and_inside_the_project(self) -> None:
        for raw, clean in (("app.py", "app.py"), ("./src/main.js", "src/main.js"),
                           ("src\\util.py", "src/util.py"), (".gitignore", ".gitignore"),
                           ("Makefile", "Makefile")):
            with self.subTest(raw):
                self.assertEqual(paths.safe(raw), clean)
        for raw in ("../x.py", "/abs.py", "a//b.py", "C:/x.py", "NUL", "con.txt", "a b.py",
                    "notes", "x" * 200 + ".py", "a./b.py"):
            with self.subTest(raw):
                self.assertIsNone(paths.safe(raw))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        support.temp_database(self)

    def test_writing_a_path_again_keeps_a_new_version_beside_the_old(self) -> None:
        first = projects.add("c", "app.py", "print(1)\n", lang="python")
        second = projects.add("c", "app.py", "print(2)\nprint(3)\n", lang="python")
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(projects.latest("c")["app.py"]["content"], "print(2)\nprint(3)\n")
        self.assertEqual(projects.file("c", "app.py", 1)["content"], "print(1)\n")
        self.assertEqual([(f["path"], f["lines"]) for f in projects.manifest("c")], [("app.py", 2)])
        self.assertEqual(projects.latest("other"), {})

    def test_a_project_is_held_to_its_limits(self) -> None:
        big = projects.add("c", "big.txt", "x" * (paths.MAX_FILE_BYTES + 1), lang="text")
        self.assertFalse(big["kept"])
        for n in range(paths.MAX_FILES):
            projects.add("c", f"f{n}.txt", "x", lang="text")
        self.assertFalse(projects.add("c", "one-more.txt", "x", lang="text")["kept"])
        self.assertTrue(projects.add("c", "f0.txt", "changed", lang="text")["kept"])


class RunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, tools_enabled=True, allow_outbound=True, agent_loop=True)

    async def test_the_answer_says_each_file_as_it_is_written(self) -> None:
        engine = ScriptedEngine(["{\"tool\": \"write_files\"}", ANSWER])
        self.addAsyncCleanup(engine.settled)
        trace = agent.Trace()
        stream = await collect(agent.run("build me a small flask todo app", history=[],
                                         trace=trace, engine=engine))
        files = only(stream, "file")
        self.assertEqual([f["type"] for f in files if f["type"] != "line"],
                         ["open", "close", "open", "close"])
        self.assertEqual([f["path"] for f in trace.files], ["app.py", "templates/index.html"])
        self.assertEqual(trace.files[0]["content"], "from flask import Flask\napp = Flask(__name__)\n")
        # The model chose to write files: the answer was asked for as files,
        # held to their grammar, and the tokens are the answer as written.
        self.assertIn(STYLES["files"].cue, engine.passes[1].prompt)
        self.assertEqual(engine.passes[1].grammar, GRAMMAR)
        self.assertEqual(trace.style, "files")
        self.assertEqual(trace.answer, ANSWER)

    async def test_an_answer_the_model_did_not_choose_to_write_as_files_is_free(self) -> None:
        engine = ScriptedEngine(["{\"tool\": \"none\"}", "A closure keeps its scope."])
        self.addAsyncCleanup(engine.settled)
        trace = agent.Trace()
        await collect(agent.run("what is a closure", history=[], trace=trace, engine=engine))
        self.assertIsNone(engine.passes[1].grammar)
        self.assertEqual(trace.style, "answer")

    async def test_the_tools_for_a_project_are_chosen_only_where_there_is_one(self) -> None:
        engine = ScriptedEngine(["{\"tool\": \"none\"}", "Fine."])
        self.addAsyncCleanup(engine.settled)
        await collect(agent.run("Suggest a name for my boat", history=[], engine=engine))
        self.assertNotIn("read_file", engine.passes[0].grammar)
        self.assertIn("write_files", engine.passes[0].grammar)
        # Still on the menu, which every conversation shares, so the system
        # text, and the cache of it, is the same with a project or without.
        self.assertIn("read_file", engine.passes[0].messages[0]["content"])

        engine = ScriptedEngine(["{\"tool\": \"none\"}", "Fine."])
        self.addAsyncCleanup(engine.settled)
        await collect(agent.run("what does app.py do", history=[], engine=engine,
                                project=[{"path": "app.py", "lines": 40}]))
        self.assertIn("read_file", engine.passes[0].grammar)

    async def test_a_project_so_far_is_listed_after_the_message(self) -> None:
        engine = ScriptedEngine(["{\"tool\": \"none\"}", "Done."])
        self.addAsyncCleanup(engine.settled)
        await collect(agent.run("add a delete button", history=[], engine=engine,
                                project=[{"path": "app.py", "lines": 40}]))
        self.assertIn("The project so far: app.py (40 lines).", engine.passes[0].prompt)


class ChatTests(ChatCase):
    def test_files_are_kept_before_the_page_hears_they_closed(self) -> None:
        self.use_engine(PlayedEngine(ANSWER))
        stream = self.chat("/calc 1+1")
        closed = [f for f in only(stream, "file") if f["type"] == "close"]
        self.assertEqual([(f["path"], f["version"], f["kept"]) for f in closed],
                         [("app.py", 1, True), ("templates/index.html", 1, True)])
        cid = only(stream, "conversation")[0]["id"]
        self.assertEqual(sorted(projects.latest(cid)), ["app.py", "templates/index.html"])
        convo = self.client.get(f"/api/conversations/{cid}").json()
        self.assertEqual([f["path"] for f in convo["project"]], ["app.py", "templates/index.html"])
        self.assertNotIn("content", convo["project"][0])

    def test_a_project_is_downloaded_as_one_folder_in_a_zip(self) -> None:
        self.use_engine(PlayedEngine(ANSWER))
        cid = only(self.chat("my todo app please"), "conversation")[0]["id"]
        res = self.client.get(f"/api/conversations/{cid}/project.zip")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "application/zip")
        self.assertIn('filename="my-todo-app-please.zip"', res.headers["content-disposition"])
        names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
        self.assertEqual(sorted(names), ["my-todo-app-please/app.py", "my-todo-app-please/templates/index.html"])
        files = self.client.get(f"/api/conversations/{cid}/project").json()["files"]
        self.assertEqual(files[0]["content"], "from flask import Flask\napp = Flask(__name__)\n")
        one = self.client.get(f"/api/conversations/{cid}/project/files/templates/index.html").json()
        self.assertEqual((one["version"], one["content"]), (1, "<h1>Todo</h1>\n"))

    def test_nothing_of_a_project_is_left_when_its_conversation_goes(self) -> None:
        self.use_engine(PlayedEngine(ANSWER))
        cid = only(self.chat("my todo app please"), "conversation")[0]["id"]
        self.client.delete(f"/api/conversations/{cid}")
        self.assertEqual(projects.latest(cid), {})
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/project.zip").status_code, 404)

    def test_a_change_the_model_writes_as_files_is_told_the_project_so_far(self) -> None:
        self.use_engine(PlayedEngine(ANSWER, {"tool": "write_files"}))
        cid = only(self.chat("my todo app please"), "conversation")[0]["id"]
        self.chat("add a delete button", conversation_id=cid)
        self.assertIn(STYLES["files"].cue, self.engine.answer_prompts[-1][-1]["content"])
        self.assertIn("The project so far: app.py (2 lines), templates/index.html (1 line).",
                      "\n".join(m["content"] for m in self.engine.answer_prompts[-1]))
        self.assertEqual(runs.recent_runs("chat")[0]["label"], "files")


class ReadFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        support.temp_database(self)
        projects.add("c", "app.py", "print(1)\n", lang="python")

    async def read(self, path: str, cid: str = "c"):
        async def made():
            CALL.set(CallContext(conversation_id=cid))
            return await call_tool("read_file", {"path": path})
        import asyncio

        return await asyncio.create_task(made())

    async def test_a_file_of_this_conversation_is_read_as_it_stands(self) -> None:
        result = await self.read("app.py")
        self.assertTrue(result.ok)
        self.assertIn("print(1)", REGISTRY["read_file"].observation(result, ""))

    async def test_another_conversations_file_and_a_path_out_are_not_there(self) -> None:
        self.assertFalse((await self.read("app.py", cid="other")).ok)
        self.assertFalse((await self.read("../app.py")).ok)
        missing = await self.read("main.py")
        self.assertIn("the project has: app.py", missing.summary)


class CheckTests(unittest.TestCase):
    def test_numbers_in_code_are_not_claims(self) -> None:
        answer = "It listens on its port.\n```python\napp.run(port=5000, debug=True)\n```"
        self.assertEqual(check(answer, ["Nothing about ports."]).unsupported, ())


if __name__ == "__main__":
    unittest.main()
