"""What of a conversation the next message is read with, and how it is fitted
to the window. Pure: no app, no model; counting is a function the test gives.
"""

from __future__ import annotations

import unittest

try:
    from app.agent.context import (
        RUN_ROOM, RUN_ROOM_MOST, STALE, SUMMARY_HEADING, SUMMARY_TOKENS, TRIM_STEP, TURN_ROOM,
        Part, Turn, blocks, cap, compact, fit, key, merged, replayed, run_room, stale, summary,
        turns_from, turns_from_history,
    )
    from app.llm import render_chatml
    from app.text import estimate
    from app.tools import base
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

SYSTEM = "You are helpful."
KEY = key(SYSTEM)
HOUR = 3600.0


def words(text: str) -> int:
    """A count: a word is a token."""
    return len(text.split())


class Counting:
    """A count that remembers every text it was asked about."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, text: str) -> int:
        self.asked.append(text)
        return estimate(text)


def said(question: str, answer: str, *, at: float = 0.0, system_key: str = KEY, middle=()) -> Turn:
    """A turn kept as it was sent."""
    parts = (Part("user", "question", question), *middle, Part("assistant", "answer", answer))
    return Turn(question, answer, parts=parts, system_key=system_key, at=at)


WEATHER = Part("user", "observation", '<data tool="weather">Paris: overcast, 17.9°C.</data>', ("weather",))
PAGE = Part("user", "observation", '<data tool="read_url">Page “Tides”: two a day.</data>', ("read_url",))
ACTION = Part("assistant", "action", '{"tool": "weather", "args": {"location": "Paris"}}')
CUE = Part("user", "cue", "Answer the question using only the data above.")
FRESH = {"weather": 3 * HOUR}.get


class MergeTests(unittest.TestCase):
    def test_parts_of_one_speaker_are_one_message_after_a_blank_line(self) -> None:
        self.assertEqual(
            merged([Part("system", "instruction", SYSTEM), Part("user", "question", "Hi"),
                    Part("user", "data", "Found"), Part("assistant", "answer", "Hello")]),
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Hi\n\nFound"},
             {"role": "assistant", "content": "Hello"}],
        )

    def test_a_part_survives_being_stored(self) -> None:
        self.assertEqual(Part.from_dict(WEATHER.as_dict()), WEATHER)


class TurnTests(unittest.TestCase):
    def test_an_older_conversation_is_its_messages_in_pairs(self) -> None:
        history = [
            {"role": "user", "content": "One?"}, {"role": "assistant", "content": "One."},
            {"role": "user", "content": "Lost?"},
            {"role": "user", "content": "Two?"}, {"role": "assistant", "content": "Two."},
        ]
        self.assertEqual(
            [(t.question, t.answer) for t in turns_from_history(history)],
            [("One?", "One."), ("Lost?", ""), ("Two?", "Two.")],
        )

    def test_a_stored_conversation_is_what_was_sent_where_it_was_kept(self) -> None:
        messages = [
            {"id": "q1", "role": "user", "content": "Coat?", "created_at": 1.0},
            {"id": "a1", "role": "assistant", "content": "Yes.", "created_at": 2.0},
            {"id": "q2", "role": "user", "content": "Why?", "created_at": 3.0},
            {"id": "a2", "role": "assistant", "content": "Rain.", "created_at": 4.0},
        ]
        parts = [Part("user", "question", "Coat?"), ACTION, WEATHER, CUE, Part("assistant", "answer", "Yes.")]
        records = [{"question_id": "q1", "answer_id": "a1", "system_key": KEY,
                    "parts": [p.as_dict() for p in parts], "created_at": 2.5}]
        first, second = turns_from(messages, records)
        self.assertEqual((first.parts, first.system_key, first.at), (tuple(parts), KEY, 2.5))
        self.assertEqual((second.question, second.answer, second.parts), ("Why?", "Rain.", ()))

    def test_a_record_that_does_not_match_its_answer_is_not_used(self) -> None:
        messages = [{"id": "q1", "role": "user", "content": "Coat?"},
                    {"id": "a9", "role": "assistant", "content": "Yes."}]
        records = [{"question_id": "q1", "answer_id": "a1", "system_key": KEY,
                    "parts": [WEATHER.as_dict()], "created_at": 1.0}]
        (turn,) = turns_from(messages, records)
        self.assertEqual(turn.parts, ())


class ReplayTests(unittest.TestCase):
    TURN = said("Coat?", "Yes, it is cold.", at=1000.0, middle=(ACTION, WEATHER, CUE))

    def test_under_the_same_system_text_a_turn_is_replayed_as_it_was_sent(self) -> None:
        self.assertEqual(replayed(self.TURN, KEY, now=1000.0, fresh_for=FRESH), list(self.TURN.parts))

    def test_under_another_it_is_the_question_what_was_found_and_the_answer(self) -> None:
        self.assertEqual(
            [p.kind for p in replayed(self.TURN, key("Another persona."), now=1000.0)],
            ["question", "observation", "answer"],
        )

    def test_a_turn_kept_only_as_messages_is_replayed_as_them(self) -> None:
        turn = Turn("Coat?", "Yes.")
        self.assertEqual(
            merged(replayed(turn, KEY, now=0.0)),
            [{"role": "user", "content": "Coat?"}, {"role": "assistant", "content": "Yes."}],
        )

    def test_a_result_out_of_date_is_replayed_as_a_line_that_says_so_and_never_changes(self) -> None:
        fresh = replayed(self.TURN, KEY, now=1000.0 + 2 * HOUR, fresh_for=FRESH)
        later = replayed(self.TURN, KEY, now=1000.0 + 4 * HOUR, fresh_for=FRESH)
        much_later = replayed(self.TURN, KEY, now=1000.0 + 400 * HOUR, fresh_for=FRESH)
        self.assertIn(WEATHER, fresh)
        self.assertNotIn(WEATHER, later)
        self.assertIn(stale(WEATHER), later)
        self.assertEqual(stale(WEATHER).content, f'<data tool="weather">{STALE}</data>')
        self.assertEqual(later, much_later)

    def test_a_result_that_does_not_go_out_of_date_is_replayed_however_old(self) -> None:
        turn = said("Tides?", "Two a day.", at=0.0, middle=(PAGE,))
        self.assertIn(PAGE, replayed(turn, KEY, now=10_000 * HOUR, fresh_for=FRESH))

    def test_a_long_message_replayed_compact_is_cut(self) -> None:
        turn = Turn("word " * 5000, "Done.")
        question = compact(turn)[0]
        self.assertLessEqual(estimate(question.content), TURN_ROOM)
        self.assertTrue(question.content.endswith("…"))

    def test_the_tools_say_how_long_their_results_stay_true(self) -> None:
        self.assertEqual(base.fresh_for("weather"), 3 * HOUR)
        self.assertEqual(base.fresh_for("currency"), 24 * HOUR)
        self.assertEqual(base.fresh_for("clock"), 600)
        self.assertIsNone(base.fresh_for("wikipedia"))
        self.assertIsNone(base.fresh_for("no such tool"))


class SummaryTests(unittest.TestCase):
    def test_each_dropped_turn_is_its_message_and_the_first_sentence_of_its_answer(self) -> None:
        part = summary([Turn("Coat?", "Yes. It is cold out."), Turn("Why?", "Rain is coming.")])
        self.assertEqual(part.kind, "summary")
        self.assertEqual(part.content, f"{SUMMARY_HEADING}\n- Coat? — Yes.\n- Why? — Rain is coming.")

    def test_it_keeps_the_latest_that_fit(self) -> None:
        turns = [Turn(f"Question {i} " + "about lighthouses " * 10, f"Answer {i}.") for i in range(30)]
        part = summary(turns)
        self.assertLessEqual(estimate(part.content), SUMMARY_TOKENS)
        self.assertIn("Question 29", part.content)
        self.assertNotIn("Question 0 ", part.content)

    def test_nothing_dropped_is_no_summary(self) -> None:
        self.assertIsNone(summary([]))


class CapTests(unittest.TestCase):
    def test_a_message_that_fits_is_left_alone(self) -> None:
        self.assertEqual(cap("Short.", 10, words), ("Short.", False))

    def test_a_longer_one_is_cut_to_fit_from_its_start(self) -> None:
        text, cut = cap("word " * 1000, 100, words)
        self.assertTrue(cut)
        self.assertLessEqual(words(text), 100)
        self.assertTrue(text.startswith("word word"))

    def test_it_fits_even_where_the_estimate_is_short_of_the_count(self) -> None:
        def per_character(text: str) -> int:
            return len(text)

        text, cut = cap("1234567890" * 100, 120, per_character)
        self.assertTrue(cut)
        self.assertLessEqual(len(text), 120)
        self.assertGreater(len(text), 60)


class FitTests(unittest.TestCase):
    def conversation(self, n: int, size: int = 40) -> list[Turn]:
        return [said(f"Question {i}: " + "tell me more " * size, f"Answer {i}. " + "it is so " * size)
                for i in range(n)]

    def test_everything_that_fits_is_read_and_nothing_changes(self) -> None:
        turns = turns_from_history([
            {"role": "user", "content": "One?"}, {"role": "assistant", "content": "One."},
        ])
        window = fit(SYSTEM, turns, [Part("user", "question", "Two?")], room=4000, count=words, now=0)
        self.assertEqual(window.messages, [
            {"role": "system", "content": SYSTEM}, {"role": "user", "content": "One?"},
            {"role": "assistant", "content": "One."}, {"role": "user", "content": "Two?"},
        ])
        self.assertEqual((window.turns, window.summarised), (1, 0))
        self.assertEqual(window.tokens, words(render_chatml(window.messages)))

    def test_too_much_drops_the_oldest_turns_whole_and_sums_them_up(self) -> None:
        turns = self.conversation(20)
        window = fit(SYSTEM, turns, [Part("user", "question", "And now?")], room=2000,
                     count=estimate, now=0)
        self.assertGreater(window.summarised, 0)
        self.assertEqual(window.turns + window.summarised, 20)
        self.assertLessEqual(window.tokens, 2000)
        self.assertTrue(window.messages[1]["content"].startswith(SUMMARY_HEADING))
        kept = window.messages[1]["content"].split("\n\n", 1)[1]
        self.assertTrue(kept.startswith(f"Question {window.summarised}:"))
        self.assertEqual(window.messages[-1]["content"], "And now?")

    def test_a_cut_falls_only_where_a_block_begins(self) -> None:
        turns = self.conversation(20)
        sizes = [estimate(render_chatml(merged(t.parts))) for t in turns]
        window = fit(SYSTEM, turns, [Part("user", "question", "And now?")], room=2000,
                     count=estimate, now=0)
        self.assertIn(window.summarised, blocks(sizes))

    def test_blocks_begin_once_a_block_has_its_tokens(self) -> None:
        self.assertEqual(blocks([300, 300, 300, 300, 300], step=600), [2, 4, 5])
        self.assertEqual(blocks([700, 10, 700], step=600), [1, 3])
        self.assertEqual(blocks([], step=600), [0])

    def test_a_larger_window_trims_less(self) -> None:
        turns = self.conversation(20)
        small = fit(SYSTEM, turns, [Part("user", "question", "?")], room=2000, count=estimate, now=0)
        large = fit(SYSTEM, turns, [Part("user", "question", "?")], room=4000, count=estimate, now=0)
        self.assertLess(large.summarised, small.summarised)

    def test_a_long_conversation_is_fitted_with_a_few_counts_and_not_one_a_turn(self) -> None:
        counting = Counting()
        window = fit(SYSTEM, self.conversation(60), [Part("user", "question", "?")], room=2000,
                     count=counting, now=0)
        self.assertGreater(window.summarised, 30)
        self.assertLessEqual(len(counting.asked), 3)
        self.assertEqual(len(counting.asked), len(set(counting.asked)), "nothing counted twice")

    def test_turn_after_turn_each_prompt_extends_the_last_but_where_a_block_is_cut(self) -> None:
        turns: list[Turn] = []
        prompts: list[str] = []
        cuts: list[int] = []
        for i in range(40):
            question = f"Question {i}: " + "tell me more " * 15
            window = fit(SYSTEM, turns, [Part("user", "question", question)], room=1500,
                         count=estimate, now=0)
            prompts.append(render_chatml(window.messages))
            cuts.append(window.summarised)
            self.assertLessEqual(window.tokens, 1500)
            turns.append(said(question, f"Answer {i}. " + "it is so " * 15))
        moves = 0
        for i in range(1, 40):
            if cuts[i] == cuts[i - 1]:
                answer = turns[i - 1].answer
                self.assertTrue(prompts[i].startswith(prompts[i - 1] + answer), f"turn {i}")
            else:
                moves += 1
        sizes = [estimate(render_chatml(merged(t.parts))) for t in turns]
        self.assertGreater(moves, 0)
        self.assertLessEqual(moves, sum(sizes) // TRIM_STEP + 1)
        self.assertEqual(sorted(set(cuts)), [c for c in sorted(set(cuts)) if c in blocks(sizes) or c == 0])


class RunRoomTests(unittest.TestCase):
    def test_a_larger_window_gives_a_run_more_room_up_to_a_ceiling(self) -> None:
        self.assertEqual(run_room(2048), RUN_ROOM)
        self.assertEqual(run_room(4096), 1024)
        self.assertEqual(run_room(8192), 2048)
        self.assertEqual(run_room(16384), RUN_ROOM_MOST)
        self.assertEqual(run_room(1024), RUN_ROOM)


if __name__ == "__main__":
    unittest.main()
