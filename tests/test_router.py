"""Which tool a message is sent to, and by which of the three tiers.

Needs the app's own requirements; without them the whole module is skipped.
The first two tiers are plain functions over text. The third asks an engine,
which here plays back a prepared choice.
"""

from __future__ import annotations

import unittest

try:
    from app.router import heuristic, parse_slash, route
    from app.tools.base import decision_ceiling
    from tests import support
    from tests.support import PlayedEngine
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


class RouteTests(unittest.IsolatedAsyncioTestCase):
    """The tiers in order, cheapest first."""

    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, allow_outbound=True)

    async def test_a_command_is_routed_even_for_a_persona_without_tools(self) -> None:
        engine = PlayedEngine()
        call, told = await route("/calc 2+2", tools_enabled=False, engine=engine)
        self.assertEqual((call.name, call.via), ("calculator", "slash"))
        self.assertEqual(told, {"tier": "slash", "ms": 0.0})
        self.assertEqual(engine.route_prompts, [])

    async def test_without_tools_nothing_but_a_command_is_routed(self) -> None:
        engine = PlayedEngine()
        call, told = await route("15% of 200", tools_enabled=False, engine=engine)
        self.assertIsNone(call)
        self.assertEqual(told, {"tier": None, "ms": 0.0})
        self.assertEqual(engine.route_prompts, [])

    async def test_a_pattern_is_tried_before_the_model(self) -> None:
        engine = PlayedEngine(decision={"tool": "wikipedia", "args": {"query": "percent"}})
        call, told = await route("15% of 200", tools_enabled=True, engine=engine)
        self.assertEqual((call.name, call.via), ("calculator", "heuristic"))
        self.assertEqual(told, {"tier": "heuristic", "ms": 0.0})
        self.assertEqual(engine.route_prompts, [])

    async def test_the_model_is_asked_last_and_its_choice_is_kept_with_its_cost(self) -> None:
        raw = '{"tool": "weather", "args": {"location": "Paris"}}'
        engine = PlayedEngine(decision=raw)
        call, told = await route("Do I need a coat in Paris", tools_enabled=True, engine=engine)
        self.assertEqual(
            (call.name, call.args, call.via, call.ms, call.raw),
            ("weather", {"location": "Paris"}, "model", support.ROUTE_MS, raw),
        )
        self.assertEqual(
            (told["tier"], told["ms"], told["raw"]), ("model", support.ROUTE_MS, raw)
        )
        self.assertEqual(len(engine.route_prompts), 1)

    async def test_the_pass_itself_goes_back_with_what_it_cost(self) -> None:
        engine = PlayedEngine()
        _, told = await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertEqual(set(told), {"tier", "ms", "raw", "text", "completion"})
        self.assertEqual(told["text"], "Suggest a name for my boat")
        self.assertEqual(told["completion"].raw, '{"tool": "none"}')
        self.assertEqual(told["completion"].ms, support.ROUTE_MS)

    async def test_none_from_the_model_is_no_call(self) -> None:
        engine = PlayedEngine()
        call, told = await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertIsNone(call)
        self.assertEqual(
            (told["tier"], told["ms"], told["raw"]),
            ("model", support.ROUTE_MS, '{"tool": "none"}'),
        )

    async def test_a_tool_that_does_not_exist_is_no_call_and_is_noted(self) -> None:
        engine = PlayedEngine(decision={"tool": "telescope", "args": {}})
        call, told = await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertIsNone(call)
        self.assertEqual(told["note"], "model picked an unknown tool: telescope")

    async def test_a_reply_that_is_not_json_is_no_call(self) -> None:
        engine = PlayedEngine(decision='{"tool": "wikipedia", "args": {"query": "the long')
        call, told = await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertIsNone(call)
        self.assertEqual(told["tier"], "model")

    async def test_only_the_start_of_a_long_reply_is_kept_to_look_at(self) -> None:
        engine = PlayedEngine(decision="x" * 500)
        _, told = await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertEqual(told["raw"], "x" * 200)

    async def test_the_pass_is_given_enough_tokens_to_finish_the_longest_reply(self) -> None:
        engine = PlayedEngine()
        await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertEqual(engine.route_limits, [decision_ceiling(ask=False)])
        self.assertNotIn("t-ask", engine.grammars[0])
        # A setting above that is honoured; one below it is not, because a
        # reply cut off before it closes is not a reply.
        support.with_settings(self, route_max_tokens=5000)
        await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        self.assertEqual(engine.route_limits[-1], 5000)

    async def test_with_no_engine_the_model_tier_is_skipped(self) -> None:
        call, told = await route("Suggest a name for my boat", tools_enabled=True, engine=None)
        self.assertIsNone(call)
        self.assertEqual(told, {"tier": None, "ms": 0.0})

    async def test_the_model_can_be_left_out_on_request(self) -> None:
        engine = PlayedEngine(decision={"tool": "weather", "args": {"location": "Paris"}})
        call, _ = await route(
            "Do I need a coat in Paris", tools_enabled=True, engine=engine, allow_model=False
        )
        self.assertIsNone(call)
        self.assertEqual(engine.route_prompts, [])

    async def test_with_outgoing_requests_off_the_model_is_offered_only_local_tools(self) -> None:
        support.with_settings(self, allow_outbound=False)
        engine = PlayedEngine()
        await route("Suggest a name for my boat", tools_enabled=True, engine=engine)
        menu = engine.route_prompts[0][0]["content"]
        self.assertIn("- calculator(", menu)
        self.assertNotIn("- weather(", menu)
        self.assertNotIn("t-weather", engine.grammars[0])


if __name__ == "__main__":
    unittest.main()
