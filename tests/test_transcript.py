"""The prompt a run reads: that it only grows, and what goes into it.

Needs the app's own requirements; without them the whole module is skipped.
Nothing here runs a model. What is checked is text: that each prompt of a run
begins with the one before it, to the character, which is what lets the model
skip re-reading it.
"""

from __future__ import annotations

import random
import unittest

try:
    from app.agent.calls import CALL_RULES, TOOLS_HEADER, render_call
    from app.agent.context import Part, merged
    from app.agent.transcript import (
        NO_MORE_CALLS,
        PROJECT_RULES,
        RUN_RULE,
        SHAPE,
        SYSTEM_WITHOUT_TOOLS,
        TOOL_RULES,
        WHO,
        Transcript,
        system_for,
    )
    from app.chatml import THINK_BLOCK, render_chatml
    from app.tools.base import REGISTRY, available
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

CALL = THINK_BLOCK + "Let me look.\n\n" + render_call("weather", {"location": "Paris"})


def fresh(question: str = "Do I need a coat in Paris?") -> Transcript:
    parts = [Part("system", "instruction", "Be brief."), Part("user", "question", question)]
    return Transcript(parts, own=1)


class SystemTextTests(unittest.TestCase):
    def setUp(self) -> None:
        support.with_settings(self, allow_outbound=True)

    def test_it_is_the_tools_in_the_models_own_format_then_who_it_is_and_how(self) -> None:
        tools = available()
        text = system_for(tools)
        self.assertTrue(text.startswith(TOOLS_HEADER))
        self.assertIn(CALL_RULES + "\n\n" + WHO + "\n" + TOOL_RULES + "\n", text)
        self.assertTrue(text.endswith(SHAPE))

    def test_it_tells_the_model_how_to_change_a_project_where_it_can(self) -> None:
        tools = available()
        self.assertIn(PROJECT_RULES, system_for(tools))
        without = [t for t in tools if t.name not in ("write_file", "edit_file")]
        self.assertNotIn(PROJECT_RULES, system_for(without))

    def test_it_says_what_running_a_file_can_do_only_where_a_file_can_be_run(self) -> None:
        tools = available()
        self.assertEqual(RUN_RULE in system_for(tools), "run_file" in {t.name for t in tools})
        with_run = [*tools, REGISTRY["run_file"]] if "run_file" not in {t.name for t in tools} else tools
        self.assertIn(RUN_RULE, system_for(with_run))

    def test_it_is_the_same_characters_every_time(self) -> None:
        self.assertEqual(system_for(available()), system_for(available()))

    def test_it_changes_only_with_what_is_on_offer(self) -> None:
        tools = available()
        self.assertNotEqual(system_for(tools), system_for(tools[:-1]))

    def test_without_tools_it_is_who_the_model_is_and_nothing_more(self) -> None:
        self.assertEqual(system_for([]), SYSTEM_WITHOUT_TOOLS)
        self.assertNotIn("<tools>", system_for([]))

    def test_it_never_says_when(self) -> None:
        # The date goes with each message; the system text may never change.
        self.assertNotIn("2026", system_for(available()))


class GrowingTests(unittest.TestCase):
    def test_a_whole_run_reads_as_one_conversation(self) -> None:
        t = fresh()
        t.call(CALL, "weather")
        t.response("weather", "Paris, France: Overcast, 12.9°C")
        t.answer(THINK_BLOCK + "Yes, take one.")
        self.assertEqual(
            [(m["role"], m["content"][:16]) for m in t.messages()],
            [("system", "Be brief."), ("user", "Do I need a coat"), ("assistant", CALL[:16]),
             ("user", "<tool_response>\n"), ("assistant", THINK_BLOCK[:16])],
        )

    def test_each_prompt_begins_with_the_one_before_and_what_the_model_then_said(self) -> None:
        t = fresh()
        before = render_chatml(t.messages(), thinking="off")
        said = CALL[len(THINK_BLOCK):]
        t.call(CALL, "weather")
        after = render_chatml(t.messages(), thinking="off")
        # What the model was given and what it wrote, then the end of its turn.
        self.assertTrue(after.startswith(before + said + "<|im_end|>\n"))
        t.response("weather", "Overcast.")
        opener = "<|im_start|>assistant\n" + THINK_BLOCK
        self.assertTrue(render_chatml(t.messages(), thinking="off").startswith(after[:-len(opener)]))

    def test_what_was_handed_to_a_reply_does_not_change_when_the_run_goes_on(self) -> None:
        t = fresh()
        handed = t.messages()
        copy = [dict(m) for m in handed]
        t.call(CALL, "weather")
        t.response("weather", "Overcast.")
        self.assertEqual(handed, copy)

    def test_the_two_speakers_always_alternate_whatever_is_added(self) -> None:
        rng = random.Random(7)
        for _ in range(50):
            t = fresh()
            for _ in range(rng.randint(1, 8)):
                rng.choice([
                    lambda: t.call(CALL, "weather"),
                    lambda: t.response("weather", "x"),
                    lambda: t.note(NO_MORE_CALLS),
                    lambda: t.answer("y"),
                ])()
            roles = [m["role"] for m in t.messages()[1:]]
            self.assertTrue(all(a != b for a, b in zip(roles, roles[1:])), roles)

    def test_a_note_shares_the_turn_of_the_last_result(self) -> None:
        t = fresh()
        t.call(CALL, "weather")
        t.response("weather", "Overcast.")
        t.note(NO_MORE_CALLS)
        self.assertEqual(
            t.messages()[-1]["content"],
            "<tool_response>\nOvercast.\n</tool_response>\n\n" + NO_MORE_CALLS,
        )

    def test_each_thing_added_is_a_part_of_its_kind(self) -> None:
        t = fresh()
        t.call(CALL, "weather")
        t.response("weather", "Overcast.", ok=False)
        t.note(NO_MORE_CALLS)
        t.answer("Yes.")
        self.assertEqual(
            [(p.role, p.kind, p.tools) for p in t.run_parts()],
            [("user", "question", ()), ("assistant", "call", ("weather",)),
             ("user", "response", ("weather",)), ("user", "note", ()), ("assistant", "answer", ())],
        )

    def test_a_failure_says_so(self) -> None:
        t = fresh()
        t.response("weather", "No place called 'Atlantis' was found.", ok=False)
        self.assertEqual(
            t.parts()[-1].content,
            "<tool_response>\nError: No place called 'Atlantis' was found.\n</tool_response>",
        )

    def test_what_the_run_added_begins_with_its_message(self) -> None:
        t = fresh()
        t.call(CALL, "weather")
        self.assertEqual(t.run_parts()[0], Part("user", "question", "Do I need a coat in Paris?"))

    def test_joined_the_parts_are_the_messages(self) -> None:
        t = fresh()
        t.call(CALL, "weather")
        t.response("weather", "Overcast.")
        self.assertEqual(merged(t.parts()), t.messages())

    def test_rendered_is_how_the_engine_reads_it(self) -> None:
        t = fresh()
        self.assertEqual(t.rendered(), render_chatml(t.messages()))


if __name__ == "__main__":
    unittest.main()
