"""look_back: finding what was said earlier in the conversation, once the
window has given the oldest turns only as a brief.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import unittest

try:
    from app.agent.context import LOOK_BACK_LINE, Part, Turn, fit, searchable
    from app.text import estimate
    from app.tools import REGISTRY
    from app.tools.base import CALL, CallContext
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

TRIP = ("Here is my trip plan: I leave from Zurich on the 3rd, stay two nights in Lyon, "
        "and my return flight is LX 1824 on the 9th at 18:40. Just say OK.")
CALC = Turn("Work out 4817 * 3.75.", "It is 18,063.75.", parts=(
    Part("user", "question", "Work out 4817 * 3.75."),
    Part("assistant", "call", "<tool_call>…</tool_call>", ("calculator",)),
    Part("user", "response", "<tool_response>\n4817*3.75 = 18,063.75\n</tool_response>",
         ("calculator",)),
    Part("assistant", "answer", "It is 18,063.75."),
))
TURNS = [
    Turn(TRIP, "OK."),
    CALC,
    Turn("Why is the sky blue?", "Shorter wavelengths scatter more in the air."),
]


class LookBackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tool = REGISTRY["look_back"]
        CALL.set(CallContext(conversation_id="c", earlier=searchable(TURNS)))

    async def test_it_finds_an_old_message_by_its_words_and_says_which_turn(self) -> None:
        result = await self.tool.run(query="return flight number")
        self.assertTrue(result.ok)
        self.assertIn("Turn 1, you asked:", result.summary)
        self.assertIn("LX 1824", result.summary)
        self.assertEqual(result.data["action"], "look_back")

    async def test_it_finds_what_a_tool_returned(self) -> None:
        result = await self.tool.run(query="4817 calculation")
        self.assertIn("Turn 2, calculator returned: 4817*3.75 = 18,063.75", result.summary)

    async def test_a_turn_can_be_read_whole_by_its_number(self) -> None:
        result = await self.tool.run(turn=2)
        self.assertEqual(result.data["notes"], [
            "Turn 2, you asked: Work out 4817 * 3.75.",
            "Turn 2, calculator returned: 4817*3.75 = 18,063.75",
            "Turn 2, I answered: It is 18,063.75.",
        ])

    async def test_nothing_found_and_nothing_asked_are_said_plainly(self) -> None:
        none = await self.tool.run(query="submarine")
        self.assertTrue(none.ok)
        self.assertEqual(none.summary, "Nothing earlier in this conversation matches 'submarine'.")
        empty = await self.tool.run(query="  ")
        self.assertFalse(empty.ok)
        missing = await self.tool.run(turn=9)
        self.assertFalse(missing.ok)
        self.assertIn("3 earlier turns", missing.summary)

    async def test_a_conversation_with_nothing_before_has_nothing_to_find(self) -> None:
        CALL.set(CallContext(conversation_id="c"))
        result = await self.tool.run(query="flight")
        self.assertEqual(result.summary, "There is nothing earlier in this conversation.")

    async def test_what_the_model_reads_is_whole_lines_within_its_budget(self) -> None:
        long = [Turn(f"Question {i}: " + "the harbour lighthouse keeper " * 40, f"Answer {i}.")
                for i in range(10)]
        CALL.set(CallContext(conversation_id="c", earlier=searchable(long)))
        result = await self.tool.run(query="lighthouse keeper")
        read = self.tool.observation(result, "", 120)
        self.assertLessEqual(estimate(read), 120)
        self.assertTrue(read.startswith("Turn "))
        self.assertTrue(all(line.startswith("Turn ") for line in read.splitlines()))


class BriefPointsToItTests(unittest.TestCase):
    def test_the_brief_ends_by_saying_the_turns_can_be_searched(self) -> None:
        turns = [Turn(f"Question {i}: " + "tell me more " * 60, f"Answer {i}.") for i in range(20)]
        window = fit("System.", turns, [Part("user", "question", "And now?")], room=2000,
                     count=estimate, now=0, brief=300, tail=LOOK_BACK_LINE)
        self.assertGreater(window.summarised, 0)
        self.assertIn(LOOK_BACK_LINE, window.messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
