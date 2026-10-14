"""Calling a tool by name, and the grammar and menu built from the registry.

Needs the app's own requirements; without them the whole module is skipped.
No tool here reaches the network: the ones that would are replaced, and a
made-up tool stands in wherever the dispatcher itself is what is being tested.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

try:
    from app.tools import base
    from app.tools.base import (
        Arg, Tool, ToolResult, args_ceiling, args_to_gbnf, call_tool, check, check_args,
        decision_ceiling, tool_menu, tools_to_gbnf, was_given,
    )
    from app.excerpt import estimate
    from evals import fixtures
    from tests import support
    from tests.gbnf import Grammar
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


LONG = " ".join(f"Sentence {i} says a thing about lighthouse number {i}." for i in range(400))


def recorded(name: str) -> ToolResult:
    return ToolResult(**fixtures.load(name)["result"])


class ObservationTests(unittest.TestCase):
    """What the model reads of a result: never more than the budget."""

    def long_result(self) -> ToolResult:
        return ToolResult(True, LONG, data={
            "text": LONG, "extract": LONG, "title": "Lights", "domain": "example.org",
        })

    def test_every_tool_reads_a_long_result_within_its_budget(self) -> None:
        for name, tool in base.REGISTRY.items():
            with self.subTest(tool=name):
                seen = tool.observation(self.long_result(), "lighthouse number 7")
                self.assertTrue(seen)
                self.assertLessEqual(estimate(seen), tool.budget)
                self.assertLessEqual(estimate(tool.observation(self.long_result(), "", 30)), 30)

    def test_a_budget_given_never_raises_the_tools_own(self) -> None:
        for name, tool in base.REGISTRY.items():
            with self.subTest(tool=name):
                seen = tool.observation(self.long_result(), "", 10_000)
                self.assertLessEqual(estimate(seen), tool.budget)

    def test_a_page_and_an_article_are_given_more_than_a_line(self) -> None:
        self.assertEqual(base.REGISTRY["read_url"].budget, 250)
        self.assertEqual(base.REGISTRY["wikipedia"].budget, 175)
        self.assertEqual(base.REGISTRY["weather"].budget, 80)

    def test_a_one_line_result_is_read_as_it_is(self) -> None:
        result = recorded("weather_paris.json")
        self.assertEqual(base.REGISTRY["weather"].observation(result, "coat?"), result.summary)

    def test_a_long_page_is_read_for_the_question_asked(self) -> None:
        tool = base.REGISTRY["read_url"]
        seen = tool.observation(recorded("read_url_lighthouses.json"), "how tall is the Fastnet lighthouse?")
        self.assertTrue(seen.startswith("Page “Lighthouses of Ireland” (example.org): "))
        self.assertIn("The Fastnet Rock lighthouse stands 54 metres tall", seen)
        self.assertLessEqual(estimate(seen), 250)

    def test_a_long_article_is_read_for_the_question_asked(self) -> None:
        article = ToolResult(True, "Wikipedia — Lights: …", data={"title": "Lights", "extract": LONG})
        seen = base.REGISTRY["wikipedia"].observation(article, "lighthouse number 377")
        self.assertTrue(seen.startswith("Wikipedia — Lights: "))
        self.assertIn("Sentence 377 says", seen)

    def test_a_result_without_its_text_is_read_by_its_summary(self) -> None:
        page = ToolResult(True, "Page “Tides” (example.org): High water at six.", data={})
        self.assertEqual(base.REGISTRY["read_url"].observation(page), page.summary)


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
                # As typed, but for a currency, which is run in capitals.
                self.assertEqual(
                    {k: v.lower() if isinstance(v, str) else v for k, v in clean.items()},
                    {k: v.lower() if isinstance(v, str) else v for k, v in call.args.items()},
                )


def call(tool: str, **args) -> str:
    """A tool call as the model has to write it."""
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False)


class GrammarTests(unittest.TestCase):
    """The grammar a routing pass is held to, and the menu it reads.

    The grammar cannot be compiled here, so these read its text: what it lets
    a model say, and what it does not.
    """

    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)
        self.grammar = Grammar(tools_to_gbnf())

    def rules(self, tools=None, **options) -> dict[str, str]:
        out = {}
        for line in tools_to_gbnf(tools, **options).splitlines():
            name, sep, body = line.partition(" ::= ")
            if sep:
                out[name] = body
        return out

    def test_the_root_offers_every_tool_in_registry_order_then_ask_then_none(self) -> None:
        self.assertEqual(
            self.rules()["root"],
            "t-calculator | t-clock | t-convert-units | t-text-stats | t-wikipedia | "
            "t-weather | t-currency | t-read-url | t-ask | t-none",
        )

    def test_every_registered_tool_has_a_rule(self) -> None:
        rules = self.rules()
        for name in base.REGISTRY:
            with self.subTest(tool=name):
                self.assertIn("t-" + name.replace("_", "-"), rules)

    def test_with_outgoing_requests_off_only_local_tools_are_offered(self) -> None:
        support.with_settings(self, allow_outbound=False)
        self.assertEqual(
            self.rules()["root"],
            "t-calculator | t-clock | t-convert-units | t-text-stats | t-ask | t-none",
        )
        self.assertNotIn("weather", tool_menu())

    def test_none_is_a_choice_with_no_arguments(self) -> None:
        self.assertEqual(self.rules()["t-none"], r'"{\"tool\": \"none\"}"')
        self.assertTrue(self.grammar.accepts('{"tool": "none"}'))
        self.assertFalse(self.grammar.accepts('{"tool": "none", "args": {}}'))

    def test_a_rule_spells_out_the_call_with_its_required_arguments_in_order(self) -> None:
        rules = self.rules()
        self.assertEqual(
            rules["t-convert-units"],
            r'"{\"tool\": \"convert_units\", \"args\": {" "\"value\": " number '
            r'", \"from_unit\": " ' + "choice-1" + r' ", \"to_unit\": " ' + "choice-1" + r' "}}"',
        )
        self.assertEqual(
            rules["t-wikipedia"],
            r'"{\"tool\": \"wikipedia\", \"args\": {" "\"query\": " text-80 "}}"',
        )

    def test_a_call_to_each_tool_is_something_the_grammar_allows(self) -> None:
        for said in (
            call("calculator", expression="1247*89"),
            call("clock"),
            call("clock", timezone_name="Europe/Paris"),
            call("convert_units", value=10, from_unit="km", to_unit="mi"),
            call("convert_units", value=-2.5, from_unit="°c", to_unit="fahrenheit"),
            call("text_stats", text="The quick brown fox."),
            call("wikipedia", query="Ada Lovelace"),
            call("weather", location="São Paulo"),
            call("currency", amount=100, from_currency="EUR", to_currency="USD"),
            call("read_url", url="https://example.org/a?b=c&d=%20"),
            call("ask", question="Which city do you mean?"),
            '{"tool": "none"}',
        ):
            with self.subTest(said=said):
                self.assertTrue(self.grammar.accepts(said))

    def test_what_a_small_model_writes_when_asked_politely_is_ruled_out(self) -> None:
        good = call("weather", location="Paris")
        for said in (
            "Sure! " + good,                                   # a preamble
            good + "\n",                                       # something after it
            '{"tool": "weather", "args": {"location": "Paris",}}',   # a trailing comma
            '{"tool": "weather", "args": {"location": "Paris", "units": "c"}}',  # an invented key
            '{"tool": "weather", "args": {}}',                 # a required argument left out
            '{"tool":"weather","args":{"location":"Paris"}}',  # its own spacing
            '{"tool": "telescope", "args": {}}',               # a tool that does not exist
            '{"tool": "weather", "args": {"location": 7}}',    # a number where text is wanted
            '{"tool": "calculator", "args": {"expression": "2+2"}',  # never closed
            "",
        ):
            with self.subTest(said=said):
                self.assertFalse(self.grammar.accepts(said))

    def test_an_optional_argument_may_be_given_or_left_out_and_nothing_between(self) -> None:
        self.assertEqual(
            self.rules()["t-clock"],
            r'"{\"tool\": \"clock\", \"args\": {" ("\"timezone_name\": " text-40)? "}}"',
        )
        self.assertTrue(self.grammar.accepts(call("clock")))
        self.assertTrue(self.grammar.accepts(call("clock", timezone_name="Asia/Tokyo")))
        self.assertFalse(self.grammar.accepts('{"tool": "clock", "args": {"timezone_name": }}'))
        self.assertFalse(self.grammar.accepts('{"tool": "clock", "args": {, "timezone_name": "UTC"}}'))

    def test_optional_arguments_never_leave_a_comma_where_none_belongs(self) -> None:
        both = Tool("probe", "Probe", "?", "", (Arg("a", "string", False), Arg("b", "number", False)),
                    None, "probe")
        after = Tool("probe", "Probe", "?", "",
                     (Arg("need"), Arg("a", "string", False), Arg("b", "number", False)), None, "probe")
        free = Grammar(tools_to_gbnf([both], ask=False))
        for args in ({}, {"a": "x"}, {"b": 1}, {"a": "x", "b": 1}):
            with self.subTest(args=args):
                self.assertTrue(free.accepts(call("probe", **args)))
        self.assertFalse(free.accepts('{"tool": "probe", "args": {"b": 1, "a": "x"}}'))  # out of order
        self.assertFalse(free.accepts('{"tool": "probe", "args": {, "b": 1}}'))
        held = Grammar(tools_to_gbnf([after], ask=False))
        for args in ({"need": "n"}, {"need": "n", "a": "x"}, {"need": "n", "b": 1},
                     {"need": "n", "a": "x", "b": 1}):
            with self.subTest(args=args):
                self.assertTrue(held.accepts(call("probe", **args)))
        self.assertFalse(held.accepts(call("probe", a="x")))

    def test_no_text_can_run_on_past_its_ceiling(self) -> None:
        rules = self.rules()
        self.assertNotIn("string", rules)
        texts = {name: body for name, body in rules.items() if name.startswith("text-")}
        self.assertEqual(set(texts), {"text-40", "text-60", "text-80", "text-120", "text-200"})
        for name, body in texts.items():
            with self.subTest(rule=name):
                self.assertEqual(body, r'"\"" char{1,' + name[5:] + r'} "\""')
        # Every text argument is held to one of them, or to a list of values.
        for tool in base.available():
            for arg in tool.args:
                if arg.type == "string":
                    with self.subTest(tool=tool.name, arg=arg.name):
                        self.assertTrue(arg.choices or (arg.max_len or base.DEFAULT_MAX_LEN) <= 200)
        self.assertTrue(self.grammar.accepts(call("weather", location="x" * 60)))
        self.assertFalse(self.grammar.accepts(call("weather", location="x" * 61)))
        self.assertFalse(self.grammar.accepts(call("weather", location="")))

    def test_text_may_hold_any_letters_and_the_usual_escapes_but_no_raw_control(self) -> None:
        for location in ("Zürich", "東京", 'the \\"old\\" town', "a\\\\b", "line\\nbreak"):
            with self.subTest(location=location):
                said = '{"tool": "weather", "args": {"location": "' + location + '"}}'
                self.assertTrue(self.grammar.accepts(said))
        for location in ("a\nb", 'a"b', "a\\qb", "tab\there"):
            with self.subTest(location=location):
                said = '{"tool": "weather", "args": {"location": "' + location + '"}}'
                self.assertFalse(self.grammar.accepts(said))

    def test_a_currency_can_only_be_one_the_tool_knows(self) -> None:
        for code in ("USD", "EUR", "JPY", "ZAR"):
            with self.subTest(code=code):
                self.assertTrue(self.grammar.accepts(
                    call("currency", amount=1, from_currency=code, to_currency="GBP")))
        for code in ("dollars", "usd", "US", "XXX", "EURO", ""):
            with self.subTest(code=code):
                self.assertFalse(self.grammar.accepts(
                    call("currency", amount=1, from_currency=code, to_currency="GBP")))

    def test_a_unit_can_be_written_by_its_name_or_by_a_word_for_it_and_not_made_up(self) -> None:
        for unit in ("km", "kilometres", "mi", "miles", "km/h", "°c", "c", "k", "m", "floz"):
            with self.subTest(unit=unit):
                self.assertTrue(self.grammar.accepts(
                    call("convert_units", value=1, from_unit=unit, to_unit="m")))
        for unit in ("parsecs", "KM", "kilo metres", "kmm", "x", ""):
            with self.subTest(unit=unit):
                self.assertFalse(self.grammar.accepts(
                    call("convert_units", value=1, from_unit=unit, to_unit="m")))

    def test_a_number_is_a_plain_decimal_of_bounded_length(self) -> None:
        for number, allowed in (("0", True), ("-12.5", True), ("9" * 16, True), ("1.123456", True),
                                ("9" * 17, False), ("1.1234567", False), ("01", False),
                                ("1e3", False), (".5", False), ('"7"', False)):
            with self.subTest(number=number):
                said = '{"tool": "convert_units", "args": {"value": ' + number
                said += ', "from_unit": "km", "to_unit": "mi"}}'
                self.assertEqual(self.grammar.accepts(said), allowed)

    def test_the_model_may_ask_one_bounded_question(self) -> None:
        self.assertEqual(
            self.rules()["t-ask"], r'"{\"tool\": \"ask\", \"args\": {" "\"question\": " text-120 "}}"'
        )
        self.assertTrue(self.grammar.accepts(call("ask", question="Which city?")))
        self.assertFalse(self.grammar.accepts(call("ask", question="x" * 121)))
        self.assertFalse(self.grammar.accepts(call("ask")))

    def test_asking_can_be_left_out_of_the_grammar(self) -> None:
        rules = self.rules(ask=False)
        self.assertNotIn("t-ask", rules)
        self.assertNotIn("t-ask", rules["root"])
        self.assertFalse(Grammar(tools_to_gbnf(ask=False)).accepts(call("ask", question="Why?")))

    def test_the_menu_is_one_line_a_tool_and_marks_what_may_be_left_out(self) -> None:
        rows = tool_menu().splitlines()
        self.assertEqual(len(rows), len(base.available()))
        self.assertEqual(
            rows[0],
            "- calculator(expression): Evaluate an arithmetic expression exactly. Use for any sum.",
        )
        self.assertEqual(
            rows[1],
            "- clock(timezone_name?): Current date and time, optionally in a named timezone.",
        )


class ArgsGrammarTests(unittest.TestCase):
    """The grammar a pass is held to when it fills in a set of arguments."""

    PAIR = (Arg("a", "string", True, "the first", max_len=10),
            Arg("b", "string", True, "the second", max_len=10))

    def test_it_is_one_object_of_those_arguments_and_nothing_else(self) -> None:
        grammar = Grammar(args_to_gbnf(self.PAIR))
        self.assertTrue(grammar.accepts('{"a": "Python", "b": "Rust"}'))
        for text in ('{"a": "Python"}', '{"b": "Rust", "a": "Python"}', '{"a": "Python", "b": "Rust", "c": "C"}',
                     '{"tool": "none"}', '{"a": "Python", "b": "far too long for it"}',
                     'Sure! {"a": "Python", "b": "Rust"}'):
            with self.subTest(text=text):
                self.assertFalse(grammar.accepts(text))

    def test_it_holds_each_argument_to_its_kind(self) -> None:
        spec = (Arg("n", "number", True, "how many"),
                Arg("unit", "string", True, "which", choices=("km", "mi")),
                Arg("note", "string", False, "anything", max_len=5))
        grammar = Grammar(args_to_gbnf(spec))
        self.assertTrue(grammar.accepts('{"n": 3, "unit": "km"}'))
        self.assertTrue(grammar.accepts('{"n": 3, "unit": "mi", "note": "hi"}'))
        self.assertFalse(grammar.accepts('{"n": "three", "unit": "km"}'))
        self.assertFalse(grammar.accepts('{"n": 3, "unit": "miles"}'))

    def test_enough_tokens_are_given_for_the_longest_object_it_allows(self) -> None:
        longest = '{"a": "' + "é" * 10 + '", "b": "' + "é" * 10 + '"}'
        self.assertTrue(Grammar(args_to_gbnf(self.PAIR)).accepts(longest))
        self.assertGreaterEqual(args_ceiling(self.PAIR), len(longest.encode("utf-8")))

    def test_a_set_of_arguments_is_checked_as_a_tools_are(self) -> None:
        clean, rejection = check_args("Compare", self.PAIR, {"a": "Python", "c": "x"})
        self.assertEqual(clean, {"a": "Python"})
        self.assertEqual(rejection.reasons, ("b is required and was not given",))
        self.assertEqual(rejection.summary, "Compare needs b.")
        self.assertEqual(check_args("Compare", self.PAIR, {"a": "Python", "b": 3}),
                         ({"a": "Python", "b": "3"}, None))


class GrammarReaderTests(unittest.TestCase):
    """The reader the tests above lean on. If it misread a grammar, they would
    be checking nothing, so it is checked on grammars small enough to read."""

    def accepts(self, grammar: str, text: str) -> bool:
        return Grammar(grammar).accepts(text)

    def test_a_literal_matches_itself_whole_and_nothing_else(self) -> None:
        g = r'root ::= "a\"b\\c"'
        self.assertTrue(self.accepts(g, 'a"b\\c'))
        for text in ("", 'a"b', 'a"b\\cd', 'A"b\\c'):
            with self.subTest(text=text):
                self.assertFalse(self.accepts(g, text))

    def test_a_class_is_a_set_ranges_and_all_and_can_be_turned_inside_out(self) -> None:
        self.assertTrue(self.accepts("root ::= [a-cx]", "b"))
        self.assertTrue(self.accepts("root ::= [a-cx]", "x"))
        self.assertFalse(self.accepts("root ::= [a-cx]", "d"))
        self.assertFalse(self.accepts("root ::= [a-cx]", "ab"))
        self.assertTrue(self.accepts(r'root ::= [^"\\\x7F\x00-\x1F]', "é"))
        for ch in ('"', "\\", "\n", "\x7f", "\x00"):
            with self.subTest(ch=repr(ch)):
                self.assertFalse(self.accepts(r'root ::= [^"\\\x7F\x00-\x1F]', ch))

    def test_repetition_counts(self) -> None:
        for grammar, yes, no in (
            ('root ::= "a"?', ("", "a"), ("aa",)),
            ('root ::= "a"*', ("", "a", "aaaa"), ("b",)),
            ('root ::= "a"+', ("a", "aaa"), ("",)),
            ('root ::= "a"{2,3}', ("aa", "aaa"), ("a", "aaaa")),
            ('root ::= "a"{2}', ("aa",), ("a", "aaa")),
            ('root ::= [0-9]{0,2} "x"', ("x", "1x", "12x"), ("123x",)),
        ):
            for text in yes:
                with self.subTest(grammar=grammar, text=text):
                    self.assertTrue(self.accepts(grammar, text))
            for text in no:
                with self.subTest(grammar=grammar, text=text):
                    self.assertFalse(self.accepts(grammar, text))

    def test_alternatives_groups_and_other_rules(self) -> None:
        g = 'root ::= "<" item ("," item)* ">"\nitem ::= "a" | "bb"'
        for text in ("<a>", "<bb>", "<a,bb,a>"):
            with self.subTest(text=text):
                self.assertTrue(self.accepts(g, text))
        for text in ("<>", "<a,>", "<b>", "a", "<a,bb"):
            with self.subTest(text=text):
                self.assertFalse(self.accepts(g, text))

    def test_one_alternative_being_the_start_of_another_is_not_a_trap(self) -> None:
        g = 'root ::= "\\"" unit "\\""\nunit ::= "m" | "mi" | "min"'
        for text in ('"m"', '"mi"', '"min"'):
            self.assertTrue(self.accepts(g, text))
        self.assertFalse(self.accepts(g, '"mil"'))

    def test_what_it_cannot_read_it_refuses_to_guess_at(self) -> None:
        for grammar in ("root = x", 'root ::= "a" & "b"', "root ::= (", r'root ::= "\q"'):
            with self.subTest(grammar=grammar):
                with self.assertRaises((ValueError, IndexError)):
                    Grammar(grammar).accepts("a")


class CeilingTests(unittest.TestCase):
    """How long a reply can get, which is how many tokens a pass must be given
    to be sure of reaching the end of it."""

    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def longest(self, tool: Tool) -> str:
        """The longest call to a tool the grammar allows: every argument given,
        each as long as it can be."""
        args = {}
        for arg in tool.args:
            if arg.type != "string":
                args[arg.name] = None  # written in below; JSON has no way to spell this number
            elif arg.choices:
                args[arg.name] = max(arg.choices, key=lambda c: len(json.dumps(c, ensure_ascii=False).encode()))
            else:
                args[arg.name] = "\U0001F600" * (arg.max_len or base.DEFAULT_MAX_LEN)  # four bytes each
        return call(tool.name, **args).replace("null", "-" + "9" * 16 + "." + "9" * 6)

    def test_the_longest_call_to_each_tool_is_allowed_and_fits_under_the_ceiling(self) -> None:
        grammar = Grammar(tools_to_gbnf())
        ceiling = decision_ceiling()
        for tool in [*base.available(), Tool("ask", "", "", "", (base.ASK,), None, "")]:
            with self.subTest(tool=tool.name):
                said = self.longest(tool)
                self.assertTrue(grammar.accepts(said))
                self.assertLessEqual(len(said.encode("utf-8")), ceiling)

    def test_the_ceiling_is_exactly_the_longest_of_them(self) -> None:
        longest = max(len(self.longest(t).encode("utf-8")) for t in base.available())
        self.assertEqual(decision_ceiling(), longest)

    def test_the_ceiling_is_worked_out_from_the_arguments(self) -> None:
        tool = Tool("t", "", "", "", (Arg("a", max_len=10), Arg("n", "number")), None, "")
        # {"tool": "t", "args": {"a": "<10 chars of 4 bytes>", "n": <24 bytes>}}
        frame = len('{"tool": "t", "args": {"a": "", "n": }}')
        self.assertEqual(decision_ceiling([tool], ask=False), frame + 40 + 24)
        self.assertEqual(decision_ceiling([], ask=False), len('{"tool": "none"}'))

    def test_a_list_of_values_is_as_long_as_its_longest_value(self) -> None:
        tool = Tool("t", "", "", "", (Arg("u", choices=("km", "kilometres", "°c")),), None, "")
        self.assertEqual(
            decision_ceiling([tool], ask=False), len('{"tool": "t", "args": {"u": "kilometres"}}')
        )

    def test_characters_alone_would_not_be_enough(self) -> None:
        # 120 characters is the ceiling the grammar puts on a question, and a
        # question that long can be several times that in bytes. A pass given
        # only the old 96 tokens could be cut off inside it.
        self.assertGreater(decision_ceiling(), 96)
        self.assertGreater(decision_ceiling([], ask=True), 120)


class AllowedValueTests(unittest.TestCase):
    """What the grammar holds a model to, every other caller is held to as well."""

    def test_a_currency_is_taken_in_any_case_and_run_in_capitals(self) -> None:
        clean, rejection = check(
            base.REGISTRY["currency"], {"amount": 5, "from_currency": " usd", "to_currency": "Eur"}
        )
        self.assertIsNone(rejection)
        self.assertEqual(clean, {"amount": 5, "from_currency": "USD", "to_currency": "EUR"})

    def test_a_currency_nobody_publishes_a_rate_for_is_refused_by_name(self) -> None:
        _, rejection = check(
            base.REGISTRY["currency"], {"amount": 5, "from_currency": "dollars", "to_currency": "EUR"}
        )
        self.assertEqual(rejection.code, "bad input")
        self.assertEqual(
            rejection.reasons,
            ("from_currency must be one of the 32 values it knows, such as AUD, BGN, BRL; "
             "got 'dollars'",),
        )
        self.assertEqual(
            rejection.summary, "Currency does not know 'dollars' as a currency to convert from."
        )

    def test_a_unit_is_taken_by_any_of_its_names_and_run_by_its_own(self) -> None:
        clean, rejection = check(
            base.REGISTRY["convert_units"],
            {"value": "10", "from_unit": "Kilometres", "to_unit": "miles."},
        )
        self.assertIsNone(rejection)
        self.assertEqual(clean, {"value": 10, "from_unit": "km", "to_unit": "mi"})

    def test_a_unit_nobody_knows_is_refused_before_the_tool_runs(self) -> None:
        _, rejection = check(
            base.REGISTRY["convert_units"], {"value": 1, "from_unit": "parsecs", "to_unit": "km"}
        )
        self.assertEqual(len(rejection.reasons), 1)
        self.assertTrue(rejection.reasons[0].startswith("from_unit must be one of the "))
        self.assertTrue(rejection.reasons[0].endswith("got 'parsecs'"))
        self.assertEqual(
            rejection.summary, "Convert does not know 'parsecs' as a unit to convert from."
        )

    def test_every_spelling_the_grammar_offers_is_one_the_check_accepts(self) -> None:
        for tool in base.REGISTRY.values():
            for arg in tool.args:
                for spelling in arg.choices or ():
                    with self.subTest(tool=tool.name, arg=arg.name, spelling=spelling):
                        value, complaint = base._coerce(arg, spelling)  # noqa: SLF001 - the rule itself
                        self.assertIsNone(complaint)
                        self.assertEqual(value, arg.canon(spelling) if arg.canon else spelling)

    def test_how_long_text_may_be_binds_the_model_and_not_a_person(self) -> None:
        # A ceiling is on what the grammar lets a model write. What a person
        # types is the tool's own business.
        clean, rejection = check(base.REGISTRY["weather"], {"location": "x" * 500})
        self.assertIsNone(rejection)
        self.assertEqual(len(clean["location"]), 500)

class GivenAddressTests(unittest.TestCase):
    """Whether an address is one the user wrote, or one that came from
    somewhere else."""

    SAID = ["Can you read https://Example.org/tides for me?", "and also www.rivers.test/a/b."]

    def test_an_address_they_wrote_is_theirs_however_it_is_dressed(self) -> None:
        for address in (
            "https://example.org/tides", "http://example.org/tides", "example.org/tides",
            "https://www.example.org/tides", "HTTPS://EXAMPLE.ORG/tides", "https://example.org/tides/",
            "https://example.org", "rivers.test/a/b", "https://rivers.test/a/b", "www.rivers.test/a",
        ):
            with self.subTest(address=address):
                self.assertTrue(was_given(address, self.SAID))

    def test_an_address_they_did_not_write_is_not(self) -> None:
        for address in (
            "https://evil.example/collect",
            "https://example.org/tides?leak=their+question",   # their address, with something added
            "https://example.org/tides/secret",                 # their address, and further
            "https://example.org/tide",                         # part of a word of it
            "https://xample.org/tides",                         # the end of a name of theirs
            "https://org/tides",
            "https://example.org.evil.example/tides",
            "", "   ", "https://", "www.",
        ):
            with self.subTest(address=address):
                self.assertFalse(was_given(address, self.SAID))

    def test_an_address_inside_a_longer_name_of_theirs_is_not_theirs(self) -> None:
        said = ["see https://data.example.com/report"]
        self.assertFalse(was_given("https://a.example.com/report", said))
        self.assertFalse(was_given("https://example.com/report", said))
        self.assertTrue(was_given("https://data.example.com/report", said))

    def test_nothing_they_said_gives_no_address(self) -> None:
        self.assertFalse(was_given("https://example.org", []))
        self.assertFalse(was_given("https://example.org", ["tell me about tides"]))

    def test_the_page_reader_is_the_tool_that_is_held_to_this(self) -> None:
        held = [(t.name, a.name) for t in base.REGISTRY.values() for a in t.args if a.quoted]
        self.assertEqual(held, [("read_url", "url")])


if __name__ == "__main__":
    unittest.main()
