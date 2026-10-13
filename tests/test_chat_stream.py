"""One message through ``/api/chat``: what the stream says, in what order, what
the model is given to read, and what is left in the database afterwards.

Needs the app's own requirements (FastAPI, httpx for the test client); without
them the whole module is skipped. It needs no model and no network: the engine
plays back prepared text, and the tools that would go out are replaced.
"""

from __future__ import annotations

import unittest

try:
    from app import db
    from app.personas import get_persona
    from app.router import ROUTE_SYSTEM
    from app.tools.base import ToolResult, tool_menu
    from tests import support
    from tests.support import ChatCase, PlayedEngine, only, shape, step
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

# The end of every stream that reaches an answer.
ANSWER = ["step:answer:running", "start", "token", "step:answer:ok", "done"]

# What follows a tool's output in the question the model reads.
INSTRUCTION = (
    "Answer the question using only the data above. It is current and correct. "
    "Do not say you lack real-time access, and do not recalculate any figure."
)

CALC_REFUSAL = (
    "That is not something this calculator handles. It does numbers, the usual "
    "operators, and functions like sqrt, round, log and factorial."
)

WEATHER = ToolResult(
    True,
    "Paris, France: overcast, 17.9°C (feels 17.2°C), wind 9.4 km/h.",
    card="weather",
    data={"place": "Paris", "country": "France", "temperature": 17.9},
)


def with_data(question: str, *facts: str) -> str:
    lines = "\n".join(f"- {fact}" for fact in facts)
    return f"{question}\n\nLive data, retrieved just now by a tool:\n{lines}\n\n{INSTRUCTION}"


class PlainAnswerTests(ChatCase):
    """A persona without tools, a message that is not a command."""

    def test_a_plain_message_is_answered_in_one_pass_with_nothing_routed(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.route_prompts, [])
        self.assertEqual(
            self.engine.answer_prompts,
            [[
                {"role": "system", "content": get_persona("explainer").system},
                {"role": "user", "content": "Why is the sky blue?"},
            ]],
        )
        self.assertEqual(self.engine.samplings, [get_persona("explainer").sampling()])

    def test_the_first_event_names_the_conversation(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        first = stream[0]
        self.assertEqual(first[0], "conversation")
        self.assertEqual(set(first[1]), {"id", "title", "created", "skill", "persona"})
        self.assertEqual(first[1]["title"], "Why is the sky blue?")
        self.assertIs(first[1]["created"], True)
        self.assertIsNone(first[1]["skill"])
        self.assertEqual(first[1]["persona"], "explainer")

    def test_the_answer_step_opens_and_closes_around_the_writing(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        done = only(stream, "done")[0]
        self.assertEqual(
            step(stream, "answer", "running"),
            {"id": "answer", "kind": "model", "status": "running",
             "label": "Writing the answer", "glyph": "✦"},
        )
        self.assertEqual(
            step(stream, "answer", "ok"),
            {"id": "answer", "kind": "model", "status": "ok", "label": "Answer",
             "glyph": "✦", "ms": done["total_ms"]},
        )

    def test_start_says_which_request_this_is_and_how_full_the_window_is(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        start = only(stream, "start")[0]
        self.assertEqual(set(start), {"request_id", "queue_wait_ms", "prompt_tokens", "n_ctx"})
        self.assertTrue(start["request_id"])
        self.assertEqual(start["n_ctx"], self.engine.cfg.n_ctx)
        self.assertEqual(
            start["prompt_tokens"], self.engine.prompt_tokens(self.engine.answer_prompts[0])
        )

    def test_the_words_arrive_as_tokens_and_the_first_carries_its_wait(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        stream = self.chat("Why is the sky blue?", persona="explainer")
        tokens = only(stream, "token")
        self.assertEqual("".join(t["t"] for t in tokens), "Light scatters off air.")
        self.assertIn("ttft_ms", tokens[0])
        for later in tokens[1:]:
            self.assertEqual(set(later), {"t"})

    def test_done_carries_the_figures_and_not_the_text(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        stream = self.chat("Why is the sky blue?", persona="explainer")
        done = only(stream, "done")[0]
        self.assertEqual(
            set(done),
            {"cancelled", "ttft_ms", "queue_wait_ms", "prompt_tokens", "completion_tokens",
             "prefill_tps", "gen_tps", "total_ms", "rss_mb", "tool_calls"},
        )
        self.assertIs(done["cancelled"], False)
        self.assertEqual(done["completion_tokens"], 4)
        self.assertEqual(done["tool_calls"], 0)
        self.assertEqual(done["prompt_tokens"], only(stream, "start")[0]["prompt_tokens"])

    def test_the_question_and_the_answer_are_stored_with_the_figures(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        stream = self.chat("Why is the sky blue?", persona="explainer")
        done = only(stream, "done")[0]
        convo = self.conversation(stream)
        self.assertEqual(convo["persona"], "explainer")
        self.assertEqual([m["role"] for m in convo["messages"]], ["user", "assistant"])
        asked, answered = convo["messages"]
        self.assertEqual(asked["content"], "Why is the sky blue?")
        self.assertEqual(answered["content"], "Light scatters off air.")
        for figure in ("ttft_ms", "gen_tps", "prompt_tokens", "completion_tokens"):
            with self.subTest(figure=figure):
                self.assertEqual(answered[figure], done[figure])
        self.assertEqual(answered["tool_calls"], [])

    def test_an_answer_is_one_row_of_throughput_history(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        done = only(stream, "done")[0]
        rows = db.recent_runs("chat")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "explainer")
        self.assertEqual(rows[0]["gen_tokens"], done["completion_tokens"])
        self.assertEqual(rows[0]["prefill_tps"], done["prefill_tps"])

    def test_a_second_message_is_read_with_the_first_exchange_before_it(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        first = self.chat("Why is the sky blue?", persona="explainer")
        cid = only(first, "conversation")[0]["id"]
        second = self.chat("And at sunset?", persona="explainer", conversation_id=cid)
        self.assertEqual(only(second, "conversation")[0]["id"], cid)
        self.assertIs(only(second, "conversation")[0]["created"], False)
        self.assertEqual(
            self.engine.answer_prompts[1],
            [
                {"role": "system", "content": get_persona("explainer").system},
                {"role": "user", "content": "Why is the sky blue?"},
                {"role": "assistant", "content": "Light scatters off air."},
                {"role": "user", "content": "And at sunset?"},
            ],
        )
        self.assertEqual(len(self.conversation(second)["messages"]), 4)

    def test_a_conversation_id_nobody_has_starts_a_new_conversation(self) -> None:
        stream = self.chat("Hello", persona="explainer", conversation_id="no-such-conversation")
        first = only(stream, "conversation")[0]
        self.assertIs(first["created"], True)
        self.assertNotEqual(first["id"], "no-such-conversation")

    def test_a_persona_nobody_has_is_the_default_one(self) -> None:
        stream = self.chat("Hello", persona="no-such-persona")
        self.assertEqual(only(stream, "conversation")[0]["persona"], "explainer")

    def test_an_empty_message_is_refused_before_anything_is_stored(self) -> None:
        res = self.client.post("/api/chat", json={"message": ""})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(db.list_conversations(), [])


class SlashCommandTests(ChatCase):
    """A command names its tool. It works for every persona and costs no pass."""

    def test_a_command_runs_its_tool_without_asking_the_model_which(self) -> None:
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             *ANSWER],
        )
        self.assertEqual(self.engine.route_prompts, [])
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "Calculator",
             "detail": "slash", "glyph": "⚡", "tool": "calculator", "ms": 0},
        )
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)

    def test_the_tool_step_says_what_it_was_given_and_how_long_it_took(self) -> None:
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(
            step(stream, "tool-0", "running"),
            {"id": "tool-0", "kind": "tool", "status": "running", "label": "Calculator",
             "glyph": "⚡", "tool": "calculator", "args": {"expression": "2+2"}},
        )
        finished = step(stream, "tool-0", "ok")
        self.assertGreaterEqual(finished.pop("ms"), 0)
        self.assertEqual(
            finished,
            {"id": "tool-0", "kind": "tool", "status": "ok", "label": "Calculator",
             "glyph": "⚡", "tool": "calculator", "detail": None},
        )

    def test_the_card_carries_the_whole_result(self) -> None:
        stream = self.chat("/calc 2+2", persona="explainer")
        card = only(stream, "card")[0]
        self.assertEqual(set(card), {"step_id", "card", "tool", "data"})
        self.assertEqual(card["step_id"], "tool-0")
        self.assertEqual(card["card"], "calculator")
        self.assertEqual(card["tool"], "calculator")
        self.assertEqual(card["data"]["expression"], "2+2")
        self.assertEqual(card["data"]["result"], "4")

    def test_the_result_is_put_inside_the_question_the_model_reads(self) -> None:
        self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(
            self.engine.answer_prompts[0],
            [
                {"role": "system", "content": get_persona("explainer").system},
                {"role": "user", "content": with_data("/calc 2+2", "calculator: 2+2 = 4")},
            ],
        )

    def test_the_stored_question_is_the_bare_one_and_the_call_hangs_off_the_answer(self) -> None:
        stream = self.chat("/calc 2+2", persona="explainer")
        convo = self.conversation(stream)
        asked, answered = convo["messages"]
        self.assertEqual(asked["content"], "/calc 2+2")
        self.assertEqual(convo["orphan_tool_calls"], [])
        self.assertEqual(len(answered["tool_calls"]), 1)
        call = answered["tool_calls"][0]
        self.assertEqual(call["tool"], "calculator")
        self.assertEqual(call["via"], "slash")
        self.assertEqual(call["args"], {"expression": "2+2"})
        self.assertEqual(call["card"], "calculator")
        self.assertEqual(call["summary"], "2+2 = 4")
        self.assertIs(call["ok"], True)
        self.assertEqual(call["data"], only(stream, "card")[0]["data"])

    def test_the_next_message_does_not_see_the_earlier_tool_result(self) -> None:
        first = self.chat("/calc 2+2", persona="explainer")
        cid = only(first, "conversation")[0]["id"]
        self.chat("Why?", persona="explainer", conversation_id=cid)
        self.assertEqual(
            [m["content"] for m in self.engine.answer_prompts[1][1:]],
            ["/calc 2+2", "A short answer.", "Why?"],
        )

    def test_an_answer_with_nothing_in_it_is_not_stored_and_its_call_is_left_loose(self) -> None:
        self.use_engine(PlayedEngine(""))
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             "step:answer:running", "start", "step:answer:ok", "done"],
        )
        self.assertEqual(only(stream, "done")[0]["completion_tokens"], 0)
        convo = self.conversation(stream)
        self.assertEqual([m["role"] for m in convo["messages"]], ["user"])
        self.assertEqual(len(convo["orphan_tool_calls"]), 1)
        self.assertEqual(db.recent_runs("chat"), [])

    def test_with_tools_switched_off_a_command_is_only_text(self) -> None:
        support.with_settings(self, tools_enabled=False)
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.answer_prompts[0][-1]["content"], "/calc 2+2")


class AgentRoutingTests(ChatCase):
    """The one persona that pays for routing: a pattern first, then the model."""

    def test_a_pattern_match_costs_no_pass_and_is_still_announced(self) -> None:
        stream = self.chat("15% of 200", persona="agent")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:running", "step:route:ok", "step:tool-0:running",
             "step:tool-0:ok", "card", *ANSWER],
        )
        self.assertEqual(self.engine.route_prompts, [])
        self.assertEqual(
            step(stream, "route", "running"),
            {"id": "route", "kind": "route", "status": "running", "label": "Choosing a tool",
             "glyph": "◇"},
        )
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "Calculator",
             "detail": "heuristic", "glyph": "⚡", "tool": "calculator", "ms": 0},
        )
        self.assertEqual(
            self.engine.answer_prompts[0][-1]["content"],
            with_data("15% of 200", "calculator: 15/100*200 = 30"),
        )
        self.assertEqual(self.conversation(stream)["messages"][1]["tool_calls"][0]["via"], "heuristic")

    def test_with_no_pattern_the_model_is_asked_and_may_say_none(self) -> None:
        stream = self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(
            shape(stream), ["conversation", "step:route:running", "step:route:ok", *ANSWER]
        )
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "No tool needed",
             "detail": "model", "glyph": "◇", "tool": None, "ms": support.ROUTE_MS},
        )
        self.assertEqual(
            self.engine.answer_prompts[0],
            [
                {"role": "system", "content": get_persona("agent").system},
                {"role": "user", "content": "Suggest a name for my boat"},
            ],
        )

    def test_the_routing_pass_reads_a_menu_and_the_message_and_nothing_else(self) -> None:
        self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(
            self.engine.route_prompts,
            [[
                {"role": "system", "content": ROUTE_SYSTEM.format(menu=tool_menu())},
                {"role": "user", "content": "Suggest a name for my boat"},
            ]],
        )

    def test_the_routing_pass_reads_no_more_than_600_characters(self) -> None:
        long = "Suggest a name for my boat. " * 30
        self.chat(long, persona="agent")
        self.assertEqual(self.engine.route_prompts[0][1]["content"], long.strip()[:600])

    def test_the_routing_pass_does_not_see_earlier_turns(self) -> None:
        first = self.chat("Suggest a name for my boat", persona="agent")
        cid = only(first, "conversation")[0]["id"]
        self.chat("Something shorter", persona="agent", conversation_id=cid)
        self.assertEqual(len(self.engine.route_prompts[1]), 2)
        self.assertEqual(self.engine.route_prompts[1][1]["content"], "Something shorter")
        self.assertEqual(len(self.engine.answer_prompts[1]), 4)

    def test_the_model_can_choose_a_tool_and_its_result_reaches_the_answer(self) -> None:
        self.use_engine(PlayedEngine(decision={"tool": "weather", "args": {"location": "Paris"}}))
        asked = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris", persona="agent")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:running", "step:route:ok", "step:tool-0:running",
             "step:tool-0:ok", "card", *ANSWER],
        )
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "Weather",
             "detail": "model", "glyph": "☁", "tool": "weather", "ms": support.ROUTE_MS},
        )
        self.assertEqual(asked["weather"], [{"location": "Paris"}])
        self.assertEqual(
            self.engine.answer_prompts[0][-1]["content"],
            with_data("Do I need a coat in Paris", f"weather: {WEATHER.summary}"),
        )
        call = self.conversation(stream)["messages"][1]["tool_calls"][0]
        self.assertEqual(call["via"], "model")
        self.assertEqual(call["data"], WEATHER.data)

    def test_a_choice_that_names_no_real_tool_is_no_tool(self) -> None:
        self.use_engine(PlayedEngine(decision={"tool": "telescope", "args": {}}))
        stream = self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(
            shape(stream), ["conversation", "step:route:running", "step:route:ok", *ANSWER]
        )
        self.assertEqual(step(stream, "route", "ok")["label"], "No tool needed")
        self.assertEqual(step(stream, "route", "ok")["detail"], "model")

    def test_a_reply_cut_off_before_it_closed_is_no_tool(self) -> None:
        self.use_engine(PlayedEngine(decision='{"tool": "wikipedia", "args": {"query": "the long'))
        stream = self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(
            shape(stream), ["conversation", "step:route:running", "step:route:ok", *ANSWER]
        )
        self.assertIsNone(step(stream, "route", "ok")["tool"])

    def test_a_command_nobody_defined_costs_a_pass_the_stream_never_mentions(self) -> None:
        stream = self.chat("/foo bar", persona="agent")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(len(self.engine.route_prompts), 1)

    def test_other_personas_never_reach_a_pattern_or_the_model(self) -> None:
        stream = self.chat("15% of 200", persona="explainer")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.route_prompts, [])


class SkillTests(ChatCase):
    """A skill brings its own calls, its own prompt and its own persona."""

    def test_a_skill_runs_its_own_calls_and_asks_the_model_nothing_about_them(self) -> None:
        stream = self.chat("2+2", skill="do_maths")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             *ANSWER],
        )
        self.assertEqual(self.engine.route_prompts, [])
        self.assertEqual(only(stream, "conversation")[0]["skill"], "do_maths")
        self.assertEqual(only(stream, "conversation")[0]["persona"], "agent")
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "Do the maths",
             "detail": "skill", "glyph": "⚡", "tool": "do_maths", "ms": 0},
        )
        self.assertEqual(self.conversation(stream)["messages"][1]["tool_calls"][0]["via"], "skill")

    def test_the_results_go_into_the_skills_own_prompt(self) -> None:
        self.chat("2+2", skill="do_maths")
        self.assertEqual(
            self.engine.answer_prompts[0],
            [
                {"role": "system", "content": get_persona("agent").system},
                {"role": "user", "content": (
                    "The exact result is below. Restate it, then explain the working in "
                    "two or three short steps. Do not recalculate — the figure given is "
                    "correct.\n\ncalculator: 2+2 = 4"
                )},
            ],
        )

    def test_compare_looks_up_both_sides_one_after_the_other(self) -> None:
        asked = support.played_tools(self, {"wikipedia": [
            ToolResult(True, "Wikipedia — Python: a programming language.", data={"title": "Python"}),
            ToolResult(True, "Wikipedia — Rust: a programming language.", data={"title": "Rust"}),
        ]})
        stream = self.chat("Python vs Rust", skill="compare")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             "step:tool-1:running", "step:tool-1:ok", "card", *ANSWER],
        )
        self.assertEqual(asked["wikipedia"], [{"query": "Python"}, {"query": "Rust"}])
        self.assertEqual([c["step_id"] for c in only(stream, "card")], ["tool-0", "tool-1"])
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 2)
        self.assertTrue(
            self.engine.answer_prompts[0][-1]["content"].endswith(
                "wikipedia: Wikipedia — Python: a programming language.\n\n"
                "wikipedia: Wikipedia — Rust: a programming language."
            )
        )
        self.assertEqual(len(self.conversation(stream)["messages"][1]["tool_calls"]), 2)

    def test_a_skill_takes_its_persona_over_the_one_asked_for(self) -> None:
        stream = self.chat("x = 1", skill="explain_code", persona="brainstorm")
        self.assertEqual(only(stream, "conversation")[0]["persona"], "code")
        self.assertEqual(shape(stream), ["conversation", "step:route:ok", *ANSWER])
        self.assertEqual(self.engine.answer_prompts[0][0]["content"], get_persona("code").system)
        self.assertEqual(self.engine.samplings, [get_persona("code").sampling()])
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 0)

    def test_a_skill_that_found_nothing_to_call_says_so_in_its_prompt(self) -> None:
        stream = self.chat("no link in this", skill="summarise_link")
        self.assertEqual(shape(stream), ["conversation", "step:route:ok", *ANSWER])
        self.assertTrue(
            self.engine.answer_prompts[0][-1]["content"].endswith("(no tool output available)")
        )

    def test_a_skill_still_runs_its_tools_with_tools_switched_off(self) -> None:
        support.with_settings(self, tools_enabled=False)
        stream = self.chat("2+2", skill="do_maths")
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)


class FailedToolTests(ChatCase):
    """A tool that fails is shown failing. The model is told nothing of it."""

    def test_a_failed_tool_is_shown_with_its_reason(self) -> None:
        stream = self.chat("/calc 1/0", persona="explainer")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:failed", "card",
             *ANSWER],
        )
        failed = step(stream, "tool-0", "failed")
        self.assertEqual(failed["detail"], "division by zero")
        self.assertEqual(
            only(stream, "card")[0],
            {"step_id": "tool-0", "card": "error", "tool": "calculator",
             "data": {"message": CALC_REFUSAL}},
        )
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)

    def test_the_model_answers_the_bare_question_as_if_nothing_was_tried(self) -> None:
        self.chat("/calc 1/0", persona="explainer")
        self.assertEqual(self.engine.answer_prompts[0][-1]["content"], "/calc 1/0")

    def test_the_failure_is_stored_without_a_card(self) -> None:
        stream = self.chat("/calc 1/0", persona="explainer")
        call = self.conversation(stream)["messages"][1]["tool_calls"][0]
        self.assertIs(call["ok"], False)
        self.assertIsNone(call["card"])
        self.assertEqual(call["summary"], CALC_REFUSAL)

    def test_a_skill_whose_tool_failed_is_told_only_that_nothing_came_back(self) -> None:
        self.chat("1/0", skill="do_maths")
        self.assertTrue(
            self.engine.answer_prompts[0][-1]["content"].endswith("(no tool output available)")
        )

    def test_a_tool_that_goes_out_is_refused_when_outgoing_requests_are_off(self) -> None:
        support.with_settings(self, allow_outbound=False)
        stream = self.chat("/weather Paris", persona="explainer")
        failed = step(stream, "tool-0", "failed")
        self.assertEqual(failed["detail"], "offline mode")
        self.assertEqual(failed["ms"], 0)
        self.assertIn("outgoing requests switched off", only(stream, "card")[0]["data"]["message"])


class NotReadyTests(ChatCase):
    """The model is not there yet: the stream says so and ends."""

    def test_a_model_still_loading_ends_the_stream_with_an_error_and_no_done(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("Why is the sky blue?", persona="explainer")
        self.assertEqual(shape(stream), ["conversation", "step:answer:running", "error"])
        self.assertEqual(
            only(stream, "error")[0],
            {"message": "The model is still loading. Give it a moment, then send again."},
        )

    def test_the_question_is_kept_even_though_nothing_answered_it(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("Why is the sky blue?", persona="explainer")
        self.assertEqual([m["role"] for m in self.conversation(stream)["messages"]], ["user"])

    def test_a_command_still_runs_its_tool_before_the_error(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             "step:answer:running", "error"],
        )
        self.assertEqual(len(self.conversation(stream)["orphan_tool_calls"]), 1)


class CancelTests(ChatCase):
    def test_stopping_a_request_nobody_is_running_says_so(self) -> None:
        res = self.client.post("/api/cancel/no-such-request")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"cancelled": False})


if __name__ == "__main__":
    unittest.main()
