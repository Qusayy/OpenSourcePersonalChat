"""Other texts for the model to read, for one run of the suite.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from app.agent import transcript
    from app.agent.transcript import system_for
    from app.tools.base import REGISTRY, available
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
            "@system: Be brief.\n@tool clock: The time.\n\nUse a tool for news.\nOtherwise answer.\n\n"
        ))
        self.assertEqual(wording.system, "Be brief.")
        self.assertEqual(wording.tools, {"clock": "The time."})
        self.assertEqual(wording.rules, "Use a tool for news.\nOtherwise answer.")

    def test_a_text_can_be_emptied(self) -> None:
        self.assertEqual(load(self.write("@system:\n")).system, "")

    def test_a_file_of_overrides_alone_keeps_the_rules(self) -> None:
        self.assertIsNone(load(self.write("@tool clock: The time.\n")).rules)

    def test_the_same_file_has_the_same_digest(self) -> None:
        path = self.write("@tool clock: The time.\n")
        self.assertEqual(load(path).digest, load(path).digest)
        self.assertEqual(len(load(path).digest), 12)


class AppliedTests(unittest.TestCase):
    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def test_inside_the_model_reads_the_new_texts_and_after_it_the_old(self) -> None:
        before = system_for(available())
        rules = transcript.TOOL_RULES
        wording = load(RULES / "fewer-calls.txt")
        with applied(wording):
            inside = system_for(available())
            self.assertIn(wording.rules, inside)
            self.assertNotIn(rules, inside)
            self.assertIn(f'"description": "{wording.tools["clock"]}"', inside)
        self.assertEqual(system_for(available()), before)
        self.assertNotIn(wording.rules, before)

    def test_who_the_model_is_is_replaced_and_put_back(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "w.txt"
        path.write_text("@system: You are Wren.\n", encoding="utf-8")
        who = transcript.WHO
        with applied(load(path)):
            self.assertEqual(transcript.WHO, "You are Wren.")
            self.assertIn("\n\nYou are Wren.\n", system_for(available()))
        self.assertEqual(transcript.WHO, who)

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
                text = system_for(available())
                for tool in available():
                    self.assertEqual(text.count(f'"name": "{tool.name}"'), 1)


class CommandTests(unittest.TestCase):
    def test_a_file_that_cannot_be_used_stops_the_command_before_it_runs(self) -> None:
        import contextlib
        import io

        from scripts import eval_agent

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            eval_agent.main(["--rules", "no-such-file.txt"])
        self.assertEqual(caught.exception.code, 2)


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_report_says_which_wording_it_was_run_with(self) -> None:
        support.no_network(self)
        wording = load(RULES / "fewer-calls.txt")
        report = await run_suite(load_tasks(), engine_kind="scripted", categories=["no-tool"],
                                 wording=wording)
        self.assertEqual(report["rules"], {"file": wording.path, "sha256": wording.digest})
        plain = await run_suite(load_tasks(), engine_kind="scripted", categories=["no-tool"])
        self.assertIsNone(plain["rules"])


if __name__ == "__main__":
    unittest.main()
