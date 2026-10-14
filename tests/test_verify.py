"""Checking an answer against what was looked up. Pure: no app, no model."""

from __future__ import annotations

import asyncio
import unittest

from app.verify import Check, caption, check, denies_access, figures, note, scalars

# What the tools write, as they write it.
WEATHER = "Paris, France: overcast, 17.9°C (feels 17.2°C), wind 9.4 km/h."
CURRENCY = "100.00 USD = 92.34 EUR (rate 0.9234, ECB 2026-10-13)."
CONVERT = "100 km = 62.1371 mi"
CLOCK = "Wednesday 14 October 2026, 15:42 in Europe/Paris."
CALC = "(1247 * 89) / 12 = 9,248.583333"
ARTICLE = "Wikipedia — Ada Lovelace: Augusta Ada King (1815–1852) was an English mathematician."
BIG = "1e+06 m = 1,000 km"


def flagged(answer: str, *sources: str, **options) -> tuple[str, ...]:
    return check(answer, sources, **options).unsupported


class FigureTests(unittest.TestCase):
    def test_a_number_is_read_with_its_unit_as_it_was_written(self) -> None:
        cases = [
            ("17.9°C", "°C"), ("64°F", "°F"), ("18 degrees", "°"), ("12%", "%"),
            ("9.4 km/h", "km/h"), ("62 miles", "mi"), ("5mm", "mm"), ("92.34 EUR", "EUR"),
            ("€5", "EUR"), ("$12.50", "USD"), ("2.1 million", None), ("1,234", None),
        ]
        for text, unit in cases:
            with self.subTest(text):
                (figure,) = figures(text)
                self.assertEqual((figure.raw, figure.unit), (text, unit))

    def test_values_come_out_of_every_way_a_number_is_written(self) -> None:
        cases = [
            ("1,234,567", 1234567), ("2.1 million", 2.1e6), ("10k", 1e4), ("1e+06", 1e6),
            ("−5", 5), ("-12 degrees", 12), ("3:42 pm", 942), ("6:12", 372), ("15:42", 942),
        ]
        for text, value in cases:
            with self.subTest(text):
                self.assertAlmostEqual(figures(text)[0].value, value)

    def test_a_range_and_an_iso_date_are_positive_numbers(self) -> None:
        self.assertEqual([f.value for f in figures("1815–1852")], [1815, 1852])
        self.assertEqual(sorted(f.value for f in figures("ECB 2026-10-13")), [10, 13, 2026])

    def test_ordinals_and_the_numbers_of_list_items_are_not_figures(self) -> None:
        self.assertEqual(figures("the 3rd and 21st"), [])
        self.assertEqual([f.raw for f in figures("1. Take a coat.\n2) Go.\nStep 3: rest.")], [])

    def test_a_number_inside_a_word_is_not_read(self) -> None:
        self.assertEqual(figures("Python3 and v2"), [])


class CheckTests(unittest.TestCase):
    def test_a_figure_from_a_tool_however_it_was_rounded_is_supported(self) -> None:
        for answer, source in (
            ("It is 17.9°C.", WEATHER), ("About 18 degrees.", WEATHER), ("It is 18°C.", WEATHER),
            ("About 62 miles.", CONVERT), ("62.14 mi", CONVERT), ("about 60 miles", CONVERT),
            ("92.34 EUR", CURRENCY), ("€92", CURRENCY), ("at a rate of 0.92", CURRENCY),
            ("It is 3:42 pm in Paris.", CLOCK), ("It is 3:42 in Paris.", CLOCK), ("15:42", CLOCK),
            ("on 14 October 2026", CLOCK), ("She was born in 1815.", ARTICLE),
            ("It comes to 9,248.58.", CALC), ("roughly 9,250", CALC), ("1 million metres", BIG),
        ):
            with self.subTest(answer):
                self.assertEqual(flagged(answer, source), ())

    def test_a_figure_no_tool_returned_is_flagged_as_it_was_written(self) -> None:
        for answer, source, missing in (
            ("It is 25°C in Paris.", WEATHER, ("25°C",)),
            ("17.9°C, which is 64°F.", WEATHER, ("64°F",)),
            ("About 63 miles.", CONVERT, ("63 miles",)),
            ("She was born in 1816.", ARTICLE, ("1816",)),
            ("It is 4:42 pm.", CLOCK, ("4:42 pm",)),
            ("That is 95.10 EUR.", CURRENCY, ("95.10 EUR",)),
        ):
            with self.subTest(answer):
                self.assertEqual(flagged(answer, source), missing)

    def test_a_figure_with_a_unit_is_not_matched_by_one_in_another(self) -> None:
        self.assertEqual(flagged("The wind is 18 km/h.", WEATHER), ("18 km/h",))
        self.assertEqual(flagged("It is 9.4°C.", WEATHER), ("9.4°C",))

    def test_small_counts_without_a_unit_are_not_figures(self) -> None:
        self.assertEqual(flagged("Three reasons, in 2 sentences: 1. it is 17.9°C.", WEATHER), ())
        self.assertEqual(flagged("It is 8 degrees.", WEATHER), ("8 degrees",))

    def test_what_the_user_wrote_counts_as_a_source(self) -> None:
        self.assertEqual(flagged("15% of 200 is 30.", "15% of 200", "15/100*200 = 30"), ())

    def test_the_working_of_a_sum_is_supported_by_the_working_kept_with_it(self) -> None:
        answer = "1247 × 89 = 110,983, and 110,983 / 12 = 9,248.58."
        self.assertEqual(flagged(answer, CALC), ("110,983",))
        self.assertEqual(flagged(answer, CALC, "110,983"), ())

    def test_with_units_only_a_bare_number_is_left_alone(self) -> None:
        answer = "Twenty drops, then 37 more; it is 55°F."
        self.assertEqual(flagged(answer, WEATHER, with_units_only=True), ("55°F",))
        self.assertEqual(flagged("Then 37 more.", WEATHER, with_units_only=True), ())

    def test_a_figure_is_named_once_however_often_it_appears(self) -> None:
        self.assertEqual(flagged("25°C, yes, 25°C.", WEATHER), ("25°C",))


class DenialTests(unittest.TestCase):
    def test_saying_there_is_no_live_data_is_caught(self) -> None:
        for answer in (
            "I don't have access to real-time weather data.",
            "As an AI, I cannot browse the internet.",
            "I am unable to access real-time information.",
            "I do not have live data for that.",
            "Sorry, I can't check current conditions.",
        ):
            with self.subTest(answer):
                self.assertTrue(denies_access(answer))

    def test_ordinary_answers_are_not(self) -> None:
        for answer in (
            "It is 17.9°C and overcast.",
            "The live data says overcast.",
            "There is no current warning in force.",
        ):
            with self.subTest(answer):
                self.assertFalse(denies_access(answer))

    def test_it_is_only_looked_for_when_asked(self) -> None:
        self.assertFalse(check("I don't have real-time data.", [WEATHER], denial=False).denied)


class WordsTests(unittest.TestCase):
    def test_the_caption_says_what_is_wrong_and_quotes_what_was_returned(self) -> None:
        found = Check(("64°F",))
        self.assertEqual(
            caption(found, [("Weather", WEATHER)]),
            "Not in what was looked up: 64°F. Weather returned: "
            "Paris, France: overcast, 17.9°C (feels 17.2°C), wind 9.4 km/h.",
        )
        self.assertEqual(caption(Check(("25°C", "64°F"))),
                         "Not in what was looked up: 25°C and 64°F.")

    def test_a_denial_is_answered_with_what_was_found(self) -> None:
        self.assertEqual(caption(Check(denied=True), [("Weather", WEATHER)]),
                         "This was looked up just now, and it worked. Weather returned: "
                         "Paris, France: overcast, 17.9°C (feels 17.2°C), wind 9.4 km/h.")

    def test_the_note_the_model_reads_later_quotes_nothing(self) -> None:
        self.assertEqual(note(Check(("64°F",))),
                         "Checked by the harness: 64°F in the answer above is not in what was "
                         "looked up.")
        both = note(Check(("25°C", "64°F"), denied=True))
        self.assertIn("25°C and 64°F in the answer above are not", both)
        self.assertIn("the lookup worked", both)
        self.assertNotIn("Paris", both)

    def test_nothing_wrong_is_nothing_to_say(self) -> None:
        self.assertIsNone(caption(Check()))
        self.assertIsNone(note(Check()))


class ScalarTests(unittest.TestCase):
    def test_every_number_and_string_of_a_cards_data_is_read(self) -> None:
        data = {"place": "Paris", "temp": 17.9, "ok": True, "days": [{"max": 19, "min": 11.5}], "x": None}
        self.assertEqual(scalars(data), ["Paris", "17.9", "19", "11.5"])

    def test_the_calculators_working_is_kept_with_its_result(self) -> None:
        from app.tools import REGISTRY

        result = asyncio.run(REGISTRY["calculator"].run(expression="(1247 * 89) / 12"))
        self.assertEqual(result.data["working"], ["110,983"])
        answer = "1247 × 89 = 110,983, and 110,983 / 12 = 9,248.58."
        self.assertEqual(flagged(answer, result.summary, *scalars(result.data)), ())


if __name__ == "__main__":
    unittest.main()
