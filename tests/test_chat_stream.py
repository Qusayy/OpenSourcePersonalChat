"""One message through ``/api/chat``: what the stream says, in what order, what
the model is given to read, and what is left in the database afterwards.

The model decides every message. It replies; a reply answers, or ends in a
call to a tool, written in the model's own format, and then the harness makes
the call, puts what came of it in the conversation, and the model replies
again. A slash command is the user's own call, put in the conversation as if
the model had made it, and one reply answers it.

Needs the app's own requirements (FastAPI, httpx for the test client); without
them the whole module is skipped. It needs no model and no network: the engine
plays back prepared text, and the tools that would go out are replaced.
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    import httpx

    from app import main
    from app.agent import ALREADY_MADE
    from app.agent.calls import CALL_CLOSE, CALL_OPEN, render_call, response
    from app.agent.context import SUMMARY_HEADING
    from app.agent.loop import NO_ANSWER
    from app.agent.reply import CODE_OPENERS
    from app.agent.style import PROSE, PROSE_THINKING
    from app.agent.transcript import NO_MORE_CALLS, SYSTEM_WITHOUT_TOOLS, system_for
    from app.api import deps
    from app.chatml import THINK_BLOCK, THINK_CLOSE
    from app.config import settings
    from app.store import conversations, runs
    from app.tools import base as tool_base
    from app.tools.base import ToolResult, available
    from evals.scripted import ScriptedEngine
    from tests import support
    from tests.support import ChatCase, PlayedEngine, events, only, shape, step
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

# The end of every stream whose first reply answers.
ANSWER = ["step:reply-1:running", "start", "token", "step:reply-1:ok", "done", "run"]
# A call that was made and worked: its step, and its card.
LOOKUP = ["step:tool-0:running", "step:tool-0:ok", "card"]
# A reply that called a tool: it says nothing, and the call is not shown.
CALLED = ["step:reply-1:running", "start", "step:reply-1:ok"]
# The reply after a call, which answers.
THEN = ["step:reply-2:running", "start", "token", "step:reply-2:ok", "done", "run"]

# Where a reply stops of its own accord: at the end of a call, and where a
# call begins a parameter that holds code. The last reply a run may make
# stops where a call would begin.
STOPS = [CALL_CLOSE, *CODE_OPENERS]
LAST_STOPS = [*STOPS, CALL_OPEN]

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
# The model asking for the weather in Paris: as PlayedEngine is told it, and
# as the model writes it.
WEATHER_IN_PARIS = {"tool": "weather", "args": {"location": "Paris"}}
PARIS = render_call("weather", {"location": "Paris"})
LISBON = render_call("weather", {"location": "Lisbon"})


def agent_system() -> str:
    """What every reply reads first, with what is on offer now."""
    return system_for(available())


def said(messages: list[dict]) -> list[str]:
    """A prompt after its system text, turn by turn, without the lines that
    say when each message was sent."""
    return [m["content"] for m in support.unsent(messages[1:])]


def cfg(**values):
    """An engine's settings: no model, no delay, and what is given."""
    return settings.model_copy(update={"mock": True, "mock_delay": 0.0, **values})


def room(engine, messages: list[dict]) -> int:
    """What a reply to ``messages`` may write: the rest of the window."""
    return engine.cfg.n_ctx - engine.prompt_tokens(messages) - engine.cfg.reply_headroom


def reads_on(engine, before, after) -> bool:
    """Whether a pass's prompt begins with an earlier pass's prompt and what
    it wrote, to the character: all the model server has to read is new."""
    return engine.render(after.messages, after.prefix).startswith(
        engine.render(before.messages, before.prefix) + before.output
    )


class Clock:
    """``datetime`` as the run reads the time, stopped at given moments: the
    first call gets the first, and so on, the last one standing."""

    def __init__(self, *moments: datetime) -> None:
        self.moments = list(moments)

    def now(self, tz=None) -> datetime:
        moment = self.moments.pop(0) if len(self.moments) > 1 else self.moments[0]
        return moment.astimezone(tz)


def stopped_at(case: unittest.TestCase, *moments: datetime) -> None:
    patcher = mock.patch("app.agent.loop.datetime", Clock(*moments))
    patcher.start()
    case.addCleanup(patcher.stop)


JAN_5 = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)


class PlainAnswerTests(ChatCase):
    """A site with its tools off: every message is one reply, as it was said."""

    def setUp(self) -> None:
        super().setUp()
        support.with_settings(self, tools_enabled=False)

    def test_a_plain_message_is_answered_in_one_reply(self) -> None:
        stream = self.chat("Why is the sky blue?")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(
            [support.unsent(p) for p in self.engine.prompts],
            [[
                {"role": "system", "content": SYSTEM_WITHOUT_TOOLS},
                {"role": "user", "content": "Why is the sky blue?"},
            ]],
        )

    def test_the_reply_is_sampled_as_prose_in_the_room_the_window_has_left(self) -> None:
        self.chat("Why is the sky blue?")
        (sampling,) = self.engine.samplings
        self.assertEqual({k: v for k, v in sampling.items() if k != "max_tokens"}, PROSE)
        self.assertEqual(sampling["max_tokens"], room(self.engine, self.engine.prompts[0]))
        # With nothing to call, it is the last reply there is: a call it
        # begins ends it.
        self.assertEqual(self.engine.stops, [LAST_STOPS])

    def test_the_first_event_names_the_conversation(self) -> None:
        stream = self.chat("Why is the sky blue?")
        first = stream[0]
        self.assertEqual(first[0], "conversation")
        self.assertEqual(set(first[1]), {"id", "title", "created", "request_id"})
        self.assertEqual(first[1]["title"], "Why is the sky blue?")
        self.assertIs(first[1]["created"], True)

    def test_the_first_event_also_names_the_run_so_it_can_be_stopped_from_the_start(self) -> None:
        stream = self.chat("Why is the sky blue?")
        run_id = stream[0][1]["request_id"]
        self.assertTrue(run_id)
        # The reply is written under the same name, and the run ends under it.
        self.assertEqual(only(stream, "start")[0]["request_id"], run_id)
        self.assertEqual(only(stream, "run")[0]["id"], run_id)

    def test_every_message_is_a_run_of_its_own(self) -> None:
        first = self.chat("Why is the sky blue?")
        cid = only(first, "conversation")[0]["id"]
        second = self.chat("And at sunset?", conversation_id=cid)
        self.assertNotEqual(first[0][1]["request_id"], second[0][1]["request_id"])

    def test_the_model_step_opens_and_closes_around_the_writing(self) -> None:
        stream = self.chat("Why is the sky blue?")
        done = only(stream, "done")[0]
        self.assertEqual(
            step(stream, "reply-1", "running"),
            {"id": "reply-1", "kind": "model", "status": "running", "label": "Writing",
             "glyph": "✦"},
        )
        self.assertEqual(
            step(stream, "reply-1", "ok"),
            {"id": "reply-1", "kind": "model", "status": "ok", "label": "Answer", "glyph": "✦",
             "ms": done["total_ms"], "tokens_in": done["prompt_tokens"],
             "tokens_out": done["completion_tokens"], "think_tokens": 0},
        )

    def test_start_says_which_request_this_is_and_how_full_the_window_is(self) -> None:
        stream = self.chat("Why is the sky blue?")
        start = only(stream, "start")[0]
        self.assertEqual(set(start), {"request_id", "queue_wait_ms", "prompt_tokens", "n_ctx"})
        self.assertEqual(start["n_ctx"], self.engine.cfg.n_ctx)
        self.assertEqual(start["prompt_tokens"], self.engine.prompt_tokens(self.engine.prompts[0]))

    def test_the_words_arrive_as_tokens_and_the_first_carries_its_wait(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        stream = self.chat("Why is the sky blue?")
        tokens = only(stream, "token")
        self.assertEqual("".join(t["t"] for t in tokens), "Light scatters off air.")
        self.assertIn("ttft_ms", tokens[0])
        for later in tokens[1:]:
            self.assertEqual(set(later), {"t"})

    def test_done_carries_the_figures_and_not_the_text(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        stream = self.chat("Why is the sky blue?")
        done = only(stream, "done")[0]
        self.assertEqual(
            set(done),
            {"cancelled", "ttft_ms", "queue_wait_ms", "prompt_tokens", "read_tokens",
             "cached_tokens", "completion_tokens", "think_tokens", "prefill_tps", "gen_tps",
             "total_ms", "rss_mb", "requests", "tool_calls", "unsupported", "correction"},
        )
        self.assertIs(done["cancelled"], False)
        self.assertEqual(done["completion_tokens"], 4)
        self.assertEqual((done["tool_calls"], done["think_tokens"], done["requests"]), (0, 0, 1))
        self.assertEqual((done["unsupported"], done["correction"]), ([], None))
        self.assertEqual(done["prompt_tokens"], only(stream, "start")[0]["prompt_tokens"])

    def test_the_question_and_the_answer_are_stored_with_the_figures(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        stream = self.chat("Why is the sky blue?")
        done = only(stream, "done")[0]
        convo = self.conversation(stream)
        self.assertEqual(convo["persona"], "agent")
        self.assertEqual([m["role"] for m in convo["messages"]], ["user", "assistant"])
        asked, answered = convo["messages"]
        self.assertEqual(asked["content"], "Why is the sky blue?")
        self.assertEqual(answered["content"], "Light scatters off air.")
        for figure in ("ttft_ms", "gen_tps", "prompt_tokens", "completion_tokens"):
            with self.subTest(figure=figure):
                self.assertEqual(answered[figure], done[figure])
        self.assertEqual(answered["tool_calls"], [])

    def test_an_answer_is_one_row_of_throughput_history(self) -> None:
        stream = self.chat("Why is the sky blue?")
        done = only(stream, "done")[0]
        rows = runs.recent_runs("chat")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "answer")
        self.assertEqual(rows[0]["gen_tokens"], done["completion_tokens"])
        self.assertEqual(rows[0]["prefill_tps"], done["prefill_tps"])

    def test_a_second_message_is_read_with_the_first_exchange_before_it(self) -> None:
        self.use_engine(PlayedEngine("Light scatters off air."))
        first = self.chat("Why is the sky blue?")
        cid = only(first, "conversation")[0]["id"]
        second = self.chat("And at sunset?", conversation_id=cid)
        self.assertEqual(only(second, "conversation")[0]["id"], cid)
        self.assertIs(only(second, "conversation")[0]["created"], False)
        self.assertEqual(
            support.unsent(self.engine.prompts[1]),
            [
                {"role": "system", "content": SYSTEM_WITHOUT_TOOLS},
                {"role": "user", "content": "Why is the sky blue?"},
                {"role": "assistant", "content": "Light scatters off air."},
                {"role": "user", "content": "And at sunset?"},
            ],
        )
        # Read on from the first prompt and its answer, to the letter.
        self.assertEqual(self.engine.prompts[1][:2], self.engine.prompts[0])
        self.assertEqual(len(self.conversation(second)["messages"]), 4)

    def test_a_conversation_id_nobody_has_starts_a_new_conversation(self) -> None:
        stream = self.chat("Hello", conversation_id="no-such-conversation")
        first = only(stream, "conversation")[0]
        self.assertIs(first["created"], True)
        self.assertNotEqual(first["id"], "no-such-conversation")

    def test_what_an_older_page_sends_besides_the_message_is_ignored(self) -> None:
        stream = self.chat("Hello", persona="no-such-persona", skill="do_maths")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(said(self.engine.prompts[0]), ["Hello"])

    def test_an_empty_message_is_refused_before_anything_is_stored(self) -> None:
        res = self.client.post("/api/chat", json={"message": ""})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(conversations.listing(), [])


class ModelDecidesTests(ChatCase):
    """The model reads the menu and the message, and calls a tool or not."""

    def test_a_message_the_model_answers_itself_is_one_reply(self) -> None:
        stream = self.chat("Suggest a name for my boat")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(
            support.unsent(self.engine.prompts[0]),
            [
                {"role": "system", "content": agent_system()},
                {"role": "user", "content": "Suggest a name for my boat"},
            ],
        )
        # It may call: only the end of a call, or the start of code, stops it.
        self.assertEqual(self.engine.stops, [STOPS])

    def test_a_call_the_model_makes_is_made_and_its_result_read_before_it_answers(self) -> None:
        self.use_engine(PlayedEngine("Take a coat.", WEATHER_IN_PARIS))
        asked = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        self.assertEqual(shape(stream), ["conversation", *CALLED, *LOOKUP, *THEN])
        self.assertEqual(asked["weather"], [{"location": "Paris"}])
        self.assertEqual(
            support.unsent(self.engine.prompts[1]),
            [
                {"role": "system", "content": agent_system()},
                {"role": "user", "content": "Do I need a coat in Paris"},
                {"role": "assistant", "content": PARIS},
                {"role": "user", "content": response(WEATHER.summary)},
            ],
        )
        # The answer reads on from the reply that called, to the letter.
        self.assertEqual(self.engine.prompts[1][:2], self.engine.prompts[0])

    def test_the_reply_that_called_says_which_tool_it_called(self) -> None:
        self.use_engine(PlayedEngine(decision=WEATHER_IN_PARIS))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        called = step(stream, "reply-1", "ok")
        self.assertEqual(called["label"], "Called Weather")
        self.assertEqual(called["tokens_in"], self.engine.prompt_tokens(self.engine.prompts[0]))
        self.assertEqual(step(stream, "reply-2", "ok")["label"], "Answer")

    def test_the_call_itself_is_never_shown(self) -> None:
        self.use_engine(PlayedEngine("Take a coat.", WEATHER_IN_PARIS))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        self.assertEqual("".join(t["t"] for t in only(stream, "token")), "Take a coat.")
        self.assertEqual(self.conversation(stream)["messages"][1]["content"], "Take a coat.")

    def test_what_the_model_says_before_a_call_is_shown_and_kept(self) -> None:
        self.use_engine(ScriptedEngine(["Let me check.\n" + PARIS, "Take a coat."]))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:reply-1:running", "start", "token", "step:reply-1:ok",
             *LOOKUP, *THEN],
        )
        self.assertEqual("".join(t["t"] for t in only(stream, "token")), "Let me check.\nTake a coat.")
        # Every reply's words are the answer, each said once.
        self.assertEqual(self.conversation(stream)["messages"][1]["content"],
                         "Let me check.\n\nTake a coat.")

    def test_the_call_is_stored_as_the_model_made_it(self) -> None:
        self.use_engine(PlayedEngine(decision=WEATHER_IN_PARIS))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        convo = self.conversation(stream)
        self.assertEqual(convo["orphan_tool_calls"], [])
        (call,) = convo["messages"][1]["tool_calls"]
        self.assertEqual((call["tool"], call["via"], call["args"]),
                         ("weather", "model", {"location": "Paris"}))
        self.assertEqual((call["card"], call["summary"], call["ok"]),
                         ("weather", WEATHER.summary, True))
        self.assertEqual(call["data"], WEATHER.data)
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)

    def test_a_call_to_a_tool_there_is_not_is_answered_with_an_error(self) -> None:
        self.use_engine(PlayedEngine(decision={"tool": "telescope", "args": {}}))
        stream = self.chat("Suggest a name for my boat")
        # It was never begun, so it only fails.
        self.assertEqual(
            shape(stream), ["conversation", *CALLED, "step:tool-0:failed", "card", *THEN]
        )
        self.assertEqual(step(stream, "reply-1", "ok")["label"], "Called a tool")
        self.assertEqual(
            step(stream, "tool-0", "failed"),
            {"id": "tool-0", "kind": "tool", "status": "failed", "label": "Tool call",
             "glyph": "◆", "tool": "telescope", "ms": 0.0, "detail": "unknown tool"},
        )
        self.assertEqual(
            only(stream, "card")[0],
            {"step_id": "tool-0", "card": "error", "tool": "telescope",
             "data": {"message": "There is no tool called 'telescope'."}},
        )
        self.assertEqual(
            self.engine.prompts[1][-1]["content"],
            response("Error: There is no tool called 'telescope'."),
        )

    def test_a_call_that_names_no_function_is_answered_with_an_error(self) -> None:
        self.use_engine(PlayedEngine(decision="<tool_call>\nweather Paris\n</tool_call>"))
        stream = self.chat("Suggest a name for my boat")
        self.assertEqual(
            shape(stream), ["conversation", *CALLED, "step:tool-0:failed", "card", *THEN]
        )
        self.assertEqual(step(stream, "tool-0", "failed")["detail"], "bad call")
        self.assertEqual(
            only(stream, "card")[0]["data"]["message"],
            "The tool call did not name a function, so no tool was called.",
        )
        self.assertTrue(self.engine.prompts[1][-1]["content"].startswith(
            "<tool_response>\nError: The tool call did not name a function"
        ))

    def test_the_model_is_told_in_whole_words_what_was_wrong_with_its_call(self) -> None:
        # What the model reads of a response has every look-alike of the
        # format's tags taken out (calls.clean), so the reason names none.
        self.use_engine(PlayedEngine(decision="<tool_call>\nweather Paris\n</tool_call>"))
        stream = self.chat("Suggest a name for my boat")
        told = only(stream, "card")[0]["data"]["message"]
        self.assertEqual(self.engine.prompts[1][-1]["content"],
                         f"<tool_response>\nError: {told}\n</tool_response>")

    def test_a_call_whose_closing_tags_the_reply_never_wrote_is_still_read(self) -> None:
        self.use_engine(PlayedEngine(
            decision="<tool_call>\n<function=wikipedia>\n<parameter=query>\nthe long"
        ))
        article = ToolResult(True, "An article.", card="wikipedia")
        asked = support.played_tools(self, {"wikipedia": article})
        stream = self.chat("Tell me about the long march")
        self.assertEqual(shape(stream), ["conversation", *CALLED, *LOOKUP, *THEN])
        self.assertEqual(asked["wikipedia"], [{"query": "the long"}])

    def test_a_command_nobody_defined_is_left_to_the_model(self) -> None:
        stream = self.chat("/foo bar")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(said(self.engine.prompts[0]), ["/foo bar"])
        self.assertEqual(self.engine.stops, [STOPS])

    def test_with_every_tool_switched_off_the_model_is_shown_no_menu(self) -> None:
        with mock.patch.dict(tool_base.REGISTRY, clear=True):
            stream = self.chat("Suggest a name for my boat")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(self.engine.prompts[0][0]["content"], SYSTEM_WITHOUT_TOOLS)
        self.assertEqual(self.engine.stops, [LAST_STOPS])

    def test_a_long_message_is_read_whole_where_it_fits(self) -> None:
        long = "Suggest a name for my boat. " * 30
        stream = self.chat(long)
        self.assertEqual(said(self.engine.prompts[0]), [long.strip()])
        self.assertEqual(only(stream, "trimmed"), [])


class SlashCommandTests(ChatCase):
    """A command names its tool. It is made at once, as if the model had made
    it, and one reply answers it."""

    def test_a_command_runs_its_tool_before_the_model_writes_a_word(self) -> None:
        stream = self.chat("/calc 2+2")
        self.assertEqual(shape(stream), ["conversation", *LOOKUP, *ANSWER])
        self.assertEqual(len(self.engine.prompts), 1)
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)

    def test_the_tool_step_says_what_it_was_given_and_how_long_it_took(self) -> None:
        stream = self.chat("/calc 2+2")
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
        stream = self.chat("/calc 2+2")
        card = only(stream, "card")[0]
        self.assertEqual(set(card), {"step_id", "card", "tool", "data"})
        self.assertEqual((card["step_id"], card["card"], card["tool"]),
                         ("tool-0", "calculator", "calculator"))
        self.assertEqual((card["data"]["expression"], card["data"]["result"]), ("2+2", "4"))

    def test_the_call_is_put_in_the_conversation_as_if_the_model_had_made_it(self) -> None:
        self.chat("/calc 2+2")
        self.assertEqual(
            support.unsent(self.engine.prompts[0]),
            [
                {"role": "system", "content": agent_system()},
                {"role": "user", "content": "/calc 2+2"},
                {"role": "assistant", "content": render_call("calculator", {"expression": "2+2"})},
                {"role": "user", "content": response("2+2 = 4")},
            ],
        )

    def test_where_the_model_has_a_thinking_block_the_call_comes_with_an_empty_one(self) -> None:
        self.use_engine(PlayedEngine(cfg=cfg(thinking="off")))
        self.chat("/calc 2+2")
        self.assertEqual(
            self.engine.prompts[0][2],
            {"role": "assistant",
             "content": THINK_BLOCK + render_call("calculator", {"expression": "2+2"})},
        )

    def test_the_stored_question_is_the_bare_one_and_the_call_hangs_off_the_answer(self) -> None:
        stream = self.chat("/calc 2+2")
        convo = self.conversation(stream)
        asked, answered = convo["messages"]
        self.assertEqual(asked["content"], "/calc 2+2")
        self.assertEqual(convo["orphan_tool_calls"], [])
        (call,) = answered["tool_calls"]
        self.assertEqual((call["tool"], call["via"], call["args"]),
                         ("calculator", "slash", {"expression": "2+2"}))
        self.assertEqual((call["card"], call["summary"], call["ok"]), ("calculator", "2+2 = 4", True))
        self.assertEqual(call["data"], only(stream, "card")[0]["data"])

    def test_the_next_message_reads_the_earlier_call_as_it_was_sent(self) -> None:
        first = self.chat("/calc 2+2")
        cid = only(first, "conversation")[0]["id"]
        self.chat("Why?", conversation_id=cid)
        self.assertEqual(
            said(self.engine.prompts[1]),
            ["/calc 2+2", render_call("calculator", {"expression": "2+2"}), response("2+2 = 4"),
             "A short answer.", "Why?"],
        )
        # The first prompt, then its answer, then the new message.
        self.assertEqual(self.engine.prompts[1][:4], self.engine.prompts[0])

    def test_an_answer_with_nothing_in_it_is_stored_as_saying_so(self) -> None:
        self.use_engine(PlayedEngine(""))
        stream = self.chat("/calc 2+2")
        self.assertEqual(
            shape(stream),
            ["conversation", *LOOKUP, "step:reply-1:running", "start", "step:reply-1:ok", "done",
             "run"],
        )
        self.assertEqual(only(stream, "done")[0]["completion_tokens"], 0)
        convo = self.conversation(stream)
        self.assertEqual([m["content"] for m in convo["messages"]], ["/calc 2+2", NO_ANSWER])
        self.assertEqual(len(convo["messages"][1]["tool_calls"]), 1)
        self.assertEqual(convo["orphan_tool_calls"], [])

    def test_with_tools_switched_off_a_command_is_only_text(self) -> None:
        support.with_settings(self, tools_enabled=False)
        stream = self.chat("/calc 2+2")
        self.assertEqual(shape(stream), ["conversation", *ANSWER])
        self.assertEqual(said(self.engine.prompts[0]), ["/calc 2+2"])


class FailedToolTests(ChatCase):
    """A tool that fails is shown failing, and the model reads that it failed."""

    def test_a_failed_tool_is_shown_with_its_reason(self) -> None:
        stream = self.chat("/calc 1/0")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:tool-0:running", "step:tool-0:failed", "card", *ANSWER],
        )
        self.assertEqual(step(stream, "tool-0", "failed")["detail"], "division by zero")
        self.assertEqual(
            only(stream, "card")[0],
            {"step_id": "tool-0", "card": "error", "tool": "calculator",
             "data": {"message": CALC_REFUSAL}},
        )
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)

    def test_a_tool_of_this_machine_is_read_failing_in_its_own_words(self) -> None:
        # What it says to do about it is meant for the model, and is kept.
        self.chat("/calc 1/0")
        self.assertEqual(self.engine.prompts[0][-1]["content"], response("Error: " + CALC_REFUSAL))

    def test_a_tool_that_goes_out_is_read_failing_with_what_happened_and_not_what_to_try(self) -> None:
        # The card tells a person what to try next. A small model reading that
        # in a tool result takes it as an order, so it is given only the fact.
        self.use_engine(PlayedEngine(decision={"tool": "weather", "args": {"location": "Atlantis"}}))
        support.played_tools(self, {"weather": ToolResult(
            False,
            "No place called 'Atlantis' was found. Try adding a country, like “Amman, Jordan”.",
            error="no such place",
        )})
        stream = self.chat("Do I need a coat in Atlantis")
        self.assertEqual(
            self.engine.prompts[1][-1]["content"],
            response("Error: No place called 'Atlantis' was found."),
        )
        # The person still gets the whole sentence.
        self.assertIn("Try adding a country", only(stream, "card")[0]["data"]["message"])

    def test_a_call_refused_before_it_ran_is_read_the_same_way(self) -> None:
        stream = self.chat("/fx 5 xyz eur")
        self.assertEqual(step(stream, "tool-0", "failed")["detail"], "bad input")
        self.assertEqual(
            self.engine.prompts[0][-1]["content"],
            response("Error: Currency does not know 'xyz' as a currency to convert from."),
        )

    def test_the_failure_is_stored_without_a_card(self) -> None:
        stream = self.chat("/calc 1/0")
        call = self.conversation(stream)["messages"][1]["tool_calls"][0]
        self.assertIs(call["ok"], False)
        self.assertIsNone(call["card"])
        self.assertEqual(call["summary"], CALC_REFUSAL)

    def test_a_tool_that_goes_out_is_refused_when_outgoing_requests_are_off(self) -> None:
        support.with_settings(self, allow_outbound=False)
        stream = self.chat("/weather Paris")
        failed = step(stream, "tool-0", "failed")
        self.assertEqual(failed["detail"], "offline mode")
        self.assertEqual(failed["ms"], 0)
        self.assertIn("outgoing requests switched off", only(stream, "card")[0]["data"]["message"])
        # And the model is not offered it either.
        self.assertNotIn('"name": "weather"', self.engine.prompts[0][0]["content"])


class NotReadyTests(ChatCase):
    """The model is not there yet: the stream says so and ends."""

    def test_a_model_still_loading_ends_the_stream_with_an_error_and_no_done(self) -> None:
        support.with_settings(self, tools_enabled=False)
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("Why is the sky blue?")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:reply-1:running", "error", "step:reply-1:failed", "run"],
        )
        self.assertEqual(
            only(stream, "error")[0],
            {"message": "The model is still loading. Give it a moment, then send again."},
        )
        self.assertEqual(only(stream, "run")[0]["outcome"], "failed")

    def test_the_question_is_kept_even_though_nothing_answered_it(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("Why is the sky blue?")
        self.assertEqual([m["role"] for m in self.conversation(stream)["messages"]], ["user"])
        self.assertEqual(conversations.record(stream[0][1]["id"])[1], [])

    def test_a_command_still_runs_its_tool_before_the_error(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("/calc 2+2")
        self.assertEqual(
            shape(stream),
            ["conversation", *LOOKUP, "step:reply-1:running", "error", "step:reply-1:failed", "run"],
        )
        self.assertEqual(len(self.conversation(stream)["orphan_tool_calls"]), 1)


class Stopping(PlayedEngine):
    """An engine whose reader presses Stop once two words are out."""

    def _iter_chunks(self, messages, sampling, **kwargs):
        for index, piece in enumerate(super()._iter_chunks(messages, sampling, **kwargs)):
            yield piece
            if index == 1:
                for run_id in list(self._cancels):
                    self.cancel(run_id)


class StopTests(ChatCase):
    """Stop, pressed while a run is going, and after it has gone."""

    def test_stopping_a_request_nobody_is_running_says_so(self) -> None:
        res = self.client.post("/api/cancel/no-such-request")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"cancelled": False})

    def test_once_a_stream_has_ended_its_name_stops_nothing(self) -> None:
        stream = self.chat("Why is the sky blue?")
        res = self.client.post(f"/api/cancel/{stream[0][1]['request_id']}")
        self.assertEqual(res.json(), {"cancelled": False})
        self.assertEqual(self.engine._cancels, {})

    def test_a_reply_stopped_part_way_ends_the_run_and_keeps_what_was_written(self) -> None:
        self.use_engine(Stopping("Light scatters off the air."))
        stream = self.chat("Why is the sky blue?")
        self.assertEqual(
            shape(stream),
            ["conversation", "step:reply-1:running", "start", "token", "step:reply-1:stopped",
             "done", "run"],
        )
        self.assertEqual("".join(t["t"] for t in only(stream, "token")), "Light scatters")
        self.assertIs(only(stream, "done")[0]["cancelled"], True)
        self.assertEqual(only(stream, "run")[0]["outcome"], "stopped")
        convo = self.conversation(stream)
        self.assertEqual([m["content"] for m in convo["messages"]],
                         ["Why is the sky blue?", "Light scatters"])
        # What a stopped run sent is not replayed: the next message reads the
        # two messages.
        self.assertEqual(conversations.record(convo["id"])[1], [])
        (row,) = runs.run_steps(stream[0][1]["request_id"])
        self.assertEqual((row["kind"], row["ok"], row["error"]), ("answer", False, "stopped"))

    def test_stop_while_a_tool_is_out_ends_the_run_without_waiting_for_it(self) -> None:
        self.use_engine(PlayedEngine(decision=WEATHER_IN_PARIS))
        engine = self.engine

        async def slow(**kwargs) -> ToolResult:
            for run_id in list(engine._cancels):
                engine.cancel(run_id)
            await asyncio.sleep(30)
            return WEATHER

        tool = dataclasses.replace(tool_base.REGISTRY["weather"], run=slow)
        with mock.patch.dict(tool_base.REGISTRY, {"weather": tool}):
            stream = self.chat("Do I need a coat in Paris")
        self.assertEqual(
            shape(stream),
            ["conversation", *CALLED, "step:tool-0:running", "step:tool-0:stopped", "done", "run"],
        )
        self.assertEqual(len(engine.prompts), 1)
        self.assertEqual(only(stream, "run")[0]["outcome"], "stopped")
        self.assertEqual([m["role"] for m in self.conversation(stream)["messages"]], ["user"])


class QueueTests(unittest.IsolatedAsyncioTestCase):
    """Two messages at once: one is written, and the other waits its turn and
    is told where it stands."""

    def setUp(self) -> None:
        support.temp_database(self)
        support.no_network(self)
        support.with_settings(self, tools_enabled=True, allow_outbound=True)
        # The first message's reply is held inside the model until released.
        self.engine = ScriptedEngine(["First.", "Second."], gated=True)
        self.engine.QUEUE_TICK = 0.01
        patcher = mock.patch.object(deps, "engine", self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.engine.settled()

    async def chat(self, client: httpx.AsyncClient, message: str) -> list:
        res = await client.post("/api/chat", json={"message": message})
        self.assertEqual(res.status_code, 200)
        return events(res.text)

    async def both(self, *, stop_the_second: bool = False) -> tuple[list, list]:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://aurora.test") as client:
            first = asyncio.create_task(self.chat(client, "One"))
            await self.engine.entered(1)
            second = asyncio.create_task(self.chat(client, "Two"))
            await support.until(lambda: len(self.engine._waiting) == 1)
            # Long enough for a notice or two.
            await asyncio.sleep(0.05)
            if stop_the_second:
                self.engine.cancel(self.engine._waiting[0])
                waited = await second
                self.engine.release()
                return await first, waited
            self.engine.release()
            return await first, await second

    async def test_a_message_that_waits_is_told_its_place_then_answered(self) -> None:
        first, second = await self.both()
        self.assertEqual(shape(first), ["conversation", *ANSWER])
        notices = shape(second)[1:shape(second).index("step:reply-1:running")]
        self.assertTrue(notices)
        self.assertEqual(set(notices), {"queue"})
        told = only(second, "queue")
        self.assertIs(told[0]["first"], True)
        self.assertEqual({t["position"] for t in told}, {0})
        self.assertEqual(shape(second)[-len(ANSWER):], ANSWER)
        self.assertEqual(self.engine.most_at_once, 1)

    async def test_a_message_stopped_while_it_waits_never_reaches_the_model(self) -> None:
        first, second = await self.both(stop_the_second=True)
        self.assertEqual(shape(first), ["conversation", *ANSWER])
        self.assertEqual(set(shape(second)[1:-2]), {"queue"})
        self.assertEqual(shape(second)[-2:], ["done", "run"])
        self.assertIs(only(second, "done")[0]["cancelled"], True)
        totals = only(second, "run")[0]
        self.assertEqual((totals["outcome"], totals["steps"]), ("stopped", 0))
        self.assertEqual(len(self.engine.passes), 1)
        convo = conversations.get(only(second, "conversation")[0]["id"])
        self.assertEqual([m["content"] for m in convo["messages"]], ["Two"])


class TrimTests(ChatCase):
    """A conversation that has outgrown the window, and a message that would."""

    def setUp(self) -> None:
        super().setUp()
        support.with_settings(self, tools_enabled=False)
        self.use_engine(PlayedEngine(cfg=cfg(n_ctx=4096)))

    def test_the_oldest_turns_give_way_to_a_summary_and_the_page_is_told(self) -> None:
        cid = conversations.create("Question 0")
        for n in range(6):
            conversations.add_message(cid, "user", f"Question {n}: " + "word " * 240)
            conversations.add_message(cid, "assistant", f"Answer {n}. " + "more " * 240)
        stream = self.chat("And now?", conversation_id=cid)
        self.assertEqual(shape(stream), ["conversation", "trimmed", *ANSWER])
        trimmed = only(stream, "trimmed")[0]
        self.assertEqual(set(trimmed), {"dropped", "turns", "summarised", "cut"})
        self.assertGreater(trimmed["summarised"], 0)
        self.assertEqual(trimmed["turns"] + trimmed["summarised"], 6)
        # A turn is a question and its answer.
        self.assertEqual(trimmed["dropped"], 2 * trimmed["summarised"])
        self.assertIs(trimmed["cut"], False)
        prompt = self.engine.prompts[0]
        self.assertTrue(prompt[1]["content"].startswith(SUMMARY_HEADING))
        self.assertEqual(said(prompt)[-1], "And now?")
        self.assertLessEqual(self.engine.prompt_tokens(prompt), self.engine.cfg.n_ctx)

    def test_a_message_too_long_for_the_window_is_cut_and_the_page_is_told(self) -> None:
        long = "word " * 1600
        stream = self.chat(long)
        self.assertEqual(only(stream, "trimmed"),
                         [{"dropped": 0, "turns": 0, "summarised": 0, "cut": True}])
        read = said(self.engine.prompts[0])[0]
        self.assertLess(len(read), len(long.strip()))
        # What was said is kept whole.
        self.assertEqual(self.conversation(stream)["messages"][0]["content"], long.strip())

    def test_a_conversation_that_fits_is_not_trimmed(self) -> None:
        self.assertEqual(only(self.chat("Hello"), "trimmed"), [])


class DateLineTests(ChatCase):
    """Every message says when it was sent, after it, in its own turn."""

    def test_the_message_is_read_with_when_it_was_sent(self) -> None:
        stopped_at(self, JAN_5)
        self.chat("Hello")
        self.assertEqual(self.engine.prompts[0][-1]["content"],
                         "Hello\n\n(Sent Mon 05 Jan 2026, 09:30 UTC.)")

    def test_the_time_is_the_sites_own(self) -> None:
        support.with_settings(self, timezone="Europe/Paris")
        stopped_at(self, JAN_5)
        self.chat("Hello")
        self.assertEqual(self.engine.prompts[0][-1]["content"],
                         "Hello\n\n(Sent Mon 05 Jan 2026, 10:30 CET.)")

    def test_every_reply_of_a_run_reads_the_same_line(self) -> None:
        stopped_at(self, JAN_5)
        self.use_engine(PlayedEngine(decision=WEATHER_IN_PARIS))
        support.played_tools(self, {"weather": WEATHER})
        self.chat("Do I need a coat in Paris")
        self.assertEqual(
            [p[1]["content"] for p in self.engine.prompts],
            ["Do I need a coat in Paris\n\n(Sent Mon 05 Jan 2026, 09:30 UTC.)"] * 2,
        )

    def test_a_command_is_read_with_it_too(self) -> None:
        stopped_at(self, JAN_5)
        self.chat("/calc 2+2")
        self.assertEqual(self.engine.prompts[0][1]["content"],
                         "/calc 2+2\n\n(Sent Mon 05 Jan 2026, 09:30 UTC.)")

    def test_each_message_keeps_its_own_time_when_it_is_read_again(self) -> None:
        stopped_at(self, JAN_5, datetime(2026, 1, 6, 18, 5, tzinfo=timezone.utc))
        first = self.chat("Hello")
        self.chat("Still there?", conversation_id=only(first, "conversation")[0]["id"])
        self.assertEqual(
            [m["content"] for m in self.engine.prompts[1][1:]],
            ["Hello\n\n(Sent Mon 05 Jan 2026, 09:30 UTC.)", "A short answer.",
             "Still there?\n\n(Sent Tue 06 Jan 2026, 18:05 UTC.)"],
        )


class LoopTests(ChatCase):
    """One call after another, within the limits the settings set."""

    def setUp(self) -> None:
        super().setUp()
        support.with_settings(self, agent_loop=True)

    def test_two_lookups_and_an_answer_are_streamed_stored_and_added_up(self) -> None:
        engine = self.use_engine(ScriptedEngine([PARIS, LISBON, "Lisbon."]))
        looked_up = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Is Paris or Lisbon warmer right now?")
        self.assertEqual(
            shape(stream),
            ["conversation", *CALLED, *LOOKUP,
             "step:reply-2:running", "start", "step:reply-2:ok",
             "step:tool-1:running", "step:tool-1:ok", "card",
             "step:reply-3:running", "start", "token", "step:reply-3:ok", "done", "run"],
        )
        self.assertEqual(step(stream, "reply-2", "ok")["label"], "Called Weather")
        self.assertEqual(looked_up["weather"], [{"location": "Paris"}, {"location": "Lisbon"}])
        rows = runs.run_steps(only(stream, "conversation")[0]["request_id"])
        self.assertEqual([r["kind"] for r in rows], ["call", "tool", "call", "tool", "answer"])
        totals = stream[-1][1]
        self.assertEqual((totals["steps"], totals["passes"], totals["outcome"]), (5, 3, "answered"))
        self.assertAlmostEqual(sum(r["total_ms"] for r in rows), totals["total_ms"], delta=0.06)
        answered = self.conversation(stream)["messages"][1]
        self.assertEqual(answered["content"], "Lisbon.")
        self.assertEqual(len(answered["tool_calls"]), 2)
        self.assertEqual({r["message_id"] for r in rows}, {answered["id"]})
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 2)
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)

    def test_a_call_already_made_is_not_made_again(self) -> None:
        self.use_engine(ScriptedEngine([PARIS, PARIS, "Overcast."]))
        looked_up = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        self.assertEqual(looked_up["weather"], [{"location": "Paris"}])
        self.assertEqual(step(stream, "tool-1", "failed")["detail"], "repeated")
        self.assertEqual(self.engine.passes[2].messages[-1]["content"],
                         response("Error: " + ALREADY_MADE))

    def test_at_the_limit_the_model_is_told_and_its_last_reply_cannot_call(self) -> None:
        support.with_settings(self, agent_max_steps=1)
        engine = self.use_engine(ScriptedEngine([PARIS, "Overcast. " + LISBON]))
        looked_up = support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Is Paris or Lisbon warmer right now?")
        self.assertEqual(engine.passes[1].messages[-1]["content"],
                         response(WEATHER.summary) + "\n\n" + NO_MORE_CALLS)
        self.assertEqual(engine.passes[1].stop, LAST_STOPS)
        # The call it began anyway ends it there.
        self.assertEqual(looked_up["weather"], [{"location": "Paris"}])
        self.assertEqual(self.conversation(stream)["messages"][1]["content"], "Overcast.")
        self.assertEqual(shape(stream)[-6:], THEN)

    def test_with_the_loop_off_one_call_is_all_a_message_may_make(self) -> None:
        support.with_settings(self, agent_loop=False)
        engine = self.use_engine(ScriptedEngine([PARIS, "Overcast."]))
        support.played_tools(self, {"weather": WEATHER})
        self.chat("Do I need a coat in Paris")
        self.assertEqual(len(engine.passes), 2)
        self.assertTrue(engine.passes[1].messages[-1]["content"].endswith(NO_MORE_CALLS))
        self.assertEqual(engine.passes[1].stop, LAST_STOPS)

    def test_one_engine_serves_message_after_message(self) -> None:
        engine = self.use_engine(ScriptedEngine(["First."] * 5))
        for _ in range(5):
            stream = self.chat("Suggest a name for my boat")
            self.assertEqual(only(stream, "done")[0]["completion_tokens"], 1)
            self.assertTrue(engine.free)
        self.assertEqual(len(engine.passes), 5)
        self.assertEqual(engine.info()["completed"], 5)


class ThinkingTests(ChatCase):
    """A model that thinks before it says anything."""

    def setUp(self) -> None:
        super().setUp()
        self.thinking = cfg(thinking="on")

    def thinks(self, *script: str) -> ScriptedEngine:
        return self.use_engine(ScriptedEngine(script, cfg=self.thinking))

    def test_the_thought_streams_apart_from_the_answer(self) -> None:
        self.thinks("Rayleigh scattering.</think>\n\nLight scatters.")
        stream = self.chat("Why is the sky blue?")
        told = shape(stream)
        self.assertEqual([w for w in told if w != "think"], ["conversation", *ANSWER])
        self.assertLess(told.index("think"), told.index("token"))
        self.assertEqual(step(stream, "reply-1", "running")["label"], "Thinking")
        self.assertEqual("".join(t["t"] for t in only(stream, "think")), "Rayleigh scattering.")
        self.assertIn("ttft_ms", only(stream, "think")[0])
        # The line breaks after the thought are not said.
        self.assertEqual("".join(t["t"] for t in only(stream, "token")), "Light scatters.")

    def test_the_thought_and_the_answer_are_two_requests_of_one_reply(self) -> None:
        engine = self.thinks("Rayleigh scattering.</think>\n\nLight scatters.")
        stream = self.chat("Why is the sky blue?")
        thought, answer = engine.passes
        self.assertEqual(thought.stop, [THINK_CLOSE])
        self.assertEqual({k: v for k, v in thought.sampling.items() if k != "max_tokens"},
                         PROSE_THINKING)
        self.assertEqual(thought.sampling["max_tokens"], engine.cfg.think_budget)
        # The answer goes on from the thought, closed.
        self.assertEqual(answer.prefix, "Rayleigh scattering.</think>")
        self.assertEqual(answer.messages, thought.messages)
        self.assertEqual(only(stream, "done")[0]["requests"], 2)
        self.assertEqual(len(only(stream, "start")), 1)

    def test_what_is_kept_is_the_answer_said_and_the_reply_as_written(self) -> None:
        self.thinks("Rayleigh scattering.</think>\n\nLight scatters.")
        stream = self.chat("Why is the sky blue?")
        done = only(stream, "done")[0]
        self.assertEqual(done["think_tokens"], 2)
        self.assertEqual(step(stream, "reply-1", "ok")["think_tokens"], 2)
        convo = self.conversation(stream)
        self.assertEqual(convo["messages"][1]["content"], "Light scatters.")
        (record,) = conversations.record(convo["id"])[1]
        self.assertEqual(record["parts"][-1],
                         {"role": "assistant", "kind": "answer", "tools": [],
                          "content": "<think>\nRayleigh scattering.</think>\n\nLight scatters."})
        (row,) = runs.run_steps(stream[0][1]["request_id"])
        self.assertEqual(row["input"], {"think_tokens": 2})

    def test_a_thought_before_a_call_and_one_before_the_answer_are_kept_in_order(self) -> None:
        self.thinks(
            "I should look.</think>\n\nLet me check.\n" + PARIS,
            "It is mild.</think>\n\nTake a coat.",
        )
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        cid = only(stream, "conversation")[0]["id"]
        answer = self.client.get(f"/api/conversations/{cid}").json()["messages"][1]
        self.assertEqual(answer["segments"], [
            {"kind": "thought", "text": "I should look."},
            {"kind": "text", "text": "Let me check."},
            {"kind": "card", "call": 0},
            {"kind": "thought", "text": "It is mild."},
            {"kind": "text", "text": "Take a coat."},
        ])
        self.assertEqual(answer["content"], "Let me check.\n\nTake a coat.")

    def test_the_next_message_reads_on_from_the_reply_as_it_was_written(self) -> None:
        engine = self.thinks(
            "Rayleigh scattering.</think>\n\nLight scatters.", "Shorter path.</think>\n\nRedder."
        )
        first = self.chat("Why is the sky blue?")
        self.chat("And at sunset?", conversation_id=only(first, "conversation")[0]["id"])
        self.assertEqual(len(engine.passes), 4)
        self.assertTrue(reads_on(engine, engine.passes[1], engine.passes[2]))


class StoredTraceTests(ChatCase):
    """What is left in the database about how each message was answered."""

    def rows(self, stream: list) -> list[dict]:
        return runs.run_steps(only(stream, "conversation")[0]["request_id"])

    def test_every_message_stores_steps_in_order_that_sum_to_the_run_total(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        cases = (
            ("a greeting", "Hello there", ["Hi."], ["answer"]),
            ("a command", "/calc 2+2", ["Four."], ["tool", "answer"]),
            ("the model's call", "Do I need a coat in Paris", [PARIS, "Take a coat."],
             ["call", "tool", "answer"]),
            ("two calls", "Paris or Lisbon?", [PARIS, LISBON, "Lisbon."],
             ["call", "tool", "call", "tool", "answer"]),
            ("a failed tool", "/calc 1/0", ["No."], ["tool", "answer"]),
            ("a call to no tool", "Look it up", [render_call("telescope", {}), "Nothing."],
             ["call", "tool", "answer"]),
        )
        for name, message, script, kinds in cases:
            with self.subTest(name):
                self.use_engine(ScriptedEngine(script))
                stream = self.chat(message)
                self.assertEqual(stream[-1][0], "run")
                totals = stream[-1][1]
                rows = self.rows(stream)
                self.assertEqual([r["kind"] for r in rows], kinds)
                self.assertEqual([r["idx"] for r in rows], list(range(len(rows))))
                self.assertEqual(totals["steps"], len(rows))
                self.assertEqual(totals["passes"], kinds.count("call") + kinds.count("answer"))
                self.assertEqual(totals["id"], only(stream, "conversation")[0]["request_id"])
                self.assertEqual(totals["outcome"], "answered")
                self.assertAlmostEqual(
                    sum(r["total_ms"] for r in rows), totals["total_ms"], delta=0.06
                )
                self.assertGreater(totals["wall_ms"], totals["total_ms"] - 1)

    def test_the_run_is_reported_last_with_what_it_cost_and_where_the_time_went(self) -> None:
        stream = self.chat("Hello there")
        self.assertEqual([name for name, _ in stream].count("run"), 1)
        self.assertEqual(
            set(stream[-1][1]),
            {"id", "outcome", "steps", "passes", "total_ms", "waiting_ms", "reading_ms", "ledger",
             "writing_ms", "tools_ms", "wall_ms"},
        )
        # A greeting is the model's to answer like anything else, in one reply.
        self.assertEqual((stream[-1][1]["steps"], stream[-1][1]["passes"]), (1, 1))

    def test_the_steps_of_an_answered_run_hang_off_its_answer(self) -> None:
        stream = self.chat("/calc 2+2")
        convo = self.conversation(stream)
        rows = self.rows(stream)
        self.assertEqual({r["message_id"] for r in rows}, {convo["messages"][1]["id"]})
        self.assertEqual({r["conversation_id"] for r in rows}, {convo["id"]})

    def test_the_stored_steps_of_the_replies_have_what_the_stream_reported(self) -> None:
        self.use_engine(PlayedEngine("Take a coat.", WEATHER_IN_PARIS))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        called, tool, answer = self.rows(stream)
        reported = step(stream, "reply-1", "ok")
        self.assertEqual((called["kind"], called["via"], called["name"]), ("call", "model", "weather"))
        # The call is kept as the model wrote it.
        self.assertEqual(called["output_text"], PARIS)
        self.assertEqual(called["prompt_tokens"], reported["tokens_in"])
        self.assertEqual(called["output_tokens"], reported["tokens_out"])
        self.assertEqual((tool["kind"], tool["name"], tool["input"], tool["output_text"]),
                         ("tool", "weather", {"location": "Paris"}, WEATHER.summary))
        done = only(stream, "done")[0]
        self.assertEqual(answer["prompt_tokens"], done["prompt_tokens"])
        self.assertEqual(answer["output_tokens"], done["completion_tokens"])
        self.assertEqual(answer["first_token_ms"], done["ttft_ms"])
        self.assertIsNone(answer["new_tokens"])
        # The answer's own text is in `messages`, not copied here.
        self.assertIsNone(answer["output_text"])

    def test_a_run_that_failed_leaves_its_steps_tied_to_no_answer(self) -> None:
        self.use_engine(PlayedEngine(ready=False))
        stream = self.chat("/calc 2+2")
        self.assertEqual(stream[-1][1]["outcome"], "failed")
        rows = self.rows(stream)
        self.assertEqual([r["kind"] for r in rows], ["tool", "answer"])
        self.assertEqual({r["message_id"] for r in rows}, {None})
        self.assertIs(rows[-1]["ok"], False)
        self.assertIn("still loading", rows[-1]["error"])

    def test_deleting_the_conversation_deletes_what_was_kept_of_its_runs(self) -> None:
        stream = self.chat("/calc 2+2")
        self.assertEqual(len(self.rows(stream)), 2)
        self.client.delete(f"/api/conversations/{only(stream, 'conversation')[0]['id']}")
        self.assertEqual(self.rows(stream), [])

    def test_with_a_retention_of_zero_the_run_is_still_reported_and_nothing_is_kept(self) -> None:
        self.db_settings.steps_keep_days = 0
        stream = self.chat("/calc 2+2")
        self.assertEqual(stream[-1][1]["steps"], 2)
        self.assertEqual(self.rows(stream), [])
        # The answer itself is kept as it always was.
        self.assertEqual(len(self.conversation(stream)["messages"]), 2)


class LedgerTests(ChatCase):
    """What a run did, told when it ends and read back with its answer."""

    def test_a_stored_answer_reads_back_the_same_ledger_the_page_was_told(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        self.use_engine(ScriptedEngine([PARIS, "Take a coat."]))
        stream = self.chat("Do I need a coat in Paris")
        live = stream[-1][1]
        stored = self.conversation(stream)["messages"][1]["run"]
        self.assertEqual([s["kind"] for s in live["ledger"]], ["call", "tool", "answer"])
        for key in ("steps", "passes", "total_ms", "waiting_ms", "reading_ms", "writing_ms",
                    "tools_ms", "ledger"):
            with self.subTest(key=key):
                self.assertEqual(stored[key], live[key])
        self.assertEqual(stored["id"], live["id"])
        self.assertIsNone(stored["wall_ms"])

    def test_an_answer_whose_steps_are_gone_has_no_run(self) -> None:
        stream = self.chat("Why is the sky blue?")
        runs.prune_steps(keep_days=-1)
        self.assertIsNone(self.conversation(stream)["messages"][1]["run"])
        self.assertNotIn("run", self.conversation(stream)["messages"][0])


class ReloadTests(ChatCase):
    """A conversation read back: its cards, and each answer in the order it
    was said."""

    def get(self, cid: str) -> dict:
        res = self.client.get(f"/api/conversations/{cid}")
        self.assertEqual(res.status_code, 200)
        return res.json()

    def test_the_cards_come_back_with_the_answer_that_made_them(self) -> None:
        self.use_engine(ScriptedEngine(["Let me check.\n" + PARIS, "Take a coat."]))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        convo = self.get(only(stream, "conversation")[0]["id"])
        asked, answered = convo["messages"]
        self.assertEqual(convo["orphan_tool_calls"], [])
        (call,) = answered["tool_calls"]
        self.assertEqual((call["tool"], call["card"], call["data"]),
                         ("weather", "weather", WEATHER.data))
        self.assertNotIn("segments", asked)

    def test_an_answer_is_read_back_as_words_cards_and_words_in_the_order_they_came(self) -> None:
        self.use_engine(ScriptedEngine(["Let me check.\n" + PARIS, "Take a coat."]))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Do I need a coat in Paris")
        answered = self.get(only(stream, "conversation")[0]["id"])["messages"][1]
        self.assertEqual(answered["segments"], [
            {"kind": "text", "text": "Let me check."},
            {"kind": "card", "call": 0},
            {"kind": "text", "text": "Take a coat."},
        ])

    def test_a_command_is_read_back_as_its_card_then_the_answer(self) -> None:
        stream = self.chat("/calc 2+2")
        answered = self.get(only(stream, "conversation")[0]["id"])["messages"][1]
        self.assertEqual(answered["segments"], [
            {"kind": "card", "call": 0}, {"kind": "text", "text": "A short answer."},
        ])

    def test_two_calls_are_read_back_as_two_cards_by_their_place(self) -> None:
        self.use_engine(ScriptedEngine([PARIS, LISBON, "Lisbon."]))
        support.played_tools(self, {"weather": WEATHER})
        stream = self.chat("Is Paris or Lisbon warmer right now?")
        answered = self.get(only(stream, "conversation")[0]["id"])["messages"][1]
        self.assertEqual(answered["segments"], [
            {"kind": "card", "call": 0}, {"kind": "card", "call": 1},
            {"kind": "text", "text": "Lisbon."},
        ])
        self.assertEqual(len(answered["tool_calls"]), 2)

    def test_an_answer_the_model_wrote_nothing_of_is_read_back_saying_so(self) -> None:
        # Stored as NO_ANSWER; the segments say it too, though the model
        # wrote none of it, so a page that draws from them shows the words.
        self.use_engine(PlayedEngine(""))
        stream = self.chat("/calc 2+2")
        answered = self.get(only(stream, "conversation")[0]["id"])["messages"][1]
        self.assertEqual(answered["content"], NO_ANSWER)
        self.assertIn({"kind": "text", "text": NO_ANSWER}, answered["segments"])

    def test_an_answer_kept_before_replies_were_kept_has_no_segments(self) -> None:
        cid = conversations.create("Hello")
        conversations.add_message(cid, "user", "Hello")
        conversations.add_message(cid, "assistant", "Hi there.")
        self.assertIsNone(self.get(cid)["messages"][1]["segments"])

    def test_an_answer_whose_calls_do_not_match_what_it_said_has_no_segments(self) -> None:
        cid = conversations.create("Hello")
        question = conversations.add_message(cid, "user", "Hello")
        answer = conversations.add_message(cid, "assistant", "Hi there.")
        conversations.add_context_turn(
            cid, "run-1", question_id=question, answer_id=answer, system_key="k",
            parts=[
                {"role": "user", "kind": "question", "content": "Hello", "tools": []},
                {"role": "assistant", "kind": "call", "content": PARIS, "tools": ["weather"]},
                {"role": "user", "kind": "response", "content": response("Overcast."),
                 "tools": ["weather"]},
                {"role": "assistant", "kind": "answer", "content": "Hi there.", "tools": []},
            ],
        )
        # One call said, and none kept with the answer.
        self.assertIsNone(self.get(cid)["messages"][1]["segments"])

    def test_a_stopped_answer_has_no_segments(self) -> None:
        self.use_engine(Stopping("Light scatters off the air."))
        stream = self.chat("Why is the sky blue?")
        self.assertIsNone(self.get(only(stream, "conversation")[0]["id"])["messages"][1]["segments"])


class CorrectionTests(ChatCase):
    """An answer held against what was looked up for it."""

    def test_a_figure_nothing_returned_is_said_under_the_answer_and_kept(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        engine = self.use_engine(ScriptedEngine(["It is 25°C, so leave the coat.", "Fine."]))
        first = self.chat("/weather Paris")
        done = only(first, "done")[0]
        expected = (
            "Not in what was looked up: 25°C. Weather returned: "
            "Paris, France: overcast, 17.9°C (feels 17.2°C), wind 9.4 km/h."
        )
        self.assertEqual((done["unsupported"], done["correction"]), (["25°C"], expected))
        cid = only(first, "conversation")[0]["id"]
        # Kept with the answer, so a reload shows it too.
        self.assertEqual(self.conversation(first)["messages"][1]["correction"], expected)
        # And kept for the model, in the harness's voice, quoting nothing.
        _, records = conversations.record(cid)
        last = records[0]["parts"][-1]
        self.assertEqual(last["kind"], "correction")
        self.assertEqual(
            last["content"],
            "Checked by the harness: 25°C in the answer above is not in what was looked up.",
        )
        # The next message still reads on from where the last one ended, and
        # reads the check before it.
        self.chat("Why?", conversation_id=cid)
        before, after = engine.passes
        self.assertTrue(reads_on(engine, before, after))
        self.assertEqual(support.unsent(after.messages)[-1]["content"], last["content"] + "\n\nWhy?")

    def test_a_figure_the_model_read_in_its_own_call_is_supported(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        self.use_engine(ScriptedEngine([PARIS, "It is 17.9°C and overcast."]))
        done = only(self.chat("How warm is Paris?"), "done")[0]
        self.assertEqual((done["unsupported"], done["correction"]), ([], None))

    def test_an_answer_true_to_its_lookup_draws_nothing(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        self.use_engine(ScriptedEngine(["About 18 degrees and overcast."]))
        stream = self.chat("/weather Paris")
        done = only(stream, "done")[0]
        self.assertEqual((done["unsupported"], done["correction"]), ([], None))
        self.assertIsNone(self.conversation(stream)["messages"][1]["correction"])

    def test_an_answer_that_looked_nothing_up_is_not_checked(self) -> None:
        self.use_engine(ScriptedEngine(["Paris has 2.1 million people."]))
        done = only(self.chat("How many people live in Paris?"), "done")[0]
        self.assertEqual((done["unsupported"], done["correction"]), ([], None))


class ReplayTests(ChatCase):
    """The next message is read with the conversation as it was sent."""

    def two(self, engine: ScriptedEngine, first: str, then: str) -> str:
        """Two messages of one conversation. Returns its id."""
        self.use_engine(engine)
        cid = only(self.chat(first), "conversation")[0]["id"]
        self.chat(then, conversation_id=cid)
        return cid

    def test_each_message_reads_on_from_where_the_last_one_ended(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        cases = (
            # (name, script, first message, then, the last pass of the first
            # message, the first pass of the second)
            ("a greeting", ["One.", "Two."], "Hello", "And?", 0, 1),
            ("a command", ["Four.", "Because."], "/calc 2+2", "Why?", 0, 1),
            ("the model's call", [PARIS, "Overcast.", "Milder."],
             "Do I need a coat in Paris", "And tomorrow?", 1, 2),
            ("words before a call", ["Let me check.\n" + PARIS, "Overcast.", "Milder."],
             "Do I need a coat in Paris", "And tomorrow?", 1, 2),
            ("a question asked back", ["Which city?", PARIS, "Overcast."],
             "what is the weather like", "Paris", 0, 1),
        )
        for name, script, first, then, last, next_ in cases:
            with self.subTest(name):
                engine = ScriptedEngine(script)
                self.two(engine, first, then)
                self.assertEqual(len(engine.passes), len(script), "every pass as scripted")
                self.assertTrue(reads_on(engine, engine.passes[last], engine.passes[next_]))

    def test_what_was_sent_is_kept_with_the_conversation_and_goes_with_it(self) -> None:
        support.played_tools(self, {"weather": WEATHER})
        cid = self.two(ScriptedEngine([PARIS, "Overcast.", "Milder."]),
                       "Do I need a coat in Paris today", "And tomorrow?")
        messages, records = conversations.record(cid)
        self.assertEqual(len(records), 2)
        first = records[0]
        self.assertEqual((first["question_id"], first["answer_id"]),
                         (messages[0]["id"], messages[1]["id"]))
        self.assertEqual([p["kind"] for p in first["parts"]],
                         ["question", "now", "call", "response", "answer"])
        self.assertEqual(first["parts"][2]["content"], PARIS)
        self.assertEqual(first["parts"][3]["tools"], ["weather"])
        self.assertEqual(first["parts"][3]["content"], response(WEATHER.summary))
        # Reloading the conversation shows what it always showed.
        convo = self.client.get(f"/api/conversations/{cid}").json()
        self.assertEqual([m["content"] for m in convo["messages"]],
                         ["Do I need a coat in Paris today", "Overcast.", "And tomorrow?", "Milder."])
        self.client.delete(f"/api/conversations/{cid}")
        self.assertEqual(conversations.record(cid), ([], []))
        # And it is not cleared out with the steps.
        cid = self.two(ScriptedEngine(["One.", "Two."]), "Hello", "And?")
        runs.prune_steps(keep_days=0)
        self.assertEqual(len(conversations.record(cid)[1]), 2)

    def test_a_conversation_kept_before_this_is_read_as_it_always_was(self) -> None:
        cid = conversations.create("Hello")
        conversations.add_message(cid, "user", "Hello")
        conversations.add_message(cid, "assistant", "Hi there.")
        engine = self.use_engine(ScriptedEngine(["Fine."]))
        self.chat("And what can you do?", conversation_id=cid)
        self.assertEqual(said(engine.passes[0].messages),
                         ["Hello", "Hi there.", "And what can you do?"])


if __name__ == "__main__":
    unittest.main()
