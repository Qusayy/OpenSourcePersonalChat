"""Skills: what each one needs from a message, the lookups it makes, and
what it asks the model, as parts of a prompt.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest

try:
    from app.router import ToolCall
    from app.skills import SKILLS, Lookup, build_prompt, look, prompt_parts, skill_list
    from app.tools.base import REGISTRY
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

RESULTS = ["wikipedia: Python is a language.", "wikipedia: Rust is a language."]
FAILED = ["weather: No place called 'Atlantis' was found."]


# What the page is told about the skills, as it was before skills had inputs.
# The page draws its buttons and graphs from this; it does not change.
LISTED = [
    {"id": "summarise_link", "name": "Summarise a link", "glyph": "⧉",
     "blurb": "Read a page and boil it down", "pipeline": ["read_url", "model"],
     "persona": "agent", "placeholder": "Paste a URL…"},
    {"id": "fact_check", "name": "Fact-check", "glyph": "⚖",
     "blurb": "Weigh a claim against Wikipedia", "pipeline": ["wikipedia", "model"],
     "persona": "agent", "placeholder": "Type a claim to check…"},
    {"id": "compare", "name": "Compare two things", "glyph": "⇹",
     "blurb": "Side-by-side from sources", "pipeline": ["wikipedia", "wikipedia", "model"],
     "persona": "agent", "placeholder": "e.g. Python vs Rust"},
    {"id": "weather_brief", "name": "Weather brief", "glyph": "☁",
     "blurb": "Forecast, in plain words", "pipeline": ["weather", "model"],
     "persona": "agent", "placeholder": "Which town or city?"},
    {"id": "do_maths", "name": "Do the maths", "glyph": "⚡",
     "blurb": "Exact answer, then the working", "pipeline": ["calculator", "model"],
     "persona": "agent", "placeholder": "e.g. (1247 * 89) / 12"},
    {"id": "explain_code", "name": "Explain this code", "glyph": "◇",
     "blurb": "Line by line, no tools", "pipeline": ["model"],
     "persona": "code", "placeholder": "Paste some code…"},
]


class ShapeTests(unittest.TestCase):
    def test_the_page_is_told_exactly_what_it_was_told_before(self) -> None:
        self.assertEqual(skill_list(), LISTED)

    def test_the_pipeline_is_the_lookups_in_order_then_the_model(self) -> None:
        self.assertEqual(SKILLS["compare"].pipeline, ("wikipedia", "wikipedia", "model"))
        self.assertEqual(SKILLS["explain_code"].pipeline, ("model",))

    def test_every_lookup_is_a_real_tool_given_what_it_takes_from_a_declared_input(self) -> None:
        for skill in SKILLS.values():
            names = {arg.name for arg in skill.inputs}
            for step in skill.steps:
                with self.subTest(skill=skill.id, tool=step.tool):
                    tool = REGISTRY[step.tool]
                    self.assertTrue({a for a, _ in step.given} <= {a.name for a in tool.args})
                    self.assertTrue({a.name for a in tool.required_args()} <= {a for a, _ in step.given})
                    self.assertTrue({source for _, source in step.given} <= names)
            if skill.whole is not None:
                self.assertIn(skill.whole, names)

    def test_every_skill_says_what_it_does(self) -> None:
        for skill in SKILLS.values():
            with self.subTest(skill.id):
                self.assertTrue(skill.description.endswith("."))


class ReadingTests(unittest.TestCase):
    """What a skill finds in a message, and the lookups that makes."""

    def test_two_things_to_compare_are_found_however_they_are_asked_for(self) -> None:
        compare = SKILLS["compare"]
        for text, a, b in (
            ("Python vs Rust", "Python", "Rust"),
            ("Python vs. Rust", "Python", "Rust"),
            ("Python versus Rust", "Python", "Rust"),
            ("Python v. Rust", "Python", "Rust"),
            ("What is the difference between a crocodile and an alligator?", "a crocodile", "an alligator"),
            ("difference between tea and coffee", "tea", "coffee"),
            ("What are the differences between Python and Rust", "Python", "Rust"),
            ("Compare Python with Rust", "Python", "Rust"),
            ("compare the Moon to Mars", "the Moon", "Mars"),
            ("Compare Python and Rust", "Python", "Rust"),
            ("Python compared to Rust", "Python", "Rust"),
            ("Python, compared with Rust", "Python", "Rust"),
            ("How does Python differ from Rust?", "Python", "Rust"),
            ("How is tea different from coffee?", "tea", "coffee"),
            ("Which is faster, Python or Rust?", "Python", "Rust"),
            ("cats and dogs", "cats", "dogs"),
            ("cats or dogs?", "cats", "dogs"),
            ("“Python” vs “Rust”", "Python", "Rust"),
        ):
            with self.subTest(text):
                self.assertEqual(compare.read(text), {"a": a, "b": b})
                self.assertEqual(
                    compare.prepare(text),
                    [ToolCall("wikipedia", {"query": a}, via="skill"),
                     ToolCall("wikipedia", {"query": b}, via="skill")],
                )

    def test_one_thing_alone_is_the_first_and_only_that_is_looked_up(self) -> None:
        compare = SKILLS["compare"]
        self.assertEqual(compare.read("  Rust  "), {"a": "Rust"})
        self.assertEqual([c.args for c in compare.prepare("Rust")], [{"query": "Rust"}])
        self.assertEqual([a.name for a in compare.missing(compare.read("Rust"))], ["b"])

    def test_the_whole_message_is_the_input_of_a_skill_with_one(self) -> None:
        self.assertEqual(SKILLS["weather_brief"].read(" Paris "), {"location": "Paris"})
        self.assertEqual(SKILLS["fact_check"].prepare("The Moon is cheese"),
                         [ToolCall("wikipedia", {"query": "The Moon is cheese"}, via="skill")])

    def test_a_link_is_found_anywhere_and_nothing_else_is_taken_for_one(self) -> None:
        reader = SKILLS["summarise_link"]
        self.assertEqual(reader.read("sum up https://example.org/a please"),
                         {"url": "https://example.org/a"})
        self.assertEqual(reader.read("no link here"), {})
        self.assertEqual(reader.prepare("no link here"), [])

    def test_nothing_in_the_message_is_nothing_to_look_up(self) -> None:
        for skill in SKILLS.values():
            with self.subTest(skill.id):
                self.assertEqual(skill.prepare("   "), [])

    def test_a_lookup_is_made_only_with_every_input_it_takes(self) -> None:
        step = look("currency", amount="n", from_currency="x", to_currency="y")
        self.assertEqual(step, Lookup("currency", (("amount", "n"), ("from_currency", "x"),
                                                   ("to_currency", "y"))))
        self.assertIsNone(step.call({"n": "5", "x": "EUR"}))
        self.assertEqual(step.call({"n": "5", "x": "EUR", "y": "USD"}).args,
                         {"amount": "5", "from_currency": "EUR", "to_currency": "USD"})


class PromptPartTests(unittest.TestCase):
    def test_joined_as_a_prompt_joins_them_the_parts_are_the_prompt_to_the_letter(self) -> None:
        for skill in SKILLS.values():
            for failures in (None, FAILED):
                with self.subTest(skill=skill.id, failures=failures):
                    parts = prompt_parts(skill, "  Python vs Rust ", RESULTS, failures)
                    self.assertEqual(
                        "\n\n".join(p.content for p in parts),
                        build_prompt(skill, "  Python vs Rust ", RESULTS, failures),
                    )
                    self.assertEqual({p.role for p in parts}, {"user"})

    def test_what_was_found_is_one_part_and_says_where_it_came_from(self) -> None:
        parts = prompt_parts(SKILLS["compare"], "Python vs Rust", RESULTS, tools=("wikipedia",))
        self.assertEqual([p.kind for p in parts], ["instruction", "data"])
        self.assertEqual(parts[1].tools, ("wikipedia",))
        self.assertIn("Python is a language.", parts[1].content)

    def test_a_piece_that_is_only_the_users_text_is_the_question(self) -> None:
        parts = prompt_parts(SKILLS["explain_code"], "print(1)", [])
        self.assertEqual([(p.kind, p.content) for p in parts][-1], ("question", "print(1)"))

    def test_a_claim_quoted_in_an_instruction_stays_an_instruction(self) -> None:
        parts = prompt_parts(SKILLS["fact_check"], "The sky is green", RESULTS)
        self.assertEqual([p.kind for p in parts], ["instruction", "data", "instruction"])


if __name__ == "__main__":
    unittest.main()
