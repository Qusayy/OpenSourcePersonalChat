"""The system text and the choice grammar, pinned to the character.

The model server reuses what it has already read only while each prompt
begins with the one before it, and a stored turn is replayed verbatim only
while its system text is the one in use now. So a change to either text has
a cost, and this test makes every such change a deliberate one: when it
fails, the new hash goes here in the same commit as the change that made it.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import hashlib
import unittest

try:
    from app.agent.route import decision_grammar
    from app.agent.skills import offered
    from app.agent.transcript import system_for
    from app.tools.base import available
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

SYSTEM_SHA = "b20f281389eb871a"
GRAMMAR_SHA = "5e16a35fb62e2f18"
MENU = [
    "calculator", "clock", "convert_units", "text_stats", "wikipedia", "weather",
    "currency", "web_search", "news", "read_url", "open_result", "read_file", "remember",
    "recall", "compare",
]


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class GoldenPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        support.with_settings(self, tools_enabled=True, allow_outbound=True, notes="conversation")
        self.menu = offered(available())

    def test_the_menu_is_in_its_order(self) -> None:
        self.assertEqual([choice.name for choice in self.menu], MENU)

    def test_the_system_text_is_the_one_pinned(self) -> None:
        self.assertEqual(short_hash(system_for(self.menu)), SYSTEM_SHA)

    def test_the_choice_grammar_is_the_one_pinned(self) -> None:
        self.assertEqual(short_hash(decision_grammar(self.menu)), GRAMMAR_SHA)


if __name__ == "__main__":
    unittest.main()
