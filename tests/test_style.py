"""How an answer is written: read from the message, never asked of the model.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    from app.agent.context import Part
    from app.agent.style import LOOKED_UP_TEMPERATURE, STYLES, style_for
    from app.agent.transcript import Transcript, sent_at, system_for
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


class StyleTests(unittest.TestCase):
    def test_each_message_is_answered_in_the_shape_it_asks_for(self) -> None:
        for message, style in (
            ("how do I reverse a list in python", "code"),
            ("write a function that sorts a list", "code"),
            ("fix this: TypeError: 'NoneType' object is not subscriptable", "code"),
            ("```js\nconsole.log(1)\n``` why does this print twice?", "code"),
            ("build me a small flask todo app", "code"),
            ("give me 5 ideas for a birthday party", "list"),
            ("suggest names for my boat", "list"),
            ("what is the capital of France", "concise"),
            ("who won the 2022 world cup", "concise"),
            ("briefly, what is DNS?", "concise"),
            ("hi", "concise"),
            ("why is the sky blue", "explain"),
            ("explain quantum entanglement to me", "explain"),
            ("how does a hash map work", "explain"),
        ):
            with self.subTest(message):
                self.assertEqual(style_for(message).id, style)

    def test_words_that_only_look_like_code_do_not_make_it_code(self) -> None:
        for message in ("how to go to Paris by train", "I love listening to Swift",
                        "explain the class system in India"):
            with self.subTest(message):
                self.assertNotEqual(style_for(message).id, "code")

    def test_an_answer_from_a_lookup_is_held_cooler_whatever_its_style(self) -> None:
        for style in STYLES.values():
            with self.subTest(style.id):
                self.assertLessEqual(style.sampling(looked_up=True)["temperature"], LOOKED_UP_TEMPERATURE)
                self.assertEqual(style.sampling()["temperature"], style.temperature)
                self.assertEqual(style.sampling()["max_tokens"], style.max_tokens)

    def test_the_style_never_reaches_the_system_text(self) -> None:
        for style in STYLES.values():
            with self.subTest(style.id):
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
