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
    from app.tools.base import (
        Arg, Tool, ToolResult, call_tool, check, tool_menu, tools_to_gbnf,
    )
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

    async def test_a_value_reaches_the_tool_as_the_kind_the_tool_declared(self) -> None:
        self.probe()
        await call_tool("probe", {"word": 7, "count": "12"})
        self.assertEqual(self.given, [{"word": "7", "count": 12}])

    async def test_a_call_that_is_refused_never_reaches_the_tool(self) -> None:
        self.probe()
        result = await call_tool("probe", {"word": "a", "count": "several"})
        self.assertEqual(self.given, [])
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, "bad input")
        self.assertEqual(result.summary, "Probe could not use 'several' as count: it is not a number.")
        self.assertEqual(result.ms, 0.0)
        self.assertIsNone(result.card)

    async def test_a_real_tool_is_refused_in_words_a_person_would_use(self) -> None:
        result = await call_tool("convert_units", {"value": "ten", "from_unit": "km", "to_unit": "mi"})
        self.assertEqual(result.error, "bad input")
        self.assertEqual(result.summary, "Convert could not use 'ten' as a value: it is not a number.")

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


PROBE = Tool(
    name="probe",
    label="Probe",
    glyph="?",
    description="A tool that exists only in these tests.",
    args=(
        Arg("word", "string", True),
        Arg("count", "number", False, default=1),
        Arg("note", "string", False),
    ),
    run=None,  # never run: these tests stop at the check
    card="probe",
)


class CheckTests(unittest.TestCase):
    """What a call was given, turned into what its tool can be run with."""

    def test_a_number_written_as_text_becomes_a_number(self) -> None:
        for given, wanted in (("12", 12), ("12.5", 12.5), (" 7 ", 7), ("-3", -3),
                              ("1,250", 1250), ("1,250,000.5", 1250000.5), ("1e3", 1000.0)):
            with self.subTest(given=given):
                clean, rejection = check(PROBE, {"word": "a", "count": given})
                self.assertIsNone(rejection)
                self.assertEqual(clean["count"], wanted)
                self.assertIs(type(clean["count"]), type(wanted))

    def test_a_number_that_is_already_one_is_left_as_it_is(self) -> None:
        for given in (12, 12.5, 0, -3):
            with self.subTest(given=given):
                clean, _ = check(PROBE, {"word": "a", "count": given})
                self.assertEqual(clean["count"], given)
                self.assertIs(type(clean["count"]), type(given))

    def test_what_is_not_a_number_is_refused_by_name(self) -> None:
        for given, reason in (
            ("twelve", "count must be a number; got 'twelve'"),
            ("", "count must be a number; got ''"),
            ("1,5", "count must be a number; got '1,5'"),
            ("12 km", "count must be a number; got '12 km'"),
            (True, "count must be a number; got True"),
            ([12], "count must be a number; got a list"),
            ({"n": 12}, "count must be a number; got an object"),
            ("nan", "count must be a finite number; got 'nan'"),
            (float("inf"), "count must be a finite number; got inf"),
        ):
            with self.subTest(given=given):
                _, rejection = check(PROBE, {"word": "a", "count": given})
                self.assertIsNotNone(rejection)
                self.assertEqual(rejection.reasons, (reason,))
                self.assertEqual(rejection.code, "bad input")

    def test_text_is_passed_on_exactly_as_it_was_given(self) -> None:
        for given in ("a", "  spaced  ", "", "12"):
            with self.subTest(given=given):
                clean, rejection = check(PROBE, {"word": given})
                self.assertIsNone(rejection)
                self.assertEqual(clean["word"], given)

    def test_a_number_where_text_is_wanted_becomes_text(self) -> None:
        self.assertEqual(check(PROBE, {"word": 7})[0]["word"], "7")
        self.assertEqual(check(PROBE, {"word": 2.5})[0]["word"], "2.5")

    def test_what_is_not_text_is_refused_by_name(self) -> None:
        for given, reason in (
            (["a"], "word must be text; got a list"),
            ({"a": 1}, "word must be text; got an object"),
            (False, "word must be text; got False"),
        ):
            with self.subTest(given=given):
                _, rejection = check(PROBE, {"word": given})
                self.assertEqual(rejection.reasons, (reason,))
                self.assertEqual(rejection.code, "bad input")
                self.assertEqual(
                    rejection.summary, "Probe could not use that as word: it is not text."
                )

    def test_arguments_the_tool_never_declared_are_dropped(self) -> None:
        clean, rejection = check(PROBE, {"word": "a", "colour": "red"})
        self.assertIsNone(rejection)
        self.assertEqual(clean, {"word": "a", "count": 1})

    def test_an_argument_left_out_or_given_as_nothing_takes_its_default(self) -> None:
        self.assertEqual(check(PROBE, {"word": "a"})[0], {"word": "a", "count": 1})
        self.assertEqual(check(PROBE, {"word": "a", "count": None})[0], {"word": "a", "count": 1})
        # An optional argument with no default is simply absent.
        self.assertNotIn("note", check(PROBE, {"word": "a", "note": None})[0])

    def test_a_default_does_not_stand_in_for_a_value_that_was_refused(self) -> None:
        clean, rejection = check(PROBE, {"word": "a", "count": "several"})
        self.assertNotIn("count", clean)
        self.assertEqual(rejection.reasons, ("count must be a number; got 'several'",))

    def test_a_required_argument_that_is_missing_is_refused_in_both_voices(self) -> None:
        for given in ({}, None, {"word": None}, {"colour": "red"}):
            with self.subTest(given=given):
                _, rejection = check(PROBE, given)
                self.assertEqual(rejection.reasons, ("word is required and was not given",))
                self.assertEqual(rejection.summary, "Probe needs word.")
                self.assertEqual(rejection.code, "missing input")

    def test_every_problem_with_a_call_is_reported_at_once(self) -> None:
        _, rejection = check(PROBE, {"count": "several"})
        self.assertEqual(
            rejection.reasons,
            ("count must be a number; got 'several'", "word is required and was not given"),
        )
        self.assertEqual(
            rejection.summary,
            "Probe could not use 'several' as count: it is not a number. Probe needs word.",
        )
        self.assertEqual(rejection.code, "bad input")

    def test_a_reason_is_a_fact_about_the_call_and_never_an_instruction(self) -> None:
        # A reason may be shown to the model, which takes "try" and "please" as orders.
        _, rejection = check(PROBE, {"count": [1], "word": {"a": 1}})
        for reason in rejection.reasons:
            with self.subTest(reason=reason):
                self.assertNotRegex(reason.lower(), r"\b(try|please|should|retry|again)\b")

    def test_every_real_tool_accepts_the_arguments_its_own_command_produces(self) -> None:
        from app.router import parse_slash

        for text in ("/calc 2+2", "/clock", "/time Asia/Tokyo", "/convert 10 km to mi",
                     "/stats one two", "/wiki Ada Lovelace", "/weather Lisbon",
                     "/fx 5 usd eur", "/read https://e.org/x"):
            with self.subTest(text=text):
                call, _ = parse_slash(text)
                clean, rejection = check(base.REGISTRY[call.name], call.args)
                self.assertIsNone(rejection)
                self.assertEqual(clean, call.args)


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
