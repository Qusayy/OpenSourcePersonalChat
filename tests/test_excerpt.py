"""Choosing the part of a long text worth reading. Pure: no app, no model."""

from __future__ import annotations

import random
import unittest

from app.excerpt import GAP, estimate, excerpt, sentences, terms

# A page whose answer is near its end, as pages tend to have it.
FILLER = [
    "The coast has many lights along it.",
    "Each one was built where ships had been lost.",
    "Their towers were painted so sailors could tell them apart by day.",
    "At night each light flashes in a pattern of its own.",
    "Keepers lived beside them and kept the lamps burning.",
    "Most were later run by machines instead.",
    "Visitors can climb some of them in summer.",
    "Storms in winter close the paths to the rocks.",
    "Old logbooks record the weather of every night.",
    "Some of the buildings are now holiday cottages.",
]
ANSWER = "The Fastnet light stands 54 metres tall and was first lit in 1904."
PAGE = " ".join(FILLER[:8] + [ANSWER] + FILLER[8:])


def pieces(result: str) -> list[str]:
    """What an excerpt took from the text, its gap marks taken out."""
    return [" ".join(p.split()) for p in result.split("…") if p.strip()]


class ExcerptTests(unittest.TestCase):
    def test_a_text_that_fits_is_returned_whole(self) -> None:
        self.assertEqual(excerpt("  Two short sentences. That is all.  ", 50), "Two short sentences. That is all.")

    def test_a_question_finds_its_answer_near_the_end_of_the_page(self) -> None:
        result = excerpt(PAGE, 40, "How tall is the Fastnet light?")
        self.assertIn(ANSWER, result)
        self.assertLessEqual(estimate(result), 40)

    def test_without_a_question_each_third_of_the_page_has_its_say(self) -> None:
        found = sentences(PAGE)
        result = excerpt(PAGE, 60)
        taken = [found.index(p) for piece in pieces(result) for p in sentences(piece) if p in found]
        thirds = {index * 3 // len(found) for index in taken}
        self.assertEqual(thirds, {0, 1, 2})

    def test_a_question_none_of_whose_words_are_in_the_text_is_no_question(self) -> None:
        self.assertEqual(excerpt(PAGE, 60, "zebra quantum"), excerpt(PAGE, 60))

    def test_a_word_in_every_sentence_tells_less_than_a_rare_one(self) -> None:
        text = (
            "The lighthouse at Hook is old. The lighthouse at Fastnet was lit in 1904. "
            "The lighthouse at Baily is white. The lighthouse at Skellig is remote."
        )
        result = excerpt(text, 18, "When was the lighthouse lit, in 1904?")
        self.assertIn("The lighthouse at Fastnet was lit in 1904.", result)

    def test_sentences_keep_the_order_of_the_text_and_every_gap_is_marked(self) -> None:
        result = excerpt(PAGE, 45, "keepers Fastnet")
        found = sentences(PAGE)
        order = [found.index(p) for p in sentences(result.replace("…", "")) if p in found]
        self.assertEqual(order, sorted(order))
        self.assertTrue(result.startswith("… "), "the page's first sentence was left out")
        self.assertIn(GAP, result)

    def test_a_line_the_page_repeats_is_read_once(self) -> None:
        text = "Home. Menu. " * 2 + "Home. " + ANSWER + " Footer text here."
        result = excerpt(text, 30, "Fastnet")
        self.assertIn(ANSWER, result)
        self.assertEqual(result.count("Home."), 1)

    def test_a_sentence_too_long_for_the_budget_is_cut_at_a_word(self) -> None:
        long = "word " * 60 + "end."
        result = excerpt(long, 10)
        self.assertTrue(result.endswith(" …"))
        self.assertLessEqual(estimate(result), 10)
        self.assertNotIn("wor …", result)

    def test_nothing_fits_in_no_budget(self) -> None:
        self.assertEqual(excerpt(PAGE, 0, "Fastnet"), "")
        self.assertEqual(excerpt("", 50), "")

    def test_headings_and_list_items_are_sentences_of_their_own(self) -> None:
        self.assertEqual(
            sentences("History\n- Built in 1904\n- Rebuilt later.  It is tall."),
            ["History", "- Built in 1904", "- Rebuilt later.", "It is tall."],
        )

    def test_the_words_that_count_are_lower_cased_without_their_plurals(self) -> None:
        self.assertEqual(terms("The Keepers' lamps and Fastnet's rocks"),
                         ["keeper", "lamp", "fastnet", "rock"])


class BudgetTests(unittest.TestCase):
    """A budget is never exceeded, whatever the text, the budget or the question."""

    WORDS = ("light", "tower", "keeper", "Fastnet", "1904", "rock", "storm", "ship", "café",
             "naïve", "東京", "lamp", "white", "tall", "the", "and", "is", "was")

    def sentence(self, rng: random.Random) -> str:
        words = [rng.choice(self.WORDS) for _ in range(rng.randint(1, 30))]
        words[0] = words[0].capitalize()
        return " ".join(words) + rng.choice((".", "!", "?", "", ".\n"))

    def test_any_text_any_budget(self) -> None:
        rng = random.Random(1904)
        for _ in range(400):
            text = " ".join(self.sentence(rng) for _ in range(rng.randint(0, 40)))
            budget = rng.randint(0, 200)
            focus = " ".join(rng.choice(self.WORDS) for _ in range(rng.randint(0, 4)))
            with self.subTest(text=text[:60], budget=budget, focus=focus):
                result = excerpt(text, budget, focus)
                self.assertLessEqual(estimate(result), budget)
                self.assertEqual(result, excerpt(text, budget, focus), "the same every time")
                flat = " ".join(text.split())
                at = 0
                for piece in pieces(result):
                    found = flat.find(piece, at)
                    self.assertGreaterEqual(found, 0, f"{piece!r} is not from the text, in order")
                    at = found + len(piece)

    def test_a_token_is_a_byte_in_three_rounded_up(self) -> None:
        self.assertEqual(estimate(""), 0)
        self.assertEqual(estimate("abcd"), 2)
        self.assertEqual(estimate("東京"), 2)  # six bytes


if __name__ == "__main__":
    unittest.main()
