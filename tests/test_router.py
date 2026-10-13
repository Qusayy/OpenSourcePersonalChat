"""Which tool a message is sent to, and by which of the three tiers.

Needs the app's own requirements; without them the whole module is skipped.
The first two tiers are plain functions over text. The third is a pass through
the model, which is made elsewhere; what is tested here is what that pass is
given to read and how its reply is read.
"""

from __future__ import annotations

import json
import unittest

try:
    from app.llm import Completion
    from app.router import (
        ROUTE_SYSTEM,
        decision_grammar,
        decision_limit,
        decision_messages,
        free_route,
        heuristic,
        parse_action,
        parse_slash,
        repair_messages,
    )
    from app.tools.base import Rejection, available, decision_ceiling, tool_menu
    from tests import support
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

# (what was typed, the tool, its arguments, why)
PATTERNS = (
    ("https://example.com/a read this", "read_url", {"url": "https://example.com/a"},
     "found a link"),
    ("15% of 200", "calculator", {"expression": "15/100*200"}, "looks like arithmetic"),
    ("1247 * 89", "calculator", {"expression": "1247 * 89"}, "looks like arithmetic"),
    ("what is 12*12?", "calculator", {"expression": "12*12"}, "looks like arithmetic"),
    ("100 EUR to USD", "currency",
     {"amount": 100.0, "from_currency": "EUR", "to_currency": "USD"}, "currency pair"),
    ("10 km to miles", "convert_units", {"value": 10.0, "from_unit": "km", "to_unit": "mi"},
     "unit conversion"),
    ("weather in Paris?", "weather", {"location": "Paris"}, "weather query"),
    ("what time is it in Tokyo", "clock", {"timezone_name": "Tokyo"}, "time query"),
    ("what is the time", "clock", {"timezone_name": "UTC"}, "time query"),
    ("who is Ada Lovelace?", "wikipedia", {"query": "Ada Lovelace"}, "factual lookup"),
)

# Close to a pattern, and deliberately left for the model.
NOT_PATTERNS = ("", "   ", "what is the best language", "the weather", "12", "hello there",
                "who is the best tennis player of all time ever",
                "Suggest a name for my boat")


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


class HeuristicTests(unittest.TestCase):
    def test_each_pattern_names_its_tool_and_fills_its_arguments(self) -> None:
        for text, tool, args, note in PATTERNS:
            with self.subTest(text=text):
                call = heuristic(text)
                self.assertIsNotNone(call)
                self.assertEqual(
                    (call.name, call.args, call.via, call.note), (tool, args, "heuristic", note)
                )

    def test_a_near_miss_is_left_for_the_model(self) -> None:
        for text in NOT_PATTERNS:
            with self.subTest(text=text):
                self.assertIsNone(heuristic(text))

    def test_a_currency_pair_is_not_mistaken_for_a_unit_conversion(self) -> None:
        self.assertEqual(heuristic("250 gbp in eur").name, "currency")
        self.assertEqual(heuristic("250 kg in lb").name, "convert_units")


class FreeRouteTests(unittest.TestCase):
    """The two tiers that cost nothing, in order."""

    def test_a_command_is_routed_even_for_a_persona_without_tools(self) -> None:
        call, tier = free_route("/calc 2+2", tools_enabled=False)
        self.assertEqual((call.name, call.via, tier), ("calculator", "slash", "slash"))

    def test_without_tools_nothing_but_a_command_is_routed(self) -> None:
        self.assertEqual(free_route("15% of 200", tools_enabled=False), (None, None))

    def test_a_pattern_is_tried_where_tools_are_on(self) -> None:
        call, tier = free_route("15% of 200", tools_enabled=True)
        self.assertEqual((call.name, call.via, tier), ("calculator", "heuristic", "heuristic"))

    def test_a_command_is_tried_before_a_pattern(self) -> None:
        call, tier = free_route("/wiki 15% of 200", tools_enabled=True)
        self.assertEqual((call.name, tier), ("wikipedia", "slash"))

    def test_a_command_nobody_defined_can_still_match_a_pattern(self) -> None:
        call, tier = free_route("/foo https://example.org/x", tools_enabled=True)
        self.assertEqual((call.name, tier), ("read_url", "heuristic"))

    def test_what_neither_catches_is_left_for_the_model(self) -> None:
        for text in ("Suggest a name for my boat", "/foo bar", "the weather"):
            with self.subTest(text=text):
                self.assertEqual(free_route(text, tools_enabled=True), (None, None))


class DecisionPromptTests(unittest.TestCase):
    """What the model is given when it is asked to choose."""

    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def test_it_reads_a_menu_and_the_message_and_nothing_else(self) -> None:
        tools = available()
        self.assertEqual(
            decision_messages("Suggest a name for my boat", tools),
            [
                {"role": "system", "content": ROUTE_SYSTEM.format(menu=tool_menu(tools))},
                {"role": "user", "content": "Suggest a name for my boat"},
            ],
        )

    def test_a_long_message_is_read_only_at_its_start(self) -> None:
        long = "Suggest a name for my boat. " * 30
        self.assertEqual(decision_messages(long, available())[1]["content"], long[:600])

    def test_it_is_given_enough_tokens_to_finish_the_longest_reply(self) -> None:
        tools = available()
        self.assertEqual(decision_limit(tools), decision_ceiling(tools, ask=False))
        # A setting above that is honoured; one below it is not, because a
        # reply cut off before it closes is not a reply.
        support.with_settings(self, route_max_tokens=5000)
        self.assertEqual(decision_limit(tools), 5000)
        support.with_settings(self, route_max_tokens=1)
        self.assertEqual(decision_limit(tools), decision_ceiling(tools, ask=False))

    def test_with_outgoing_requests_off_only_local_tools_are_offered(self) -> None:
        support.with_settings(self, allow_outbound=False)
        tools = available()
        menu = decision_messages("hi", tools)[0]["content"]
        self.assertIn("- calculator(", menu)
        self.assertNotIn("- weather(", menu)
        self.assertNotIn("t-weather", decision_grammar(tools))

    def test_asking_the_user_is_not_on_offer(self) -> None:
        self.assertNotIn("t-ask", decision_grammar(available()))


class RepairPromptTests(unittest.TestCase):
    def test_a_second_try_is_the_first_prompt_the_reply_and_what_was_wrong(self) -> None:
        first = [{"role": "system", "content": "menu"}, {"role": "user", "content": "question"}]
        refused = Rejection(("a must be a number; got 'x'", "b is required and was not given"),
                            "for a person", "bad input")
        self.assertEqual(
            repair_messages(first, '{"tool": "t"}', refused),
            [
                {"role": "system", "content": "menu"},
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": '{"tool": "t"}'},
                {"role": "user", "content": (
                    "That call was not made: a must be a number; got 'x'; "
                    "b is required and was not given. Reply with one JSON object only."
                )},
            ],
        )
        # The first prompt is not touched: it is what the model has already read.
        self.assertEqual(len(first), 2)


def said(raw: str = "", **more) -> Completion:
    """A reply as the engine hands it back."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return Completion(parsed=parsed if isinstance(parsed, dict) else None, raw=raw, ms=12.5, **more)


class ParseActionTests(unittest.TestCase):
    """Reading the model's reply as something to do."""

    def test_a_call_to_a_real_tool_is_a_call_to_make(self) -> None:
        raw = '{"tool": "weather", "args": {"location": "Paris"}}'
        action = parse_action(said(raw))
        self.assertEqual((action.kind, action.raw, action.note), ("tool", raw, None))
        self.assertEqual(
            (action.call.name, action.call.args, action.call.via, action.call.ms, action.call.raw),
            ("weather", {"location": "Paris"}, "model", 12.5, raw),
        )

    def test_none_is_the_end_of_choosing(self) -> None:
        action = parse_action(said('{"tool": "none"}'))
        self.assertEqual((action.kind, action.call, action.note), ("final", None, None))

    def test_a_tool_that_does_not_exist_is_no_call_and_is_noted(self) -> None:
        action = parse_action(said('{"tool": "telescope", "args": {}}'))
        self.assertEqual((action.kind, action.call), ("final", None))
        self.assertEqual(action.note, "model picked an unknown tool: telescope")

    def test_a_reply_cut_off_before_it_closed_is_no_call_and_is_noted(self) -> None:
        cut = '{"tool": "wikipedia", "args": {"query": "the long'
        action = parse_action(said(cut))
        self.assertEqual((action.kind, action.raw), ("final", cut))
        self.assertEqual(action.note, "the reply was not a complete JSON object")

    def test_a_pass_that_failed_or_never_ran_is_no_call_and_has_its_own_reason(self) -> None:
        for reply in (said(error="ValueError: boom"), said(), said(cancelled=True)):
            with self.subTest(reply=reply):
                action = parse_action(reply)
                self.assertEqual((action.kind, action.call, action.note), ("final", None, None))

    def test_a_call_with_no_usable_arguments_is_a_call_with_none(self) -> None:
        for raw in ('{"tool": "clock"}', '{"tool": "clock", "args": null}',
                    '{"tool": "clock", "args": "UTC"}', '{"tool": "clock", "args": []}'):
            with self.subTest(raw=raw):
                action = parse_action(said(raw))
                self.assertEqual((action.kind, action.call.name, action.call.args), ("tool", "clock", {}))

if __name__ == "__main__":
    unittest.main()
