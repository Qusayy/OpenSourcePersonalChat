"""Moving and deleting the files of a project: the store, the two tools, and
what the page is told.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import io
import unittest
import zipfile

try:
    from app.agent.calls import response
    from app.projects import paths
    from app.store import conversations, projects
    from tests import support
    from tests.support import ChatCase, PlayedEngine, only
    from tests.test_projects import ToolCase, told
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


def add(cid: str, path: str, content: str) -> dict:
    return projects.add(cid, path, content, lang=paths.language(path))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        support.temp_database(self)

    def test_a_deleted_file_is_gone_and_its_versions_are_kept(self) -> None:
        add("c", "a.py", "a = 1\n")
        add("c", "a.py", "a = 2\n")
        kept = projects.remove("c", ["a.py"])
        self.assertEqual(kept, {"kept": True, "removed": [{"path": "a.py", "version": 3}]})
        self.assertNotIn("a.py", projects.latest("c"))
        self.assertIsNone(projects.file("c", "a.py"))
        self.assertIsNone(projects.file("c", "a.py", 3))
        self.assertEqual(projects.file("c", "a.py", 2)["content"], "a = 2\n")
        self.assertEqual(projects.manifest("c"), [])

    def test_a_file_written_again_after_its_deletion_goes_on_counting(self) -> None:
        add("c", "a.py", "a = 1\n")
        projects.remove("c", ["a.py"])
        self.assertEqual(add("c", "a.py", "a = 9\n")["version"], 3)
        self.assertEqual(projects.file("c", "a.py")["content"], "a = 9\n")

    def test_deleting_what_is_not_there_changes_nothing(self) -> None:
        add("c", "a.py", "a = 1\n")
        self.assertFalse(projects.remove("c", ["a.py", "b.py"])["kept"])
        self.assertIn("a.py", projects.latest("c"))

    def test_a_moved_file_keeps_its_content_under_its_new_path(self) -> None:
        add("c", "notes.txt", "print(1)\n")
        kept = projects.move("c", [("notes.txt", "app/main.py")])
        self.assertEqual(kept["moved"], [{"from": "notes.txt", "to": "app/main.py", "version": 1,
                                          "lang": "python"}])
        now = projects.latest("c")
        self.assertEqual(list(now), ["app/main.py"])
        self.assertEqual((now["app/main.py"]["content"], now["app/main.py"]["lang"]),
                         ("print(1)\n", "python"))

    def test_nothing_is_moved_onto_a_file_that_is_there_and_nothing_moves_at_all(self) -> None:
        add("c", "a.py", "a = 1\n")
        add("c", "b.py", "b = 2\n")
        add("c", "c.py", "c = 3\n")
        kept = projects.move("c", [("a.py", "x.py"), ("c.py", "b.py")])
        self.assertEqual(kept, {"kept": False, "why": "there is a file there already: b.py"})
        self.assertEqual(sorted(projects.latest("c")), ["a.py", "b.py", "c.py"])

    def test_two_files_can_change_places(self) -> None:
        add("c", "a.py", "a = 1\n")
        add("c", "b.py", "b = 2\n")
        self.assertTrue(projects.move("c", [("a.py", "b.py"), ("b.py", "a.py")])["kept"])
        now = projects.latest("c")
        self.assertEqual((now["a.py"]["content"], now["b.py"]["content"]), ("b = 2\n", "a = 1\n"))

    def test_two_files_are_not_moved_to_one_path(self) -> None:
        add("c", "a.py", "a = 1\n")
        add("c", "b.py", "b = 2\n")
        self.assertFalse(projects.move("c", [("a.py", "x.py"), ("b.py", "x.py")])["kept"])

    def test_the_limits_count_only_the_files_that_are_there(self) -> None:
        for n in range(paths.MAX_FILES):
            add("c", f"f{n}.py", f"x = {n}\n")
        self.assertFalse(add("c", "one-more.py", "y = 1\n")["kept"])
        projects.remove("c", ["f0.py"])
        self.assertTrue(add("c", "one-more.py", "y = 1\n")["kept"])

    def test_the_files_below_a_folder(self) -> None:
        add("c", "app/main.py", "1\n")
        add("c", "app/routes/x.py", "2\n")
        add("c", "apple.py", "3\n")
        self.assertEqual(sorted(projects.under("c", "app")), ["app/main.py", "app/routes/x.py"])
        self.assertEqual(sorted(projects.under("c", "app/")), ["app/main.py", "app/routes/x.py"])

    def test_a_folders_name_is_held_to_a_files_rules_but_needs_no_extension(self) -> None:
        self.assertEqual(paths.safe_folder("src/app/"), "src/app")
        self.assertEqual(paths.safe_folder("./tests"), "tests")
        for bad in ("", "/", "/etc", "..", "a/../b", "c:\\x", "con", "a/./b", "x."):
            with self.subTest(bad=bad):
                self.assertIsNone(paths.safe_folder(bad))


class MoveFileTests(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        add("c", "main.py", "print(1)\n")
        add("c", "app/models.py", "x = 1\n")
        add("c", "app/routes/health.py", "ok = 1\n")

    async def test_a_file_moves_to_its_new_path(self) -> None:
        result = await self.call("move_file", {"source": "main.py", "destination": "src/start.py"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.summary, "Moved main.py to src/start.py.")
        self.assertEqual(result.data["moves"][0]["to"], "src/start.py")
        self.assertEqual(projects.file("c", "src/start.py")["content"], "print(1)\n")
        self.assertIsNone(projects.file("c", "main.py"))

    async def test_a_file_moves_into_a_folder_by_its_own_name(self) -> None:
        into = await self.call("move_file", {"source": "main.py", "destination": "app"})
        self.assertEqual(into.summary, "Moved main.py to app/main.py.")
        slash = await self.call("move_file", {"source": "app/main.py", "destination": "new/"})
        self.assertEqual(slash.summary, "Moved app/main.py to new/main.py.")

    async def test_a_folder_moves_with_everything_below_it(self) -> None:
        result = await self.call("move_file", {"source": "app", "destination": "src/app"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.summary, "Moved 2 files from app/ to src/app/.")
        self.assertEqual(sorted(projects.latest("c")),
                         ["main.py", "src/app/models.py", "src/app/routes/health.py"])

    async def test_a_folder_cannot_go_into_itself(self) -> None:
        result = await self.call("move_file", {"source": "app", "destination": "app/inner"})
        self.assertEqual((result.ok, result.error), (False, "bad path"))
        self.assertIn("cannot be moved into itself", result.summary)

    async def test_nothing_is_moved_onto_a_file_that_is_there(self) -> None:
        add("c", "other.py", "y = 2\n")
        result = await self.call("move_file", {"source": "main.py", "destination": "other.py"})
        self.assertEqual((result.ok, result.error), (False, "not kept"))
        self.assertEqual(result.summary, "Nothing was moved: there is a file there already: other.py.")
        self.assertEqual(projects.file("c", "other.py")["content"], "y = 2\n")

    async def test_what_is_not_there_says_what_is(self) -> None:
        result = await self.call("move_file", {"source": "nope.py", "destination": "x.py"})
        self.assertEqual((result.ok, result.error), (False, "no such file"))
        self.assertIn("app/models.py", result.summary)

    async def test_a_destination_that_cannot_be_a_path_is_refused(self) -> None:
        result = await self.call("move_file", {"source": "main.py", "destination": "../../etc/x.py"})
        self.assertEqual((result.ok, result.error), (False, "bad path"))

    async def test_a_move_to_where_it_is_is_no_move(self) -> None:
        result = await self.call("move_file", {"source": "main.py", "destination": "main.py"})
        self.assertEqual((result.ok, result.error), (False, "unchanged"))

    async def test_outside_a_conversation_nothing_moves(self) -> None:
        result = await self.call("move_file", {"source": "main.py", "destination": "a.py"}, cid=None)
        self.assertEqual(result.error, "no conversation")


class DeleteFileTests(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        add("c", "main.py", "print(1)\n")
        add("c", "tests/test_a.py", "a\n")
        add("c", "tests/test_b.py", "b\n")

    async def test_a_file_is_deleted(self) -> None:
        result = await self.call("delete_file", {"path": "main.py"})
        self.assertEqual((result.ok, result.summary), (True, "Deleted main.py."))
        self.assertEqual(result.data, {"action": "delete", "paths": ["main.py"]})
        self.assertIsNone(projects.file("c", "main.py"))

    async def test_a_folder_is_deleted_with_everything_in_it(self) -> None:
        result = await self.call("delete_file", {"path": "tests/"})
        self.assertEqual(result.summary, "Deleted 2 files in tests/.")
        self.assertEqual(list(projects.latest("c")), ["main.py"])

    async def test_what_is_not_there_is_not_deleted(self) -> None:
        result = await self.call("delete_file", {"path": "nope"})
        self.assertEqual((result.ok, result.error), (False, "no such file"))


class ChatTests(ChatCase):
    def project_with(self, files: dict[str, str]) -> str:
        cid = conversations.create("my app")
        for path, content in files.items():
            add(cid, path, content)
        return cid

    def test_a_move_is_told_to_the_page_after_its_card(self) -> None:
        cid = self.project_with({"main.py": "print(1)\n"})
        self.use_engine(PlayedEngine("Done.", {"tool": "move_file",
                                               "args": {"source": "main.py", "destination": "app/"}}))
        stream = self.chat("put main.py in an app folder", conversation_id=cid)
        self.assertIn("card", told(stream))
        self.assertEqual(told(stream).index("file:move"), told(stream).index("card") + 1)
        self.assertEqual(only(stream, "file"), [{"type": "move", "from": "main.py", "to": "app/main.py",
                                                 "version": 1, "lang": "python"}])
        self.assertEqual(only(stream, "card")[0]["data"]["action"], "move")
        self.assertEqual(self.engine.prompts[-1][-1]["content"],
                         response("Moved main.py to app/main.py."))
        self.assertEqual([f["path"] for f in projects.manifest(cid)], ["app/main.py"])

    def test_a_deletion_is_told_to_the_page_file_by_file(self) -> None:
        cid = self.project_with({"tests/a.py": "a\n", "tests/b.py": "b\n", "main.py": "m\n"})
        self.use_engine(PlayedEngine("Done.", {"tool": "delete_file", "args": {"path": "tests"}}))
        stream = self.chat("remove the tests", conversation_id=cid)
        self.assertEqual(only(stream, "file"), [{"type": "remove", "path": "tests/a.py"},
                                                {"type": "remove", "path": "tests/b.py"}])

    def test_the_zip_has_the_project_as_it_stands(self) -> None:
        cid = self.project_with({"main.py": "print(1)\n", "old.py": "x\n"})
        projects.move(cid, [("main.py", "app/main.py")])
        projects.remove(cid, ["old.py"])
        res = self.client.get(f"/api/conversations/{cid}/project.zip")
        names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
        self.assertEqual([n.split("/", 1)[1] for n in names], ["app/main.py"])

    def test_the_next_message_is_told_the_project_as_it_stands(self) -> None:
        cid = self.project_with({"main.py": "print(1)\n", "old.py": "x\n"})
        projects.remove(cid, ["old.py"])
        self.chat("what is there?", conversation_id=cid)
        last = self.engine.prompts[-1][-1]["content"]
        self.assertIn("Project files: main.py (1 line, v1).", last)
        self.assertNotIn("old.py", last)


if __name__ == "__main__":
    unittest.main()
