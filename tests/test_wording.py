"""Other texts for the model to choose by, for one run of the suite.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from app.agent import transcript
    from app.agent.skills import SKILLS, offered
    from app.agent.transcript import system_for
    from app.tools.base import REGISTRY, available
    from app.tools.grammar import tool_menu
    from evals.runner import run_suite
    from evals.tasks import load_tasks
    from evals.wording import applied, load
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

RULES = Path(__file__).resolve().parent.parent / "evals" / "rules"


class WordingFileTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "w.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_lines_that_name_a_text_replace_it_and_the_rest_are_the_rules(self) -> None:
        wording = load(self.write(
            "@system: Be brief.\n@tool clock: The time.\n@skill compare: Two things.\n\n"
            'Choose.\nTools:\n{menu}\nReply {"tool": "none"} when no tool is needed.\n\n'
        ))
        self.assertEqual(wording.system, "Be brief.")
        self.assertEqual(wording.tools, {"clock": "The time."})
        self.assertEqual(wording.skills, {"compare": "Two things."})
        self.assertEqual(
            wording.rules.format(menu="- t(): x"),
            'Choose.\nTools:\n- t(): x\nReply {"tool": "none"} when no tool is needed.',
        )

    def test_a_text_can_be_emptied(self) -> None:
        self.assertEqual(load(self.write("@system:\n")).system, "")

    def test_a_file_of_overrides_alone_keeps_the_rules(self) -> None:
        self.assertIsNone(load(self.write("@tool clock: The time.\n")).rules)

    def test_rules_must_say_once_where_the_menu_goes(self) -> None:
        for body in ("Choose a tool.", "{menu}\n{menu}"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                load(self.write(body))

    def test_the_same_file_has_the_same_digest(self) -> None:
        path = self.write("@tool clock: The time.\n")
        self.assertEqual(load(path).digest, load(path).digest)
        self.assertEqual(len(load(path).digest), 12)


class AppliedTests(unittest.TestCase):
    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def test_inside_the_model_reads_the_new_texts_and_after_it_the_old(self) -> None:
        before = system_for(offered(available()))
        wording = load(RULES / "default-none.txt")
        with applied(wording):
            inside = system_for(offered(available()))
            self.assertTrue(inside.startswith(transcript.SYSTEM + "\n\nMost messages need no tool."))
            self.assertIn("- clock(timezone_name?): " + wording.tools["clock"], inside)
            self.assertIn('reply {"tool": "none"}', inside)
        self.assertEqual(system_for(offered(available())), before)
        self.assertEqual(transcript.LOOP_RULES.count("{menu}"), 1)

    def test_the_system_text_is_replaced_and_put_back(self) -> None:
        wording = load(RULES / "old-framing.txt")
        system = transcript.SYSTEM
        with applied(wording):
            self.assertEqual(transcript.SYSTEM, "")
        self.assertEqual(transcript.SYSTEM, system)

    def test_a_name_that_is_not_there_is_refused_before_anything_changes(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "w.txt"
        path.write_text("@tool telescope: Look up.\n", encoding="utf-8")
        with self.assertRaises(ValueError), applied(load(path)):
            pass
        self.assertNotIn("telescope", REGISTRY)

    def test_every_candidate_can_be_read_and_put_in_front_of_the_model(self) -> None:
        for path in sorted(RULES.glob("*.txt")):
            with self.subTest(path.name), applied(load(path)):
                menu = tool_menu(offered(available()))
                text = system_for(offered(available()))
                self.assertIn(menu, text)
                for tool in available():
                    self.assertEqual(text.count(f"- {tool.name}("), 1)
                self.assertIn(SKILLS["compare"].description, text)


class CommandTests(unittest.TestCase):
    def test_a_file_that_cannot_be_used_stops_the_command_before_it_runs(self) -> None:
        import contextlib
        import io

        from scripts import eval_agent

        for name in ("no-such-file.txt", None):
            with self.subTest(name=name):
                if name is None:
                    folder = tempfile.TemporaryDirectory()
                    self.addCleanup(folder.cleanup)
                    path = Path(folder.name) / "w.txt"
                    path.write_text("Choose a tool.", encoding="utf-8")
                    name = str(path)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    eval_agent.main(["--rules", name])
                self.assertEqual(caught.exception.code, 2)


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_report_says_which_wording_it_was_run_with(self) -> None:
        support.no_network(self)
        wording = load(RULES / "default-none.txt")
        report = await run_suite(load_tasks(), engine_kind="scripted", categories=["no-tool"],
                                 wording=wording)
        self.assertEqual(report["rules"], {"file": wording.path, "sha256": wording.digest})
        plain = await run_suite(load_tasks(), engine_kind="scripted", categories=["no-tool"])
        self.assertIsNone(plain["rules"])


if __name__ == "__main__":
    unittest.main()
