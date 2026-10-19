"""Code projects: the files the model writes with its tools, kept a version
of one file at a time, listed for the next message, read back with line
numbers, run in the sandbox, and downloaded as a zip.

The model changes a project by calling ``write_file`` (a file whole) or
``edit_file`` (one exact piece of a file). The tools keep what they write;
the panel is told of each file as it is written, or once it has changed.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import asyncio
import io
import unittest
import zipfile
from dataclasses import dataclass, field

try:
    from app.agent.calls import response
    from app.agent.verify import check
    from app.projects import paths
    from app.sandbox import runner as sandbox
    from app.sandbox.runner import Outcome
    from app.store import conversations, projects
    from app.tools import REGISTRY
    from app.tools.base import CALL, CallContext, call_tool
    from tests import support
    from tests.support import ChatCase, PlayedEngine, only, step
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

FLASK = "from flask import Flask\napp = Flask(__name__)\n"
APP = (
    "import os\n\ndef main():\n    print('hi')\n    return 0\n\n"
    "if __name__ == '__main__':\n    main()\n"
)
UTIL = "def twice(n):\n    return 2 * n\n"
UNCHANGED = (
    "app.py already has exactly this content, so nothing was saved; "
    "change the code that causes the problem."
)
TRACEBACK = (
    "Traceback (most recent call last):\n"
    + "".join(f'  File "/work/project/app.py", line {n}, in step{n}\n' for n in range(60))
    + "NameError: name 'y' is not defined\n"
)


def told(stream: list) -> list[str]:
    """The order of a stream in words, with each file event by its type. A
    run of tokens, or of a file's lines, is one word."""
    out: list[str] = []
    for name, data in stream:
        if name == "file":
            word = f"file:{data['type']}"
        elif name == "step":
            word = f"step:{data['id']}:{data['status']}"
        else:
            word = name
        if word in ("token", "file:line") and out and out[-1] == word:
            continue
        out.append(word)
    return out


def lines_of(stream: list) -> list[str]:
    return [f["text"] for f in only(stream, "file") if f["type"] == "line"]


@dataclass
class FakeRunner:
    """A sandbox that runs nothing: it keeps each program and the project it
    was given, and says what the test has it say."""

    outcome: Outcome = field(default_factory=lambda: Outcome("42\n", "", 0, False, 9.0))
    runs: list[tuple[str, dict | None]] = field(default_factory=list)

    def available(self) -> bool:
        return True

    async def run(self, code: str, project: dict | None = None) -> Outcome:
        self.runs.append((code, project))
        return self.outcome


def use_runner(case: unittest.TestCase, runner) -> None:
    sandbox.use(runner)
    case.addCleanup(sandbox.use, None)


# ------------------------------------------------------------- the paths ---


class PathTests(unittest.TestCase):
    def test_a_path_is_relative_plain_and_inside_the_project(self) -> None:
        for raw, clean in (("app.py", "app.py"), ("./src/main.js", "src/main.js"),
                           ("src\\util.py", "src/util.py"), (".gitignore", ".gitignore"),
                           ("Makefile", "Makefile"), ("`app.py`", "app.py")):
            with self.subTest(raw):
                self.assertEqual(paths.safe(raw), clean)
        for raw in ("../x.py", "/abs.py", "a//b.py", "C:/x.py", "NUL", "con.txt", "a b.py",
                    "notes", "x" * 200 + ".py", "a./b.py", "src/../../x.py", ""):
            with self.subTest(raw):
                self.assertIsNone(paths.safe(raw))

    def test_a_files_language_is_what_its_name_says(self) -> None:
        for path, lang in (("app.py", "python"), ("web/index.html", "html"),
                           ("Dockerfile", "dockerfile"), ("Makefile", "makefile"),
                           ("notes.unknown", "text")):
            with self.subTest(path):
                self.assertEqual(paths.language(path), lang)


# ------------------------------------------------------------- the store ---


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

    def test_a_file_written_again_exactly_as_it_stands_is_not_a_new_version(self) -> None:
        projects.add("c", "app.py", "print(1)\n", lang="python")
        again = projects.add("c", "app.py", "print(1)\n", lang="python")
        self.assertEqual(again, {"kept": False, "why": "app.py already has exactly this content"})
        self.assertIsNone(projects.file("c", "app.py", 2))
        self.assertEqual(projects.latest("c")["app.py"]["version"], 1)

    def test_a_new_version_of_one_file_leaves_the_others_as_they_were(self) -> None:
        projects.add("c", "app.py", APP, lang="python")
        projects.add("c", "util.py", UTIL, lang="python")
        projects.add("c", "app.py", APP + "# more\n", lang="python")
        self.assertEqual({p: f["version"] for p, f in projects.latest("c").items()},
                         {"app.py": 2, "util.py": 1})

    def test_the_manifest_says_what_is_there_without_what_is_in_it(self) -> None:
        projects.add("c", "util.py", UTIL, lang="python")
        projects.add("c", "app.py", APP, lang="python")
        listed = projects.manifest("c")
        self.assertEqual([f["path"] for f in listed], ["app.py", "util.py"])
        self.assertEqual(set(listed[0]), {"path", "version", "lang", "bytes", "lines", "created_at"})
        self.assertEqual((listed[0]["lines"], listed[0]["bytes"]), (8, len(APP.encode("utf-8"))))

    def test_a_project_is_held_to_its_limits(self) -> None:
        big = projects.add("c", "big.txt", "x" * (paths.MAX_FILE_BYTES + 1), lang="text")
        self.assertFalse(big["kept"])
        for n in range(paths.MAX_FILES):
            projects.add("c", f"f{n}.txt", "x", lang="text")
        self.assertFalse(projects.add("c", "one-more.txt", "x", lang="text")["kept"])
        self.assertTrue(projects.add("c", "f0.txt", "changed", lang="text")["kept"])

    def test_forgetting_a_project_forgets_every_version_of_it_and_nothing_else(self) -> None:
        projects.add("c", "app.py", "print(1)\n", lang="python")
        projects.add("c", "app.py", "print(2)\n", lang="python")
        projects.add("other", "app.py", "print(3)\n", lang="python")
        projects.forget("c")
        self.assertEqual(projects.latest("c"), {})
        self.assertIsNone(projects.file("c", "app.py", 1))
        self.assertEqual(list(projects.latest("other")), ["app.py"])


# ------------------------------------------------------------- the tools ---


class ToolCase(unittest.IsolatedAsyncioTestCase):
    """A file tool called as a run calls it: inside its own task, for one
    conversation."""

    def setUp(self) -> None:
        support.temp_database(self)

    async def call(self, name: str, args: dict, cid: str | None = "c"):
        async def made():
            CALL.set(CallContext(conversation_id=cid, run_id="run-1"))
            return await call_tool(name, args)

        return await asyncio.create_task(made())

    def read(self, name: str, result, budget: int | None = None) -> str:
        """What the model reads of a call that worked."""
        return REGISTRY[name].observation(result, "", budget)


class WriteFileTests(ToolCase):
    async def test_a_new_file_is_created_at_its_first_version(self) -> None:
        result = await self.call("write_file", {"path": "app.py", "content": FLASK})
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "Created app.py (v1, 2 lines).")
        self.assertEqual(result.card, "file")
        self.assertEqual(
            {k: result.data[k] for k in ("path", "lang", "version", "lines", "created", "action")},
            {"path": "app.py", "lang": "python", "version": 1, "lines": 2, "created": True,
             "action": "write"},
        )
        self.assertEqual(projects.file("c", "app.py")["content"], FLASK)

    async def test_a_file_written_whole_again_is_its_next_version(self) -> None:
        await self.call("write_file", {"path": "app.py", "content": FLASK})
        result = await self.call("write_file", {"path": "app.py", "content": APP})
        self.assertEqual(result.summary, "Wrote app.py (v2, 8 lines).")
        self.assertIs(result.data["created"], False)
        self.assertEqual(projects.file("c", "app.py", 1)["content"], FLASK)
        self.assertEqual(projects.file("c", "app.py")["content"], APP)

    async def test_writing_a_file_unchanged_is_refused_and_keeps_no_version(self) -> None:
        await self.call("write_file", {"path": "app.py", "content": "print(1)\n"})
        result = await self.call("write_file", {"path": "app.py", "content": "print(1)\n"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unchanged")
        self.assertEqual(result.summary, UNCHANGED)
        self.assertEqual(result.data["version"], 1)
        self.assertIsNone(projects.file("c", "app.py", 2))

    async def test_a_path_that_cannot_be_a_files_is_refused(self) -> None:
        result = await self.call("write_file", {"path": "../evil.py", "content": "x\n"})
        self.assertEqual(result.error, "bad path")
        self.assertEqual(
            result.summary,
            "'../evil.py' cannot be a file's path; give a relative path such as app/main.py.",
        )
        self.assertEqual(projects.latest("c"), {})

    async def test_without_a_conversation_nothing_is_written(self) -> None:
        result = await self.call("write_file", {"path": "app.py", "content": "x\n"}, cid=None)
        self.assertEqual(result.error, "no conversation")

    async def test_a_file_past_the_limits_is_not_kept_and_says_why(self) -> None:
        result = await self.call("write_file", {"path": "big.txt",
                                                "content": "x" * (paths.MAX_FILE_BYTES + 1)})
        self.assertEqual(result.error, "not kept")
        self.assertEqual(result.summary, "big.txt was not saved: big.txt is larger than 256 KB.")

    async def test_one_conversation_never_writes_into_anothers_project(self) -> None:
        await self.call("write_file", {"path": "app.py", "content": FLASK}, cid="other")
        self.assertEqual(projects.latest("c"), {})
        self.assertEqual(list(projects.latest("other")), ["app.py"])


class EditFileTests(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        projects.add("c", "app.py", APP, lang="python")
        projects.add("c", "util.py", UTIL, lang="python")

    async def edit(self, old: str, new: str, path: str = "app.py"):
        return await self.call("edit_file", {"path": path, "old_string": old, "new_string": new})

    async def test_an_edit_replaces_that_piece_and_keeps_a_new_version_of_that_file_only(self) -> None:
        result = await self.edit("    print('hi')", "    print('hello')")
        self.assertTrue(result.ok)
        self.assertEqual(projects.file("c", "app.py")["content"], APP.replace("'hi'", "'hello'"))
        self.assertEqual(projects.file("c", "app.py", 1)["content"], APP)
        self.assertEqual({p: f["version"] for p, f in projects.latest("c").items()},
                         {"app.py": 2, "util.py": 1})
        self.assertEqual(
            {k: result.data[k] for k in ("path", "version", "line", "removed", "added", "action")},
            {"path": "app.py", "version": 2, "line": 4, "removed": ["    print('hi')"],
             "added": ["    print('hello')"], "action": "edit"},
        )

    async def test_the_model_reads_the_lines_around_the_edit_as_they_now_stand(self) -> None:
        result = await self.edit("    print('hi')", "    print('hello')")
        self.assertEqual(result.summary, "Edited app.py (v2); lines 1-7 now read:")
        self.assertEqual(
            self.read("edit_file", result),
            "Edited app.py (v2); lines 1-7 now read:\n"
            "1│ import os\n2│ \n3│ def main():\n4│     print('hello')\n"
            "5│     return 0\n6│ \n7│ if __name__ == '__main__':",
        )

    async def test_an_edit_near_the_end_shows_no_line_past_the_last(self) -> None:
        # The end of the last line is not a line of its own.
        result = await self.edit("    main()", "    raise SystemExit(main())")
        self.assertEqual(result.summary, "Edited app.py (v2); lines 5-8 now read:")
        self.assertNotIn("9│", self.read("edit_file", result))

    async def test_a_piece_copied_without_its_trailing_spaces_is_still_found(self) -> None:
        projects.add("c", "spaced.py", "a = 1   \nb = 2\n", lang="python")
        result = await self.edit("a = 1\nb = 2", "a = 10\nb = 2", path="spaced.py")
        self.assertTrue(result.ok)
        self.assertEqual(projects.file("c", "spaced.py")["content"], "a = 10\nb = 2\n")

    async def test_a_piece_found_loosely_changes_only_the_lines_it_matched(self) -> None:
        # Found with the ends of its lines forgiven, it replaces what it
        # matched in the file as it is, and leaves every other line alone.
        projects.add("c", "win.py", "a = 1   \r\nb = 2\r\nc = 3  \r\n", lang="python")
        await self.edit("a = 1\nb = 2", "a = 10\nb = 2", path="win.py")
        self.assertTrue(projects.file("c", "win.py")["content"].endswith("c = 3  \r\n"))

    async def test_a_piece_that_is_not_there_is_refused_with_where_to_look(self) -> None:
        result = await self.edit("    print('hey there')", "    print('hello')")
        self.assertEqual((result.ok, result.error), (False, "not found"))
        self.assertEqual(
            result.summary,
            "old_string was not found in app.py (the nearest line is 4); "
            "copy the exact lines from read_file.",
        )
        nothing_near = await self.edit("zzz qqq", "x")
        self.assertEqual(nothing_near.summary,
                         "old_string was not found in app.py; copy the exact lines from read_file.")
        self.assertEqual(projects.latest("c")["app.py"]["version"], 1)

    async def test_a_piece_that_is_there_twice_is_refused(self) -> None:
        projects.add("c", "dup.py", "x = 1\nx = 1\n", lang="python")
        result = await self.edit("x = 1", "x = 2", path="dup.py")
        self.assertEqual((result.ok, result.error), (False, "ambiguous"))
        self.assertEqual(
            result.summary,
            "old_string appears 2 times in dup.py; include more of the lines around it so that "
            "it appears once.",
        )
        self.assertEqual(projects.latest("c")["dup.py"]["version"], 1)

    async def test_an_edit_that_would_change_nothing_is_refused(self) -> None:
        result = await self.edit("    return 0", "    return 0")
        self.assertEqual((result.ok, result.error), (False, "unchanged"))
        self.assertEqual(result.summary,
                         "old_string and new_string are the same, so nothing would change.")
        empty = await self.edit("", "    return 1")
        self.assertEqual((empty.error, empty.summary),
                         ("bad input", "old_string is empty; give the exact lines to replace."))
        self.assertEqual(projects.latest("c")["app.py"]["version"], 1)

    async def test_a_file_there_is_not_cannot_be_edited(self) -> None:
        result = await self.edit("a", "b", path="main.py")
        self.assertEqual((result.ok, result.error), (False, "no such file"))
        self.assertEqual(
            result.summary,
            "There is no file main.py to edit; the project has: app.py, util.py. "
            "Use write_file to create it.",
        )


class ReadFileTests(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        projects.add("c", "app.py", "print(1)\nprint(2)\n", lang="python")

    async def test_a_file_is_read_as_it_stands_with_its_lines_numbered(self) -> None:
        result = await self.call("read_file", {"path": "app.py"})
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "app.py, version 1 (2 lines)")
        self.assertEqual(self.read("read_file", result),
                         "app.py (v1, 2 lines):\n1│ print(1)\n2│ print(2)")

    async def test_the_newest_version_is_the_one_read(self) -> None:
        projects.add("c", "app.py", "print(3)\n", lang="python")
        result = await self.call("read_file", {"path": "app.py"})
        self.assertEqual(self.read("read_file", result), "app.py (v2, 1 lines):\n1│ print(3)")

    async def test_the_numbers_are_lined_up_however_long_the_file(self) -> None:
        projects.add("c", "long.py", "".join(f"v{n} = {n}\n" for n in range(1, 13)), lang="python")
        seen = self.read("read_file", await self.call("read_file", {"path": "long.py"}))
        self.assertIn("\n 9│ v9 = 9\n10│ v10 = 10\n", seen)

    async def test_a_file_too_long_to_read_at_once_says_where_to_go_on_from(self) -> None:
        projects.add("c", "huge.py", "".join(f"line = {n}\n" for n in range(1, 401)), lang="python")
        first = self.read("read_file", await self.call("read_file", {"path": "huge.py"}), budget=100)
        # Numbered as wide as the lines shown need.
        self.assertTrue(first.startswith("huge.py (v1, 400 lines):\n 1│ line = 1\n"))
        self.assertTrue(first.endswith(
            "(lines 1-12 of 400; call read_file again with start_line 13 for the rest)"
        ))
        rest = self.read("read_file", await self.call("read_file", {"path": "huge.py",
                                                                    "start_line": 13}), budget=100)
        self.assertTrue(rest.startswith("huge.py (v1, 400 lines):\n13│ line = 13\n"))

    async def test_a_start_past_the_end_reads_the_last_line(self) -> None:
        result = await self.call("read_file", {"path": "app.py", "start_line": 99})
        self.assertEqual(self.read("read_file", result), "app.py (v1, 2 lines):\n2│ print(2)")

    async def test_another_conversations_file_and_a_path_out_are_not_there(self) -> None:
        self.assertEqual((await self.call("read_file", {"path": "app.py"}, cid="other")).error,
                         "no such file")
        self.assertEqual((await self.call("read_file", {"path": "../app.py"})).error, "no such file")
        missing = await self.call("read_file", {"path": "main.py"})
        self.assertEqual(missing.summary, "There is no file called main.py; the project has: app.py.")


class RunFileTests(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        projects.add("c", "app.py", "from util import twice\nprint(twice(21))\n", lang="python")
        projects.add("c", "util.py", UTIL, lang="python")
        self.runner = FakeRunner()
        use_runner(self, self.runner)

    async def test_the_file_runs_with_the_whole_project_beside_it(self) -> None:
        result = await self.call("run_file", {"path": "app.py"})
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "app.py printed: 42")
        ((code, project),) = self.runner.runs
        self.assertIn("runpy.run_path('app.py'", code)
        self.assertEqual(sorted(project), ["app.py", "util.py"])

    async def test_a_file_that_fails_is_read_with_the_end_of_what_it_said(self) -> None:
        self.runner.outcome = Outcome("", TRACEBACK, 1, False, 5.0)
        result = await self.call("run_file", {"path": "app.py"})
        self.assertEqual(result.summary,
                         "app.py stopped with an error: NameError: name 'y' is not defined")
        seen = self.read("run_file", result)
        self.assertTrue(seen.startswith(
            "It printed nothing.\nIt exited with code 1. The end of what it said:\n…"
        ))
        self.assertTrue(seen.endswith("NameError: name 'y' is not defined"))
        # Only the end: where a traceback names the error.
        self.assertNotIn("Traceback (most recent call last)", seen)
        self.assertNotIn("in step0\n", seen)

    async def test_only_a_python_file_of_this_project_runs(self) -> None:
        projects.add("c", "notes.md", "# notes\n", lang="markdown")
        self.assertEqual((await self.call("run_file", {"path": "notes.md"})).error, "not python")
        missing = await self.call("run_file", {"path": "main.py"})
        self.assertEqual(missing.error, "no such file")
        self.assertIn("app.py, util.py", missing.summary)
        self.assertEqual((await self.call("run_file", {"path": "../app.py"})).error, "no such file")
        self.assertEqual(self.runner.runs, [])


# -------------------------------------------------- through the chat API ---


class ChatTests(ChatCase):
    """The model writes, changes, reads and runs the files of the
    conversation's project, through ``/api/chat``."""

    def project_with(self, files: dict[str, str]) -> str:
        """A conversation whose project has these files, each at version 1."""
        cid = conversations.create("my app")
        for path, content in files.items():
            projects.add(cid, path, content, lang=paths.language(path))
        return cid

    def calls(self, tool: str, **args: str) -> None:
        self.use_engine(PlayedEngine("Done.", {"tool": tool, "args": args}))

    def test_a_file_the_model_writes_is_shown_as_it_is_written_and_kept_before_it_closes(self) -> None:
        self.calls("write_file", path="app.py", content=FLASK)
        stream = self.chat("write me a flask app")
        self.assertEqual(
            told(stream),
            ["conversation", "step:reply-1:running", "start", "file:open", "file:line",
             "step:reply-1:ok", "step:tool-0:running", "step:tool-0:ok", "card", "file:close",
             "step:reply-2:running", "start", "token", "step:reply-2:ok", "done", "run"],
        )
        opened, *_, closed = only(stream, "file")
        self.assertEqual(opened, {"type": "open", "path": "app.py", "lang": "python"})
        self.assertEqual(closed, {"type": "close", "path": "app.py", "lang": "python", "lines": 2,
                                  "version": 1, "kept": True})
        self.assertEqual("".join(lines_of(stream)), FLASK)
        cid = only(stream, "conversation")[0]["id"]
        self.assertEqual(projects.file(cid, "app.py")["content"], FLASK)
        self.assertEqual(only(stream, "card")[0]["card"], "file")
        # The model reads what was kept.
        self.assertEqual(self.engine.prompts[-1][-1]["content"],
                         response("Created app.py (v1, 2 lines)."))

    def test_the_lines_shown_as_a_file_is_written_are_its_lines(self) -> None:
        # Not the line break the call format puts after the parameter's tag.
        self.calls("write_file", path="app.py", content=FLASK)
        stream = self.chat("write me a flask app")
        self.assertEqual(lines_of(stream), ["from flask import Flask\n", "app = Flask(__name__)\n"])

    def test_the_project_is_listed_with_the_conversation_and_its_files_read_back(self) -> None:
        self.calls("write_file", path="app.py", content=FLASK)
        cid = only(self.chat("write me a flask app"), "conversation")[0]["id"]
        convo = self.client.get(f"/api/conversations/{cid}").json()
        self.assertEqual([(f["path"], f["version"], f["lines"]) for f in convo["project"]],
                         [("app.py", 1, 2)])
        self.assertNotIn("content", convo["project"][0])
        files = self.client.get(f"/api/conversations/{cid}/project").json()["files"]
        self.assertEqual([(f["path"], f["content"]) for f in files], [("app.py", FLASK)])
        one = self.client.get(f"/api/conversations/{cid}/project/files/app.py?version=1").json()
        self.assertEqual((one["version"], one["content"]), (1, FLASK))
        for missing in ("main.py", "app.py?version=2", "..%2Fapp.py"):
            with self.subTest(missing):
                res = self.client.get(f"/api/conversations/{cid}/project/files/{missing}")
                self.assertEqual(res.status_code, 404)

    def test_a_project_is_downloaded_as_one_folder_in_a_zip(self) -> None:
        self.calls("write_file", path="app.py", content=FLASK)
        cid = only(self.chat("my todo app please"), "conversation")[0]["id"]
        res = self.client.get(f"/api/conversations/{cid}/project.zip")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "application/zip")
        self.assertIn('filename="my-todo-app-please.zip"', res.headers["content-disposition"])
        archive = zipfile.ZipFile(io.BytesIO(res.content))
        self.assertEqual(archive.namelist(), ["my-todo-app-please/app.py"])
        self.assertEqual(archive.read("my-todo-app-please/app.py").decode("utf-8"), FLASK)

    def test_a_conversation_with_no_project_has_no_zip(self) -> None:
        cid = only(self.chat("Hello"), "conversation")[0]["id"]
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/project.zip").status_code, 404)
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/project").json(), {"files": []})

    def test_writing_one_file_leaves_the_others_at_their_version(self) -> None:
        cid = self.project_with({"app.py": FLASK, "util.py": UTIL})
        self.calls("write_file", path="app.py", content=APP)
        self.chat("rewrite app.py", conversation_id=cid)
        self.assertEqual({p: f["version"] for p, f in projects.latest(cid).items()},
                         {"app.py": 2, "util.py": 1})

    def test_an_edit_changes_only_that_file_and_the_panel_is_shown_it_once_it_has(self) -> None:
        cid = self.project_with({"app.py": APP, "util.py": UTIL})
        self.calls("edit_file", path="app.py",
                   old_string="    print('hi')", new_string="    print('hello')")
        stream = self.chat("say hello instead", conversation_id=cid)
        self.assertEqual(
            told(stream),
            ["conversation", "step:reply-1:running", "start", "step:reply-1:ok",
             "step:tool-0:running", "step:tool-0:ok", "card", "file:open", "file:line",
             "file:close", "step:reply-2:running", "start", "token", "step:reply-2:ok", "done",
             "run"],
        )
        edited = APP.replace("'hi'", "'hello'")
        self.assertEqual("".join(lines_of(stream)), edited)
        self.assertEqual(only(stream, "file")[-1]["version"], 2)
        self.assertEqual({p: (f["version"], f["content"]) for p, f in projects.latest(cid).items()},
                         {"app.py": (2, edited), "util.py": (1, UTIL)})
        told_model = self.engine.prompts[-1][-1]["content"]
        self.assertTrue(told_model.startswith(
            "<tool_response>\nEdited app.py (v2); lines 1-7 now read:\n1│ import os\n"
        ))
        self.assertIn("\n4│     print('hello')\n", told_model)

    def test_writing_a_file_unchanged_is_refused_and_the_model_told_to_change_the_code(self) -> None:
        cid = self.project_with({"app.py": "print(1)\n"})
        self.calls("write_file", path="app.py", content="print(1)\n")
        stream = self.chat("it still fails, fix it", conversation_id=cid)
        self.assertEqual(
            told(stream),
            ["conversation", "step:reply-1:running", "start", "file:open", "file:line",
             "step:reply-1:ok", "step:tool-0:running", "step:tool-0:failed", "card", "file:close",
             "step:reply-2:running", "start", "token", "step:reply-2:ok", "done", "run"],
        )
        self.assertEqual(step(stream, "tool-0", "failed")["detail"], "unchanged")
        self.assertEqual(only(stream, "file")[-1],
                         {"type": "close", "path": "app.py", "lang": "python", "lines": 1,
                          "kept": False, "why": UNCHANGED})
        self.assertEqual(self.engine.prompts[-1][-1]["content"], response("Error: " + UNCHANGED))
        self.assertEqual(projects.latest(cid)["app.py"]["version"], 1)
        self.assertIsNone(projects.file(cid, "app.py", 2))

    def test_an_edit_that_fails_is_read_by_the_model_and_changes_nothing(self) -> None:
        cid = self.project_with({"app.py": APP})
        self.calls("edit_file", path="app.py", old_string="print('hey')", new_string="print('hello')")
        stream = self.chat("say hello instead", conversation_id=cid)
        self.assertEqual(step(stream, "tool-0", "failed")["detail"], "not found")
        self.assertEqual(only(stream, "file"), [])
        self.assertTrue(self.engine.prompts[-1][-1]["content"].startswith(
            "<tool_response>\nError: old_string was not found in app.py"
        ))
        self.assertEqual(projects.latest(cid)["app.py"]["version"], 1)

    def test_a_path_that_cannot_be_a_files_shows_and_keeps_nothing(self) -> None:
        self.calls("write_file", path="../evil.py", content="x\n")
        stream = self.chat("write it outside")
        self.assertEqual(only(stream, "file"), [])
        self.assertEqual(step(stream, "tool-0", "failed")["detail"], "bad path")
        self.assertEqual(projects.latest(only(stream, "conversation")[0]["id"]), {})

    def test_the_model_reads_a_file_with_its_lines_numbered(self) -> None:
        cid = self.project_with({"app.py": APP})
        self.calls("read_file", path="app.py")
        stream = self.chat("what does app.py do", conversation_id=cid)
        self.assertEqual(only(stream, "file"), [])
        self.assertEqual(
            self.engine.prompts[-1][-1]["content"],
            response("app.py (v1, 8 lines):\n1│ import os\n2│ \n3│ def main():\n"
                     "4│     print('hi')\n5│     return 0\n6│ \n"
                     "7│ if __name__ == '__main__':\n8│     main()"),
        )

    def test_the_model_reads_the_end_of_what_a_failing_file_said(self) -> None:
        use_runner(self, FakeRunner(Outcome("", TRACEBACK, 1, False, 5.0)))
        cid = self.project_with({"app.py": "print(y)\n"})
        self.calls("run_file", path="app.py")
        stream = self.chat("run it", conversation_id=cid)
        self.assertEqual(step(stream, "tool-0", "ok")["tool"], "run_file")
        told_model = self.engine.prompts[-1][-1]["content"]
        self.assertIn("It exited with code 1. The end of what it said:\n…", told_model)
        self.assertTrue(told_model.endswith("NameError: name 'y' is not defined\n</tool_response>"))
        # Offered because the sandbox can run it.
        self.assertIn('"name": "run_file"', self.engine.prompts[0][0]["content"])

    def test_the_next_message_is_told_the_project_as_it_stands(self) -> None:
        self.calls("write_file", path="app.py", content=FLASK)
        first = self.chat("write me a flask app")
        cid = only(first, "conversation")[0]["id"]
        # Nothing was there to list when the first message was sent.
        self.assertEqual(support.unsent(self.engine.prompts[0])[-1]["content"], "write me a flask app")
        self.use_engine(PlayedEngine("Fine."))
        self.chat("thanks", conversation_id=cid)
        self.assertEqual(support.unsent(self.engine.prompts[0])[-1]["content"],
                         "thanks\n\nProject files: app.py (2 lines, v1).")

    def test_every_file_is_listed_by_its_size_and_version(self) -> None:
        cid = self.project_with({"app.py": APP, "util.py": UTIL, "one.txt": "x\n"})
        projects.add(cid, "app.py", APP + "# more\n", lang="python")
        self.chat("what is there", conversation_id=cid)
        self.assertEqual(
            support.unsent(self.engine.prompts[0])[-1]["content"],
            "what is there\n\nProject files: app.py (9 lines, v2), one.txt (1 line, v1), "
            "util.py (2 lines, v1).",
        )
        # And it is kept with what the message was sent, to be read again.
        (record,) = conversations.record(cid)[1]
        self.assertIn("project", [p["kind"] for p in record["parts"]])

    def test_a_file_the_model_wrote_is_not_a_lookup_to_check_the_answer_against(self) -> None:
        self.use_engine(PlayedEngine(
            "It listens on port 5000.",
            {"tool": "write_file", "args": {"path": "app.py", "content": "app.run(port=5000)\n"}},
        ))
        done = only(self.chat("write me a server"), "done")[0]
        self.assertEqual((done["unsupported"], done["correction"]), ([], None))

    def test_nothing_of_a_project_is_left_when_its_conversation_goes(self) -> None:
        self.calls("write_file", path="app.py", content=FLASK)
        cid = only(self.chat("my todo app please"), "conversation")[0]["id"]
        self.client.delete(f"/api/conversations/{cid}")
        self.assertEqual(projects.latest(cid), {})
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/project.zip").status_code, 404)
        self.assertEqual(self.client.get(f"/api/conversations/{cid}/project").status_code, 404)


class CheckTests(unittest.TestCase):
    def test_numbers_in_code_are_not_claims(self) -> None:
        answer = "It listens on its port.\n```python\napp.run(port=5000, debug=True)\n```"
        self.assertEqual(check(answer, ["Nothing about ports."]).unsupported, ())


if __name__ == "__main__":
    unittest.main()
