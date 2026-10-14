"""One message through ``/api/chat``: what the stream says, in what order, what
the model is given to read, and what is left in the database afterwards.

Needs the app's own requirements (FastAPI, httpx for the test client); without
them the whole module is skipped. It needs no model and no network: the engine
plays back prepared text, and the tools that would go out are replaced.
"""

from __future__ import annotations

import unittest
from unittest import mock

try:
    from app import db
    from app.personas import get_persona
    from app.transcript import CUE_PLAIN, CUE_WITH_DATA, data, system_for
    from app.tools import base as tool_base
    from app.tools.base import ToolResult, available
    from evals.scripted import ScriptedEngine
    from tests import support
    from tests.support import ChatCase, PlayedEngine, only, shape, step
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

# The end of every stream that reaches an answer.
ANSWER = ["step:answer:running", "start", "token", "step:answer:ok", "done", "run"]

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


def agent_system() -> str:
    """What every pass of the Agent reads first, with the tools on offer now."""
    return system_for(get_persona("agent"), available())


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
        self.assertEqual(
            set(first[1]), {"id", "title", "created", "skill", "persona", "request_id"}
        )
        self.assertEqual(first[1]["title"], "Why is the sky blue?")
        self.assertIs(first[1]["created"], True)
        self.assertIsNone(first[1]["skill"])
        self.assertEqual(first[1]["persona"], "explainer")

    def test_the_first_event_also_names_the_run_so_it_can_be_stopped_from_the_start(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        run_id = stream[0][1]["request_id"]
        self.assertTrue(run_id)
        # The pass that writes the answer goes by the same name.
        self.assertEqual(only(stream, "start")[0]["request_id"], run_id)

    def test_every_message_is_a_run_of_its_own(self) -> None:
        first = self.chat("Why is the sky blue?", persona="explainer")
        cid = only(first, "conversation")[0]["id"]
        second = self.chat("And at sunset?", persona="explainer", conversation_id=cid)
        self.assertNotEqual(first[0][1]["request_id"], second[0][1]["request_id"])

    def test_once_a_stream_has_ended_its_name_stops_nothing(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        res = self.client.post(f"/api/cancel/{stream[0][1]['request_id']}")
        self.assertEqual(res.json(), {"cancelled": False})
        self.assertEqual(self.engine._cancels, {})

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
             "glyph": "✦", "ms": done["total_ms"],
             "tokens_in": done["prompt_tokens"], "tokens_out": done["completion_tokens"]},
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
            {"cancelled", "ttft_ms", "queue_wait_ms", "prompt_tokens", "read_tokens",
             "cached_tokens", "completion_tokens", "prefill_tps", "gen_tps", "total_ms", "rss_mb",
             "tool_calls"},
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

    def test_the_next_message_reads_the_earlier_result_as_it_was_sent(self) -> None:
        first = self.chat("/calc 2+2", persona="explainer")
        cid = only(first, "conversation")[0]["id"]
        self.chat("Why?", persona="explainer", conversation_id=cid)
        self.assertEqual(
            [m["content"] for m in self.engine.answer_prompts[1][1:]],
            [with_data("/calc 2+2", "calculator: 2+2 = 4"), "A short answer.", "Why?"],
        )
        # The first prompt, then its answer, then the new message.
        self.assertEqual(self.engine.answer_prompts[1][:2], self.engine.answer_prompts[0])

    def test_an_answer_with_nothing_in_it_is_not_stored_and_its_call_is_left_loose(self) -> None:
        self.use_engine(PlayedEngine(""))
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             "step:answer:running", "start", "step:answer:ok", "done", "run"],
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

    def test_a_pattern_match_costs_no_pass_and_none_is_announced(self) -> None:
        stream = self.chat("15% of 200", persona="agent")
        # Nothing is "being chosen": the pattern decided before anything was said.
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card",
             *ANSWER],
        )
        self.assertEqual(self.engine.route_prompts, [])
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "Calculator",
             "detail": "heuristic", "glyph": "⚡", "tool": "calculator", "ms": 0},
        )
        self.assertEqual(
            self.engine.answer_prompts[0][-1]["content"],
            with_data("15% of 200", "calculator: 15/100*200 = 30"),
        )
        # The Agent reads one system text, whoever chose the tool.
        self.assertEqual(self.engine.answer_prompts[0][0]["content"], agent_system())
        self.assertEqual(self.conversation(stream)["messages"][1]["tool_calls"][0]["via"], "heuristic")

    def test_with_no_pattern_the_model_is_asked_and_may_say_none(self) -> None:
        stream = self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(
            shape(stream), ["conversation", "step:route:running", "step:route:ok", *ANSWER]
        )
        # A pass is about to be made, so one is announced.
        self.assertEqual(
            step(stream, "route", "running"),
            {"id": "route", "kind": "route", "status": "running", "label": "Choosing a tool",
             "glyph": "◇", "pass": 1},
        )
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "No tool needed",
             "detail": "model", "glyph": "◇", "tool": None, "ms": support.ROUTE_MS, "pass": 1,
             "tokens_in": self.engine.prompt_tokens(self.engine.route_prompts[0]),
             "tokens_out": support.ROUTE_TOKENS},
        )
        # The answer is written on from where the choice left off.
        self.assertEqual(
            self.engine.answer_prompts[0],
            [
                {"role": "system", "content": agent_system()},
                {"role": "user", "content": "Suggest a name for my boat"},
                {"role": "assistant", "content": '{"tool": "none"}'},
                {"role": "user", "content": CUE_PLAIN},
            ],
        )

    def test_the_choice_reads_the_system_text_with_its_menu_and_the_message(self) -> None:
        self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(
            self.engine.route_prompts,
            [[
                {"role": "system", "content": agent_system()},
                {"role": "user", "content": "Suggest a name for my boat"},
            ]],
        )

    def test_a_long_message_is_read_whole_where_it_fits(self) -> None:
        long = "Suggest a name for my boat. " * 30
        self.chat(long, persona="agent")
        self.assertEqual(self.engine.route_prompts[0][-1]["content"], long.strip())

    def test_the_choice_reads_the_conversation_so_far(self) -> None:
        first = self.chat("Suggest a name for my boat", persona="agent")
        cid = only(first, "conversation")[0]["id"]
        self.chat("Something shorter", persona="agent", conversation_id=cid)
        self.assertEqual(
            [m["content"] for m in self.engine.route_prompts[1][1:]],
            ["Suggest a name for my boat", '{"tool": "none"}', CUE_PLAIN, "A short answer.",
             "Something shorter"],
        )

    def test_the_model_can_choose_a_tool_and_its_result_reaches_the_answer(self) -> None:
        self.use_engine(PlayedEngine(decision={"tool": "weather", "args": {"location": "Paris"}}))
        asked = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris", persona="agent")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:running", "step:route:ok", "step:tool-0:running",
             "step:tool-0:ok", "card", "step:route-2:running", "step:route-2:ok", *ANSWER],
        )
        self.assertEqual(
            step(stream, "route", "ok"),
            {"id": "route", "kind": "route", "status": "ok", "label": "Weather",
             "detail": "model", "glyph": "☁", "tool": "weather", "ms": support.ROUTE_MS,
             "pass": 1, "tokens_in": self.engine.prompt_tokens(self.engine.route_prompts[0]),
             "tokens_out": support.ROUTE_TOKENS},
        )
        self.assertEqual(asked["weather"], [{"location": "Paris"}])
        told = [m["content"] for m in self.engine.answer_prompts[0]]
        self.assertEqual(told[-3:], [data("weather", WEATHER.summary), '{"tool": "none"}', CUE_WITH_DATA])
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

    def test_a_command_nobody_defined_is_left_to_the_model_and_the_pass_is_shown(self) -> None:
        stream = self.chat("/foo bar", persona="agent")
        self.assertEqual(
            shape(stream), ["conversation", "step:route:running", "step:route:ok", *ANSWER]
        )
        self.assertEqual(len(self.engine.route_prompts), 1)
        self.assertEqual(step(stream, "route", "ok")["detail"], "model")

    def test_a_command_nobody_defined_is_only_text_for_a_persona_without_tools(self) -> None:
        stream = self.chat("/foo bar", persona="explainer")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.route_prompts, [])

    def test_with_every_tool_switched_off_the_model_is_not_asked_to_choose(self) -> None:
        with mock.patch.dict(tool_base.REGISTRY, clear=True):
            stream = self.chat("Suggest a name for my boat", persona="agent")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.route_prompts, [])

    def test_other_personas_never_reach_a_pattern_or_the_model(self) -> None:
        stream = self.chat("15% of 200", persona="explainer")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.route_prompts, [])


class RoutingThroughTheSlotTests(ChatCase):
    """The same choice, made by an engine whose routing pass is a real pass:
    queued, run on a worker thread, and timed."""

    def test_the_choice_and_the_answer_are_two_passes_through_one_slot(self) -> None:
        engine = ScriptedEngine([
            '{"tool": "weather", "args": {"location": "Paris"}}', '{"tool": "none"}', "Take a coat.",
        ])
        self.use_engine(engine)
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris", persona="agent")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:running", "step:route:ok", "step:tool-0:running",
             "step:tool-0:ok", "card", "step:route-2:running", "step:route-2:ok", *ANSWER],
        )
        routed = step(stream, "route", "ok")
        self.assertEqual((routed["label"], routed["detail"], routed["tool"]),
                         ("Weather", "model", "weather"))
        self.assertGreater(routed["ms"], 0)
        self.assertEqual(len(engine.passes), 3)
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)
        self.assertEqual(self.conversation(stream)["messages"][1]["content"], "Take a coat.")

    def test_one_engine_serves_message_after_message(self) -> None:
        engine = ScriptedEngine(['{"tool": "none"}', "First."] * 5)
        self.use_engine(engine)
        for _ in range(5):
            stream = self.chat("Suggest a name for my boat", persona="agent")
            self.assertEqual(only(stream, "done")[0]["completion_tokens"], 1)
            self.assertTrue(engine.free)
        self.assertEqual(len(engine.passes), 10)
        self.assertEqual(engine.info()["completed"], 10)


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
                {"role": "system", "content": agent_system()},
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
    """A tool that fails is shown failing, and the model is told it failed."""

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

    def test_the_model_is_told_what_happened_and_asked_to_say_so(self) -> None:
        self.chat("/calc 1/0", persona="explainer")
        self.assertEqual(
            self.engine.answer_prompts[0][-1]["content"],
            "/calc 1/0\n\n"
            "A lookup was tried for this and did not work:\n"
            "- calculator: That is not something this calculator handles.\n\n"
            "Say plainly what could not be looked up, and do not guess at it.",
        )

    def test_the_model_is_told_what_happened_and_not_what_to_try(self) -> None:
        # The card tells a person what to try next. A small model reading that
        # in a tool result takes it as an order, so it is given only the fact.
        self.use_engine(PlayedEngine(decision={"tool": "weather", "args": {"location": "Atlantis"}}))
        support.played_tools(self, {"weather": ToolResult(
            False, "No place called 'Atlantis' was found. Try adding a country, like “Amman, Jordan”.",
            error="no such place",
        )})
        stream = self.chat("Do I need a coat in Atlantis", persona="agent")
        told = "\n".join(m["content"] for m in self.engine.answer_prompts[0])
        self.assertIn(data("weather", "No place called 'Atlantis' was found.", ok=False), told)
        self.assertNotIn("Try adding", told)
        self.assertNotIn("Live data", told)
        # The person still gets the whole sentence.
        self.assertIn("Try adding a country", only(stream, "card")[0]["data"]["message"])

    def test_a_call_refused_before_it_ran_is_told_the_same_way(self) -> None:
        self.chat("/fx 5 xyz eur", persona="explainer")
        self.assertIn(
            "- currency: Currency does not know 'xyz' as a currency to convert from.",
            self.engine.answer_prompts[0][-1]["content"],
        )

    def test_the_failure_is_stored_without_a_card(self) -> None:
        stream = self.chat("/calc 1/0", persona="explainer")
        call = self.conversation(stream)["messages"][1]["tool_calls"][0]
        self.assertIs(call["ok"], False)
        self.assertIsNone(call["card"])
        self.assertEqual(call["summary"], CALC_REFUSAL)

    def test_a_skill_whose_tool_failed_is_told_what_happened_in_place_of_the_result(self) -> None:
        self.chat("1/0", skill="do_maths")
        told = self.engine.answer_prompts[0][-1]["content"]
        self.assertTrue(told.endswith(
            "This lookup did not work:\n- calculator: That is not something this calculator handles.\n\nSay plainly what could not be looked up, and do not guess at it."
        ))
        # "No output" is what a skill is told when it found nothing to look up.
        self.assertNotIn("(no tool output available)", told)

    def test_a_skill_with_one_lookup_that_worked_and_one_that_did_not_is_told_both(self) -> None:
        support.played_tools(self, {"wikipedia": [
            ToolResult(True, "Wikipedia — Python: a programming language.", data={"title": "Python"}),
            ToolResult(False, "Wikipedia has no article matching 'Rsut'.", error="not found"),
        ]})
        self.chat("Python vs Rsut", skill="compare")
        told = self.engine.answer_prompts[0][-1]["content"]
        self.assertTrue(told.endswith(
            "wikipedia: Wikipedia — Python: a programming language.\n\n"
            "This lookup did not work:\n"
            "- wikipedia: Wikipedia has no article matching 'Rsut'.\n\n"
            "Say plainly what could not be looked up, and do not guess at it."
        ))

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
        self.assertEqual(shape(stream), ["conversation", "step:answer:running", "error", "run"])
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
             "step:answer:running", "error", "run"],
        )
        self.assertEqual(len(self.conversation(stream)["orphan_tool_calls"]), 1)


class CancelTests(ChatCase):
    def test_stopping_a_request_nobody_is_running_says_so(self) -> None:
        res = self.client.post("/api/cancel/no-such-request")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"cancelled": False})


class StoredTraceTests(ChatCase):
    """What is left in the database about how each message was answered."""

    def rows(self, stream: list) -> list[dict]:
        return db.run_steps(only(stream, "conversation")[0]["request_id"])

    def test_every_message_stores_steps_in_order_that_sum_to_the_run_total(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        cases = (
            ("a plain answer", "Why is the sky blue?", {"persona": "explainer"}, None,
             ["answer"]),
            ("a command", "/calc 2+2", {"persona": "explainer"}, None,
             ["route", "tool", "answer"]),
            ("a pattern", "15% of 200", {"persona": "agent"}, None, ["route", "tool", "answer"]),
            ("the model declining", "Suggest a name for my boat", {"persona": "agent"},
             ['{"tool": "none"}', "Wren."], ["route", "answer"]),
            ("the model's choice", "Do I need a coat in Paris", {"persona": "agent"},
             ['{"tool": "weather", "args": {"location": "Paris"}}', '{"tool": "none"}',
              "Take a coat."],
             ["route", "tool", "decide", "answer"]),
            ("a skill", "2+2", {"skill": "do_maths"}, None, ["route", "tool", "answer"]),
            ("a failed tool", "/calc 1/0", {"persona": "explainer"}, None,
             ["route", "tool", "answer"]),
        )
        for name, message, how, script, kinds in cases:
            with self.subTest(name):
                # Where the model is asked to choose, the engine is one whose
                # routing pass really takes the time it reports.
                self.use_engine(ScriptedEngine(script) if script else PlayedEngine())
                stream = self.chat(message, **how)
                self.assertEqual(stream[-1][0], "run")
                totals = stream[-1][1]
                rows = self.rows(stream)
                self.assertEqual([r["kind"] for r in rows], kinds)
                self.assertEqual([r["idx"] for r in rows], list(range(len(rows))))
                self.assertEqual(totals["steps"], len(rows))
                self.assertEqual(totals["id"], only(stream, "conversation")[0]["request_id"])
                self.assertEqual(totals["outcome"], "answered")
                self.assertAlmostEqual(
                    sum(r["total_ms"] for r in rows), totals["total_ms"], delta=0.06
                )
                self.assertGreater(totals["wall_ms"], totals["total_ms"] - 1)

    def test_the_run_is_reported_last_with_what_it_cost_and_where_the_time_went(self) -> None:
        stream = self.chat("Why is the sky blue?", persona="explainer")
        self.assertEqual([name for name, _ in stream].count("run"), 1)
        self.assertEqual(
            set(stream[-1][1]),
            {"id", "outcome", "steps", "passes", "total_ms", "waiting_ms", "reading_ms",
             "writing_ms", "tools_ms", "wall_ms"},
        )
        self.assertEqual((stream[-1][1]["steps"], stream[-1][1]["passes"]), (1, 1))

    def test_the_steps_of_an_answered_run_hang_off_its_answer(self) -> None:
        stream = self.chat("/calc 2+2", persona="explainer")
        answer = self.conversation(stream)["messages"][1]
        rows = self.rows(stream)
        self.assertEqual({r["message_id"] for r in rows}, {answer["id"]})
        self.assertEqual({r["conversation_id"] for r in rows}, {self.conversation(stream)["id"]})

    def test_the_stored_step_of_a_pass_has_what_the_stream_reported(self) -> None:
        stream = self.chat("Suggest a name for my boat", persona="agent")
        route, answer = self.rows(stream)
        routed = step(stream, "route", "ok")
        self.assertEqual((route["via"], route["name"]), ("model", None))
        self.assertEqual(route["input"], {"text": "Suggest a name for my boat"})
        self.assertEqual(route["output_text"], '{"tool": "none"}')
        self.assertEqual(route["prompt_tokens"], routed["tokens_in"])
        self.assertEqual(route["output_tokens"], routed["tokens_out"])
        done = only(stream, "done")[0]
        self.assertEqual(answer["prompt_tokens"], done["prompt_tokens"])
        self.assertEqual(answer["output_tokens"], done["completion_tokens"])
        self.assertEqual(answer["first_token_ms"], done["ttft_ms"])
        self.assertIsNone(answer["new_tokens"])
        # The answer's own text is in `messages`, not copied here.
        self.assertIsNone(answer["output_text"])

    def test_a_run_that_failed_leaves_its_steps_tied_to_no_answer(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(stream[-1][1]["outcome"], "failed")
        rows = self.rows(stream)
        self.assertEqual([r["kind"] for r in rows], ["route", "tool", "answer"])
        self.assertEqual({r["message_id"] for r in rows}, {None})
        self.assertIs(rows[-1]["ok"], False)
        self.assertIn("still loading", rows[-1]["error"])

    def test_an_answer_with_nothing_in_it_leaves_steps_tied_to_no_answer(self) -> None:
        self.use_engine(PlayedEngine(""))
        stream = self.chat("Why is the sky blue?", persona="explainer")
        self.assertEqual({r["message_id"] for r in self.rows(stream)}, {None})

    def test_deleting_the_conversation_deletes_what_was_kept_of_its_runs(self) -> None:
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(len(self.rows(stream)), 3)
        self.client.delete(f"/api/conversations/{only(stream, 'conversation')[0]['id']}")
        self.assertEqual(self.rows(stream), [])

    def test_with_a_retention_of_zero_the_run_is_still_reported_and_nothing_is_kept(self) -> None:
        self.db_settings.steps_keep_days = 0
        stream = self.chat("/calc 2+2", persona="explainer")
        self.assertEqual(stream[-1][1]["steps"], 3)
        self.assertEqual(self.rows(stream), [])
        # The answer itself is kept as it always was.
        self.assertEqual(len(self.conversation(stream)["messages"]), 2)


class AskTests(ChatCase):
    """The model asks the visitor for what the message left out."""

    QUESTION = {"tool": "ask", "args": {"question": "Which city do you mean?"}}

    def test_the_question_is_the_reply_and_is_stored_as_one(self) -> None:
        self.use_engine(PlayedEngine(decision=self.QUESTION))
        stream = self.chat("what's the weather like", persona="agent")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:running", "step:route:ok", "token", "done", "run"],
        )
        self.assertEqual(only(stream, "token")[0]["t"], "Which city do you mean?")
        self.assertIs(only(stream, "done")[0]["asked"], True)
        self.assertEqual(self.engine.answer_prompts, [])  # no answer pass was made
        asked, answered = self.conversation(stream)["messages"]
        self.assertEqual(answered["role"], "assistant")
        self.assertEqual(answered["content"], "Which city do you mean?")
        self.assertIsNone(answered["gen_tps"])

    def test_a_question_is_not_a_sample_of_how_fast_answers_are_written(self) -> None:
        self.use_engine(PlayedEngine(decision=self.QUESTION))
        self.chat("what's the weather like", persona="agent")
        self.assertEqual(db.recent_runs("chat"), [])
        self.assertEqual(db.chat_stats()["n"], 0)

    def test_the_visitors_reply_is_read_after_the_question_they_were_asked(self) -> None:
        self.use_engine(PlayedEngine(decision=self.QUESTION))
        first = self.chat("what's the weather like", persona="agent")
        cid = only(first, "conversation")[0]["id"]

        self.use_engine(PlayedEngine(decision={"tool": "weather", "args": {"location": "Lisbon"}}))
        looked_up = support.played_tools(self, {"weather": WEATHER})
        self.chat("Lisbon", persona="agent", conversation_id=cid)
        # Read as the model wrote it: the question inside its own reply.
        self.assertEqual(
            [m["content"] for m in self.engine.route_prompts[0][1:]],
            ["what's the weather like",
             '{"tool": "ask", "args": {"question": "Which city do you mean?"}}', "Lisbon"],
        )
        self.assertEqual(looked_up["weather"], [{"location": "Lisbon"}])


class LoopOverHttpTests(ChatCase):
    """The loop, switched on by its setting, through the real app."""

    PARIS = '{"tool": "weather", "args": {"location": "Paris"}}'
    LISBON = '{"tool": "weather", "args": {"location": "Lisbon"}}'

    def setUp(self) -> None:
        super().setUp()
        support.with_settings(self, agent_loop=True)

    def test_two_lookups_and_an_answer_are_streamed_stored_and_added_up(self) -> None:
        engine = self.use_engine(ScriptedEngine([self.PARIS, self.LISBON, '{"tool": "none"}', "Lisbon."]))
        looked_up = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Is Paris or Lisbon warmer right now?", persona="agent")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:route:running", "step:route:ok", "step:tool-0:running",
             "step:tool-0:ok", "card", "step:route-2:running", "step:route-2:ok",
             "step:tool-1:running", "step:tool-1:ok", "card", "step:route-3:running",
             "step:route-3:ok", *ANSWER],
        )
        self.assertEqual(looked_up["weather"], [{"location": "Paris"}, {"location": "Lisbon"}])
        rows = db.run_steps(only(stream, "conversation")[0]["request_id"])
        self.assertEqual([r["kind"] for r in rows], ["route", "tool", "decide", "tool", "decide", "answer"])
        totals = stream[-1][1]
        self.assertEqual((totals["steps"], totals["passes"], totals["outcome"]), (6, 4, "answered"))
        self.assertAlmostEqual(sum(r["total_ms"] for r in rows), totals["total_ms"], delta=0.06)
        answered = self.conversation(stream)["messages"][1]
        self.assertEqual(answered["content"], "Lisbon.")
        self.assertEqual(len(answered["tool_calls"]), 2)
        self.assertEqual({r["message_id"] for r in rows}, {answered["id"]})
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 2)
        self.assertTrue(engine.free)

    def test_a_later_message_reads_the_conversation_and_one_engine_serves_them_all(self) -> None:
        engine = self.use_engine(ScriptedEngine(
            [self.PARIS, '{"tool": "none"}', "Overcast.", '{"tool": "none"}', "You are welcome."]
        ))
        support.played_tools(self, {"weather": WEATHER})
        first = self.chat("Do I need a coat in Paris", persona="agent")
        cid = only(first, "conversation")[0]["id"]
        self.chat("Thank you", persona="agent", conversation_id=cid)
        read = [m["content"] for m in engine.passes[3].messages[1:]]
        self.assertEqual(read, [
            "Do I need a coat in Paris", self.PARIS, data("weather", WEATHER.summary),
            '{"tool": "none"}', CUE_WITH_DATA, "Overcast.", "Thank you",
        ])
        self.assertTrue(engine.free)
        self.assertEqual(engine.most_at_once, 1)

    def test_a_command_and_a_plain_persona_are_untouched_by_the_setting(self) -> None:
        for message, persona, kinds in (("/calc 2+2", "agent", ["route", "tool", "answer"]),
                                        ("Why is the sky blue?", "explainer", ["answer"])):
            with self.subTest(message=message):
                self.use_engine(PlayedEngine())
                stream = self.chat(message, persona=persona)
                rows = db.run_steps(only(stream, "conversation")[0]["request_id"])
                self.assertEqual([r["kind"] for r in rows], kinds)
                self.assertEqual(self.engine.route_prompts, [])


class ReplayTests(ChatCase):
    """The next message is read with the conversation as it was sent."""

    PARIS = '{"tool": "weather", "args": {"location": "Paris"}}'
    NONE = '{"tool": "none"}'

    def two(self, engine: ScriptedEngine, first: str, then: str, **how) -> str:
        """Two messages of one conversation. Returns its id."""
        self.use_engine(engine)
        cid = only(self.chat(first, **how), "conversation")[0]["id"]
        self.chat(then, conversation_id=cid, **how)
        return cid

    def test_each_message_reads_on_from_where_the_last_one_ended(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        ask = '{"tool": "ask", "args": {"question": "Which city?"}}'
        agent_, plain = {"persona": "agent"}, {"persona": "explainer"}
        cases = (
            # (name, the loop, script, first message, then, how, the last pass
            # of the first message, the first pass of the second)
            ("a plain persona", True, ["One.", "Two."], "Hello", "And?", plain, 0, 1),
            ("a command", True, ["Four.", "Because."], "/calc 2+2", "Why?", plain, 0, 1),
            ("a pattern", True, ["Thirty.", "Because."], "15% of 200", "Why?", agent_, 0, 1),
            ("the loop", True, [self.PARIS, self.NONE, "Overcast.", self.NONE, "Welcome."],
             "Do I need a coat in Paris", "Thanks", agent_, 2, 3),
            ("one choice", False, [self.PARIS, "Overcast.", self.NONE, "Welcome."],
             "Do I need a coat in Paris", "Thanks", agent_, 1, 2),
            ("a question asked back", True, [ask, self.PARIS, self.NONE, "Overcast."],
             "what is the weather like", "Paris", agent_, 0, 1),
            ("a question asked back, one choice", False, [ask, self.PARIS, "Overcast."],
             "what is the weather like", "Paris", agent_, 0, 1),
        )
        for name, loop, script, first, then, how, last, next_ in cases:
            with self.subTest(name):
                support.with_settings(self, agent_loop=loop)
                engine = ScriptedEngine(script)
                self.two(engine, first, then, **how)
                self.assertEqual(len(engine.passes), len(script), "every pass as scripted")
                before, after = engine.passes[last], engine.passes[next_]
                self.assertTrue(after.prompt.startswith(before.prompt + before.output))

    def test_what_was_sent_is_kept_with_the_conversation_and_goes_with_it(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        cid = self.two(ScriptedEngine([self.PARIS, self.NONE, "Overcast.", self.NONE, "Welcome."]),
                       "Do I need a coat in Paris", "Thanks", persona="agent")
        messages, records = db.conversation_record(cid)
        self.assertEqual(len(records), 2)
        first = records[0]
        self.assertEqual((first["question_id"], first["answer_id"]), (messages[0]["id"], messages[1]["id"]))
        self.assertEqual([p["kind"] for p in first["parts"]],
                         ["question", "action", "observation", "action", "cue", "answer"])
        self.assertEqual(first["parts"][2]["tools"], ["weather"])
        # Reloading the conversation shows what it always showed.
        convo = self.client.get(f"/api/conversations/{cid}").json()
        self.assertEqual([m["content"] for m in convo["messages"]],
                         ["Do I need a coat in Paris", "Overcast.", "Thanks", "Welcome."])
        self.client.delete(f"/api/conversations/{cid}")
        self.assertEqual(db.conversation_record(cid), ([], []))
        # And it is not cleared out with the steps.
        cid = self.two(ScriptedEngine(["One.", "Two."]), "Hello", "And?", persona="explainer")
        db.prune_steps(keep_days=0)
        self.assertEqual(len(db.conversation_record(cid)[1]), 2)

    def test_a_conversation_kept_before_this_is_read_as_it_always_was(self) -> None:
        cid = db.create_conversation("agent", "Hello")
        db.add_message(cid, "user", "Hello")
        db.add_message(cid, "assistant", "Hi there.")
        engine = self.use_engine(ScriptedEngine([self.NONE, "Fine."]))
        self.chat("How are you?", persona="agent", conversation_id=cid)
        self.assertEqual([m["content"] for m in engine.passes[0].messages[1:]],
                         ["Hello", "Hi there.", "How are you?"])



if __name__ == "__main__":
    unittest.main()
