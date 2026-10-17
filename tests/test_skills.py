"""Skills: the lookups each one makes, how the model is offered them, and how
the inputs it gives one are checked.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest

try:
    from app.agent.route import ToolCall
    from app.agent.skills import SKILLS, Lookup, Offer, check_inputs, look, offered
    from app.tools.base import REGISTRY, available
    from app.tools.grammar import decision_ceiling, tool_menu, tools_to_gbnf
    from tests import support
    from tests.gbnf import Grammar
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


class ShapeTests(unittest.TestCase):
    def test_every_lookup_is_a_real_tool_given_what_it_takes_from_a_declared_input(self) -> None:
        for skill in SKILLS.values():
            names = {arg.name for arg in skill.inputs}
            for step in skill.steps:
                with self.subTest(skill=skill.id, tool=step.tool):
                    tool = REGISTRY[step.tool]
                    self.assertTrue({a for a, _ in step.given} <= {a.name for a in tool.args})
                    self.assertTrue({a.name for a in tool.required_args()} <= {a for a, _ in step.given})
                    self.assertTrue({source for _, source in step.given} <= names)

    def test_every_skill_says_what_it_does_and_how_to_answer(self) -> None:
        for skill in SKILLS.values():
            with self.subTest(skill.id):
                self.assertTrue(skill.description.endswith("."))
                self.assertTrue(skill.cue.endswith("."))


class LookupTests(unittest.TestCase):
    def test_the_lookups_are_made_with_what_was_given_in_order(self) -> None:
        compare = SKILLS["compare"]
        self.assertEqual(
            compare.calls({"a": "Python", "b": "Rust"}),
            [ToolCall("wikipedia", {"query": "Python"}, via="skill"),
             ToolCall("wikipedia", {"query": "Rust"}, via="skill")],
        )
        self.assertEqual([c.args for c in compare.calls({"a": "Rust"})], [{"query": "Rust"}])
        self.assertEqual([a.name for a in compare.missing({"a": "Rust"})], ["b"])

    def test_a_lookup_is_made_only_with_every_input_it_takes(self) -> None:
        step = look("currency", amount="n", from_currency="x", to_currency="y")
        self.assertEqual(step, Lookup("currency", (("amount", "n"), ("from_currency", "x"),
                                                   ("to_currency", "y"))))
        self.assertIsNone(step.call({"n": "5", "x": "EUR"}))
        self.assertEqual(step.call({"n": "5", "x": "EUR", "y": "USD"}).args,
                         {"amount": "5", "from_currency": "EUR", "to_currency": "USD"})


class CheckTests(unittest.TestCase):
    def test_inputs_that_hold_are_given_back_as_text(self) -> None:
        clean, rejection = check_inputs(SKILLS["compare"], {"a": "Python", "b": "Rust"}, [])
        self.assertIsNone(rejection)
        self.assertEqual(clean, {"a": "Python", "b": "Rust"})

    def test_an_input_left_blank_is_not_one_given(self) -> None:
        _, rejection = check_inputs(SKILLS["compare"], {"a": "Python", "b": "  "}, [])
        self.assertEqual(rejection.reasons, ("b is required and was not given",))
        self.assertEqual(rejection.summary, "Compare two things was not given what it needs.")


class OfferTests(unittest.TestCase):
    """The skills the model may choose, beside the tools."""

    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def test_a_skill_is_offered_after_the_tools(self) -> None:
        tools = available()
        choices = offered(tools)
        self.assertEqual(choices[:len(tools)], tools)
        self.assertEqual([c.name for c in choices[len(tools):]], list(SKILLS))
        self.assertTrue(all(isinstance(c, Offer) for c in choices[len(tools):]))

    def test_a_skill_whose_lookups_cannot_be_made_is_not_offered(self) -> None:
        support.with_settings(self, allow_outbound=False)
        self.assertEqual(offered(available()), available())

    def test_it_is_on_the_menu_and_in_the_grammar_as_a_tool_is(self) -> None:
        choices = offered(available())
        menu = tool_menu(choices)
        self.assertIn("- compare(a, b): Compare two things side by side, from Wikipedia.", menu)
        grammar = Grammar(tools_to_gbnf(choices))
        reply = '{"tool": "compare", "args": {"a": "Python", "b": "Rust"}}'
        self.assertTrue(grammar.accepts(reply))
        self.assertFalse(grammar.accepts('{"tool": "compare", "args": {"a": "Python"}}'))
        self.assertFalse(grammar.accepts('{"tool": "weather_brief", "args": {"location": "Paris"}}'))
        longest = '{"tool": "compare", "args": {"a": "' + "é" * 80 + '", "b": "' + "é" * 80 + '"}}'
        self.assertTrue(grammar.accepts(longest))
        self.assertGreaterEqual(decision_ceiling(choices), len(longest.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
