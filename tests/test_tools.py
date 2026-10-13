"""Calling a tool by name, and the grammar and menu built from the registry.

Needs the app's own requirements; without them the whole module is skipped.
No tool here reaches the network: the ones that would are replaced, and a
made-up tool stands in wherever the dispatcher itself is what is being tested.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

try:
    from app.tools import base
    from app.tools.base import Arg, Tool, ToolResult, call_tool, tool_menu, tools_to_gbnf
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, allow_outbound=True)
        self.given: list[dict] = []

    def probe(self, run=None, *, network: bool = False, card: str = "probe") -> None:
        """Register a made-up tool for this test. By default it records what it
        was called with and succeeds without naming a card."""

        async def record(**kwargs) -> ToolResult:
            self.given.append(kwargs)
            return ToolResult(True, "probed")

        tool = Tool(
            name="probe",
            label="Probe",
            glyph="?",
            description="A tool that exists only in this test.",
            args=(
                Arg("word", "string", True),
                Arg("count", "number", False, default=1),
                Arg("note", "string", False),
            ),
            run=run or record,
            card=card,
            network=network,
        )
        patcher = mock.patch.dict(base.REGISTRY, {"probe": tool})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_tool_nobody_registered_is_refused_by_name(self) -> None:
        result = await call_tool("telescope", {})
        self.assertIs(result.ok, False)
        self.assertEqual(result.summary, "There is no tool called 'telescope'.")
        self.assertEqual(result.error, "unknown tool")

    async def test_a_tool_that_goes_out_is_refused_when_outgoing_requests_are_off(self) -> None:
        support.with_settings(self, allow_outbound=False)
        self.probe(network=True)
        result = await call_tool("probe", {"word": "a"})
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, "offline mode")
        self.assertEqual(result.ms, 0.0)
        self.assertEqual(self.given, [])

    async def test_arguments_the_tool_never_declared_are_dropped(self) -> None:
        self.probe()
        await call_tool("probe", {"word": "a", "colour": "red", "count": 3})
        self.assertEqual(self.given, [{"word": "a", "count": 3}])

    async def test_a_default_fills_an_argument_that_was_left_out(self) -> None:
        self.probe()
        await call_tool("probe", {"word": "a"})
        self.assertEqual(self.given, [{"word": "a", "count": 1}])

    async def test_a_missing_argument_is_named_the_way_a_person_would(self) -> None:
        result = await call_tool("calculator", {})
        self.assertIs(result.ok, False)
        self.assertEqual(result.summary, "Calculator needs an expression.")
        self.assertEqual(result.error, "missing input")
        self.assertIsNone(result.card)

    async def test_several_missing_arguments_are_named_together(self) -> None:
        result = await call_tool("convert_units", {"colour": "red"})
        self.assertEqual(
            result.summary,
            "Convert needs a value and a unit to convert from and a unit to convert to.",
        )

    async def test_no_arguments_at_all_is_the_same_as_none_given(self) -> None:
        result = await call_tool("calculator", None)
        self.assertEqual(result.error, "missing input")

    async def test_a_value_reaches_the_tool_in_the_type_it_arrived_in(self) -> None:
        self.probe()
        await call_tool("probe", {"word": 7, "count": "12"})
        self.assertEqual(self.given, [{"word": 7, "count": "12"}])

    async def test_a_tool_slower_than_the_limit_is_given_up_on(self) -> None:
        support.with_settings(self, tool_timeout=0.02)

        async def slow(**kwargs) -> ToolResult:
            await asyncio.sleep(5)
            return ToolResult(True, "too late")

        self.probe(slow)
        result = await call_tool("probe", {"word": "a"})
        self.assertIs(result.ok, False)
        self.assertEqual(
            result.summary,
            "Probe gave up after 0.02 seconds. The source may be slow or unreachable — try again.",
        )
        self.assertEqual(result.error, "timeout")
        self.assertGreater(result.ms, 0)

    async def test_a_tool_that_raises_is_a_failed_result_not_an_exception(self) -> None:
        async def broken(**kwargs) -> ToolResult:
            raise ValueError("boom")

        self.probe(broken)
        result = await call_tool("probe", {"word": "a"})
        self.assertIs(result.ok, False)
        self.assertEqual(result.summary, "Probe could not finish. Try again, or ask without it.")
        self.assertEqual(result.error, "ValueError: boom")

    async def test_a_result_is_timed_and_takes_the_tools_card_if_it_named_none(self) -> None:
        self.probe()
        result = await call_tool("probe", {"word": "a"})
        self.assertIs(result.ok, True)
        self.assertEqual(result.card, "probe")
        self.assertGreater(result.ms, 0)

    async def test_a_failure_the_tool_reported_itself_is_passed_through(self) -> None:
        result = await call_tool("calculator", {"expression": "1/0"})
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, "division by zero")
        self.assertEqual(result.card, "calculator")

    async def test_the_clock_needs_nothing_and_answers_in_utc(self) -> None:
        result = await call_tool("clock", {})
        self.assertIs(result.ok, True)
        self.assertEqual(result.data["zone"], "UTC")
        self.assertTrue(result.summary.endswith(" in UTC."))


class GrammarTests(unittest.TestCase):
    """The grammar a routing pass is held to, and the menu it reads."""

    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def rules(self, tools=None) -> dict[str, str]:
        out = {}
        for line in tools_to_gbnf(tools).splitlines():
            name, sep, body = line.partition(" ::= ")
            if sep:
                out[name] = body
        return out

    def test_the_root_offers_every_tool_in_registry_order_and_then_none(self) -> None:
        self.assertEqual(
            self.rules()["root"],
            "t-calculator | t-clock | t-convert-units | t-text-stats | t-wikipedia | "
            "t-weather | t-currency | t-read-url | t-none",
        )

    def test_none_is_a_choice_with_no_arguments(self) -> None:
        self.assertEqual(self.rules()["t-none"], r'"{\"tool\": \"none\"}"')

    def test_a_rule_spells_out_the_call_with_its_required_arguments_in_order(self) -> None:
        self.assertEqual(
            self.rules()["t-convert-units"],
            r'"{\"tool\": \"convert_units\", \"args\": {" "\"value\": " number '
            r'", \"from_unit\": " string ", \"to_unit\": " string "}}"',
        )

    def test_an_optional_argument_is_not_in_the_grammar_at_all(self) -> None:
        self.assertEqual(self.rules()["t-clock"], r'"{\"tool\": \"clock\", \"args\": {" "}}"')

    def test_every_registered_tool_has_a_rule(self) -> None:
        rules = self.rules()
        for name in base.REGISTRY:
            with self.subTest(tool=name):
                self.assertIn("t-" + name.replace("_", "-"), rules)

    def test_with_outgoing_requests_off_only_local_tools_are_offered(self) -> None:
        support.with_settings(self, allow_outbound=False)
        self.assertEqual(
            self.rules()["root"], "t-calculator | t-clock | t-convert-units | t-text-stats | t-none"
        )
        self.assertNotIn("weather", tool_menu())

    def test_the_menu_is_one_line_a_tool_naming_what_it_must_be_given(self) -> None:
        rows = tool_menu().splitlines()
        self.assertEqual(len(rows), len(base.available()))
        self.assertEqual(
            rows[0],
            "- calculator(expression): Evaluate an arithmetic expression exactly. Use for any sum.",
        )
        self.assertEqual(
            rows[1], "- clock(none): Current date and time, optionally in a named timezone."
        )


if __name__ == "__main__":
    unittest.main()
