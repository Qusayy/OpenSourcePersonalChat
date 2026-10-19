"""The prompt the model reads, written out by the app, and the template a
model server is given to write the same thing for anyone who asks it to.

Needs the app's own requirements; without them the whole module is skipped.
The template is read with jinja2 where it is installed (llama-server reads it
with its own jinja, which these lines keep within).
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    from app.chatml import THINK_BLOCK, THINK_OPEN, opener, render_chatml
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

try:
    import jinja2
except ImportError:  # pragma: no cover - CI installs it
    jinja2 = None

TEMPLATE = Path(__file__).resolve().parent.parent / "deploy" / "templates" / "qwen3.5.jinja"
FIRST = [{"role": "system", "content": "Answer briefly."}, {"role": "user", "content": "Hi"}]
THOUGHT = "<think>\nThey said hi.\n</think>\n\nHello."
LATER = [*FIRST, {"role": "assistant", "content": THOUGHT}, {"role": "user", "content": "And?"}]


class ChatmlTests(unittest.TestCase):
    def test_each_message_begins_with_the_last_and_its_answer(self) -> None:
        # The answer is kept as it was written, after its opener, so the next
        # prompt begins with the one it was written to.
        first = render_chatml(FIRST, thinking="on")
        self.assertTrue(render_chatml(LATER, thinking="on").startswith(
            first + THOUGHT[len(THINK_OPEN):] + "<|im_end|>\n"
        ))

    def test_an_answer_is_written_as_it_was_kept_whatever_the_setting(self) -> None:
        for thinking in ("on", "off", "none"):
            with self.subTest(thinking=thinking):
                later = render_chatml(LATER, thinking=thinking)
                self.assertIn("<|im_start|>assistant\n" + THOUGHT + "<|im_end|>\n", later)

    def test_the_answer_to_come_begins_with_its_opener(self) -> None:
        self.assertTrue(render_chatml(FIRST, thinking="on").endswith("<|im_start|>assistant\n" + THINK_OPEN))
        self.assertTrue(render_chatml(FIRST, thinking="off").endswith("<|im_start|>assistant\n" + THINK_BLOCK))
        self.assertTrue(render_chatml(FIRST, thinking="none").endswith("<|im_start|>assistant\n"))

    def test_what_is_already_written_goes_after_the_opener(self) -> None:
        self.assertTrue(render_chatml(FIRST, prefix="Hel", thinking="off").endswith(THINK_BLOCK + "Hel"))

    def test_the_setting_says_how_the_model_thinks(self) -> None:
        support.with_settings(self, thinking="on")
        self.assertEqual(opener(), THINK_OPEN)
        self.assertEqual(render_chatml(LATER), render_chatml(LATER, thinking="on"))

    def test_the_older_setting_still_means_an_empty_block(self) -> None:
        support.with_settings(self, thinking="", think_block=True)
        self.assertEqual(opener(), THINK_BLOCK)
        support.with_settings(self, thinking="", think_block=False)
        self.assertEqual(opener(), "")


@unittest.skipIf(jinja2 is None, "jinja2 is not installed")
class TemplateTests(unittest.TestCase):
    def render(self, messages: list[dict]) -> str:
        template = jinja2.Environment().from_string(TEMPLATE.read_text(encoding="utf-8"))
        return template.render(messages=messages, add_generation_prompt=True)

    def test_the_server_writes_what_the_app_writes_with_thinking_on(self) -> None:
        for messages in (FIRST, LATER):
            with self.subTest(turns=len(messages)):
                self.assertEqual(self.render(messages), render_chatml(messages, thinking="on"))
