"""The system text, pinned to the character.

The model server reuses what it has already read only while each prompt
begins with the one before it, and a stored turn is replayed verbatim only
while its system text is the one in use now. So a change to it has a cost,
and this test makes every such change a deliberate one: when it fails, the
new hash goes here in the same commit as the change that made it.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import hashlib
import json
import unittest

try:
    from app.agent.calls import CALL_RULES, TOOLS_HEADER, tools_block
    from app.agent.transcript import system_for
    from app.tools.base import available
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

SYSTEM_SHA = "5ae2f34067a2764c"
MENU = [
    "calculator", "clock", "convert_units", "text_stats", "wikipedia", "weather",
    "currency", "web_search", "news", "read_url", "open_result", "read_file", "write_file",
    "edit_file", "move_file", "delete_file", "remember", "recall",
]


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class GoldenPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        support.with_settings(
            self, tools_enabled=True, allow_outbound=True, notes="conversation", sandbox="off",
        )
        self.menu = available()

    def test_the_menu_is_in_its_order(self) -> None:
        self.assertEqual([tool.name for tool in self.menu], MENU)

    def test_the_system_text_is_the_one_pinned(self) -> None:
        self.assertEqual(short_hash(system_for(self.menu)), SYSTEM_SHA)

    def test_the_tools_are_listed_as_the_models_template_lists_them(self) -> None:
        block = tools_block(self.menu)
        self.assertTrue(block.startswith(TOOLS_HEADER + "\n"))
        self.assertTrue(block.endswith("\n</tools>" + CALL_RULES))
        lines = block[len(TOOLS_HEADER) + 1:block.index("\n</tools>")].split("\n")
        self.assertEqual([json.loads(line)["function"]["name"] for line in lines], MENU)
        weather = json.loads(lines[MENU.index("weather")])
        self.assertEqual(weather["type"], "function")
        self.assertEqual(weather["function"]["parameters"]["required"], ["location"])


if __name__ == "__main__":
    unittest.main()
