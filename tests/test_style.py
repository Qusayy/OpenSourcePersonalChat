"""How the model's text is sampled, and what it is told about the shape of
an answer.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    from app.agent.context import Part
    from app.agent.style import CODE, PROSE, PROSE_THINKING, sampling
    from app.agent.transcript import SHAPE, Transcript, project_line, sent_at, system_for
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


class SamplingTests(unittest.TestCase):
    def test_nothing_here_reads_the_message(self) -> None:
        # The model shapes its own answer from the system text. What is
        # chosen here follows only what it is writing: prose or code.
        import app.agent.style as style

        self.assertFalse(hasattr(style, "style_for"))
        self.assertFalse(hasattr(style, "STYLES"))
        self.assertIn(SHAPE, system_for([]))

    def test_prose_is_sampled_as_qwen_samples_it_thinking_and_not(self) -> None:
        self.assertEqual(sampling("prose", "on"), PROSE_THINKING)
        self.assertEqual(sampling("prose", "off"), PROSE)
        self.assertEqual(sampling("prose", "none"), PROSE)
        self.assertEqual(PROSE_THINKING["temperature"], 1.0)
        self.assertEqual(PROSE["temperature"], 0.7)
        self.assertEqual(PROSE["presence_penalty"], 1.5)

    def test_code_has_no_presence_penalty_so_an_edit_can_copy_exactly(self) -> None:
        for thinking in ("on", "off", "none"):
            with self.subTest(thinking=thinking):
                self.assertEqual(sampling("code", thinking), CODE)
        self.assertEqual(CODE["presence_penalty"], 0.0)
        self.assertEqual(CODE["temperature"], 0.6)

    def test_what_is_handed_out_is_a_copy(self) -> None:
        settings = sampling("prose")
        settings["temperature"] = 9
        self.assertNotEqual(sampling("prose")["temperature"], 9)


class SentTests(unittest.TestCase):
    def test_the_time_a_message_was_sent_is_said_in_its_own_zone(self) -> None:
        utc = datetime(2026, 10, 17, 14, 3, tzinfo=timezone.utc)
        self.assertEqual(sent_at(utc), Part("user", "now", "(Sent Sat 17 Oct 2026, 14:03 UTC.)"))
        later = datetime(2026, 10, 17, 15, 3, tzinfo=timezone(timedelta(hours=1), "BST"))
        self.assertEqual(sent_at(later).content, "(Sent Sat 17 Oct 2026, 15:03 BST.)")

    def test_a_run_keeps_the_message_and_when_it_was_sent_as_its_own(self) -> None:
        parts = [Part("system", "instruction", "S"), Part("user", "question", "Hi"),
                 sent_at(datetime(2026, 1, 1, tzinfo=timezone.utc))]
        transcript = Transcript(parts, own=2)
        self.assertEqual([p.kind for p in transcript.run_parts()], ["question", "now"])


class ProjectLineTests(unittest.TestCase):
    def test_the_project_is_named_file_by_file_with_its_version(self) -> None:
        line = project_line([
            {"path": "app/main.py", "lines": 8, "version": 2},
            {"path": "requirements.txt", "lines": 1, "version": 1},
        ])
        self.assertEqual(
            line.content, "Project files: app/main.py (8 lines, v2), requirements.txt (1 line, v1).",
        )
        self.assertNotIn("whole file", line.content)

    def test_no_project_no_line(self) -> None:
        self.assertIsNone(project_line([]))


if __name__ == "__main__":
    unittest.main()
