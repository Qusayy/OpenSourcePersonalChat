"""How an answer is written: shaped by the model, sampled and held here.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    from app.agent.context import Part
    from app.agent.style import LOOKED_UP_TEMPERATURE, STYLES
    from app.agent.transcript import SHAPE, Transcript, sent_at, system_for
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


class StyleTests(unittest.TestCase):
    def test_nothing_here_reads_the_message(self) -> None:
        # The model shapes its own answer from the system text; the one
        # choice of style it makes is to write files.
        import app.agent.style as style

        self.assertFalse(hasattr(style, "style_for"))
        self.assertEqual(set(STYLES), {"answer", "files"})
        self.assertEqual(STYLES["answer"].cue, "")
        self.assertIn(SHAPE, system_for([]))

    def test_files_are_held_to_their_grammar_and_nothing_else_is(self) -> None:
        from app.projects.parse import GRAMMAR

        self.assertEqual(STYLES["files"].grammar, GRAMMAR)
        self.assertIsNone(STYLES["answer"].grammar)

    def test_an_answer_from_a_lookup_is_held_cooler_whatever_its_style(self) -> None:
        for style in STYLES.values():
            with self.subTest(style.id):
                self.assertLessEqual(style.sampling(looked_up=True)["temperature"], LOOKED_UP_TEMPERATURE)
                self.assertEqual(style.sampling()["temperature"], style.temperature)
                self.assertEqual(style.sampling()["max_tokens"], style.max_tokens)

    def test_the_style_never_reaches_the_system_text(self) -> None:
        for style in STYLES.values():
            with self.subTest(style.id):
                if style.cue:
                    self.assertNotIn(style.cue, system_for([]))


class SentTests(unittest.TestCase):
    def test_the_time_a_message_was_sent_is_said_in_its_own_zone(self) -> None:
        utc = datetime(2026, 10, 17, 14, 3, tzinfo=timezone.utc)
        self.assertEqual(sent_at(utc), Part("user", "now", "(Sent Sat 17 Oct 2026, 14:03 UTC.)"))
        later = datetime(2026, 10, 17, 15, 3, tzinfo=timezone(timedelta(hours=1), "BST"))
        self.assertEqual(sent_at(later).content, "(Sent Sat 17 Oct 2026, 15:03 BST.)")

    def test_a_run_keeps_the_message_and_when_it_was_sent_as_its_own(self) -> None:
        parts = [Part("system", "instruction", "S"), Part("user", "question", "Hi"),
                 sent_at(datetime(2026, 1, 1, tzinfo=timezone.utc))]
        transcript = Transcript.fitted(parts, own=2)
        self.assertEqual([p.kind for p in transcript.run_parts()], ["question", "now"])


if __name__ == "__main__":
    unittest.main()
