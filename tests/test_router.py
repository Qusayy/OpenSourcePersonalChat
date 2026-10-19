"""What the user decides themselves: a slash command names its tool.

Needs the app's own requirements; without them the whole module is skipped.
Everything else is the model's to decide, in its own reply; nothing here
reads a message for a word that would pick a tool.
"""

from __future__ import annotations

import unittest

try:
    from app.agent.route import free_route, parse_slash, program_of
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

# (what was typed, the tool, its arguments)
COMMANDS = (
    ("/calc 2+2", "calculator", {"expression": "2+2"}),
    ("/math sqrt(2)", "calculator", {"expression": "sqrt(2)"}),
    ("/wiki Ada Lovelace", "wikipedia", {"query": "Ada Lovelace"}),
    ("/weather Lisbon", "weather", {"location": "Lisbon"}),
    ("/forecast Lisbon", "weather", {"location": "Lisbon"}),
    ("/read see https://e.org/x ok", "read_url", {"url": "https://e.org/x"}),
    ("/read e.org/x", "read_url", {"url": "e.org/x"}),
    ("/convert 10 km to mi", "convert_units", {"value": 10.0, "from_unit": "km", "to_unit": "mi"}),
    ("/fx 5 usd eur", "currency", {"amount": 5.0, "from_currency": "usd", "to_currency": "eur"}),
    ("/currency 1,250 GBP to JPY", "currency",
     {"amount": 1250.0, "from_currency": "GBP", "to_currency": "JPY"}),
    ("/clock", "clock", {"timezone_name": "UTC"}),
    ("/time Asia/Tokyo", "clock", {"timezone_name": "Asia/Tokyo"}),
    ("/stats one two", "text_stats", {"text": "one two"}),
    ("  /CALC 2+2  ", "calculator", {"expression": "2+2"}),
)

# Typed like a command, but not one that can be run as it stands.
NOT_COMMANDS = ("/foo bar", "/calc", "/wiki", "/weather", "/convert ten km", "/fx usd eur",
                "calc 2+2", "2+2 /calc")

class SlashTests(unittest.TestCase):
    def test_each_command_names_its_tool_and_fills_its_arguments(self) -> None:
        for text, tool, args in COMMANDS:
            with self.subTest(text=text):
                call, _ = parse_slash(text)
                self.assertIsNotNone(call)
                self.assertEqual((call.name, call.args, call.via), (tool, args, "slash"))

    def test_what_cannot_be_run_as_typed_is_not_a_command(self) -> None:
        for text in NOT_COMMANDS:
            with self.subTest(text=text):
                self.assertIsNone(parse_slash(text)[0])


class FreeRouteTests(unittest.TestCase):
    """What is decided without the model: only what the user named."""

    def test_a_command_is_routed(self) -> None:
        call, tier = free_route("/calc 2+2")
        self.assertEqual((call.name, call.via, tier), ("calculator", "slash", "slash"))

    def test_everything_else_is_the_models_to_decide(self) -> None:
        # Arithmetic, a link, a greeting, a question about the news: none of
        # them is read for a word that would pick a tool.
        for text in ("15% of 200", "https://example.org/x read this", "Hi", "Thanks!",
                     "Hi what are the latest news", "weather in Paris?", "who is Ada Lovelace?",
                     "/foo https://example.org/x", "Suggest a name for my boat"):
            with self.subTest(text=text):
                self.assertEqual(free_route(text), (None, None))


class PythonCommandTests(unittest.TestCase):
    def test_a_program_given_with_the_command_is_run_as_it_was_given(self) -> None:
        call, _ = parse_slash("/python\n```python\nprint(sum(range(101)))\n```")
        self.assertEqual((call.name, call.args, call.via),
                         ("python", {"code": "print(sum(range(101)))\n"}, "slash"))

    def test_a_program_without_a_fence_is_run_as_typed(self) -> None:
        call, _ = parse_slash("/py print(1)")
        self.assertEqual(call.args, {"code": "print(1)"})

    def test_a_command_with_nothing_to_run_is_not_one(self) -> None:
        self.assertIsNone(parse_slash("/python   ")[0])

    def test_a_fenced_program_is_found_in_text(self) -> None:
        self.assertEqual(program_of("x\n```python\nprint(2)\n```\ny"), "print(2)\n")
        self.assertEqual(program_of("no code"), "")


if __name__ == "__main__":
    unittest.main()
