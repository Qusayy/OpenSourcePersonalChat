"""The chat format the window is counted in, and the template a model server is
given to write it.

Needs the app's own requirements; without them the whole module is skipped.
The template is read with jinja2 where it is installed (llama-server reads it
with its own jinja, which these lines keep within).
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    from app.chatml import THINK_BLOCK, render_chatml
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

try:
    import jinja2
except ImportError:  # pragma: no cover - CI installs it
    jinja2 = None

TEMPLATE = Path(__file__).resolve().parent.parent / "deploy" / "templates" / "qwen3.5.jinja"
FIRST = [{"role": "system", "content": "Answer briefly."}, {"role": "user", "content": "Hi"}]
LATER = [*FIRST, {"role": "assistant", "content": "Hello."}, {"role": "user", "content": "And?"}]


class ChatmlTests(unittest.TestCase):
    def test_each_message_begins_with_the_last_and_its_answer(self) -> None:
        for think in (False, True):
            with self.subTest(think=think):
                first = render_chatml(FIRST, think=think)
                self.assertTrue(render_chatml(LATER, think=think).startswith(first + "Hello."))

    def test_with_the_block_every_answer_begins_empty_of_thought(self) -> None:
        later = render_chatml(LATER, think=True)
        self.assertIn("<|im_start|>assistant\n" + THINK_BLOCK + "Hello.<|im_end|>\n", later)
        self.assertTrue(later.endswith("<|im_start|>assistant\n" + THINK_BLOCK))
        self.assertNotIn("<think>", render_chatml(LATER, think=False))

    def test_the_setting_says_which(self) -> None:
        support.with_settings(self, think_block=True)
        self.assertEqual(render_chatml(LATER), render_chatml(LATER, think=True))


@unittest.skipIf(jinja2 is None, "jinja2 is not installed")
class TemplateTests(unittest.TestCase):
    def render(self, messages: list[dict]) -> str:
        template = jinja2.Environment().from_string(TEMPLATE.read_text(encoding="utf-8"))
        return template.render(messages=messages, add_generation_prompt=True)

    def test_the_server_is_given_what_the_window_is_counted_in(self) -> None:
        for messages in (FIRST, LATER):
            with self.subTest(turns=len(messages)):
                self.assertEqual(self.render(messages), render_chatml(messages, think=True))
