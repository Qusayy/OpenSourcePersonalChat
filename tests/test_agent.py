"""One run, driven directly: no HTTP, no database.

Needs the app's own requirements; without them the whole module is skipped.
The engine is the real one with a script where the model would be, so a run
here takes the slot, queues and hands the slot back exactly as it does on the
server. Tools that would go out are replaced.
"""

from __future__ import annotations

import asyncio
import unittest

try:
    from app import agent
    from app.personas import get_persona
    from app.skills import get_skill
    from app.tools.base import ToolResult
    from evals.scripted import ScriptedEngine
    from tests import support
    from tests.support import collect, only, shape, until
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

NONE = '{"tool": "none"}'
WEATHER = '{"tool": "weather", "args": {"location": "Paris"}}'
PARIS = ToolResult(True, "Paris, France: overcast, 17.9°C.", card="weather", data={"place": "Paris"})
ANSWER = ["step:answer:running", "start", "token", "step:answer:ok", "done", "run"]


class AgentCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, tools_enabled=True, allow_outbound=True)

    def scripted(self, *script, **options) -> ScriptedEngine:
        engine = ScriptedEngine(script, **options)
        engine.QUEUE_TICK = 0.01
        self.addAsyncCleanup(engine.settled)
        return engine

    def run_message(self, message: str, engine: ScriptedEngine, *, persona: str = "agent",
                    skill: str | None = None, history: list[dict] | None = None,
                    trace: agent.Trace | None = None):
        return agent.run(
            message,
            persona=get_persona(persona),
            skill=get_skill(skill),
            history=history or [],
            trace=trace,
            engine=engine,
        )


class RunTests(AgentCase):
    async def test_a_run_needs_no_database_and_ends_in_done(self) -> None:
        engine = self.scripted("Light scatters off air.")
        trace = agent.Trace()
        stream = await collect(self.run_message("Why is the sky blue?", engine, persona="explainer",
                                                trace=trace))
        self.assertEqual(shape(stream), ANSWER)
        self.assertEqual(trace.answer, "Light scatters off air.")
        self.assertEqual(trace.usage, only(stream, "done")[0])
        self.assertEqual(trace.calls, [])

    async def test_the_model_chooses_through_the_slot_and_then_answers_through_it(self) -> None:
        engine = self.scripted(WEATHER, "Take a coat.")
        asked = support.played_tools(self, {"weather": PARIS})
        trace = agent.Trace()
        stream = await collect(self.run_message("Do I need a coat in Paris", engine, trace=trace))
        self.assertEqual(
            shape(stream),
            ["step:route:running", "step:route:ok", "step:tool-0:running", "step:tool-0:ok",
             "card", *ANSWER],
        )
        self.assertEqual(asked["weather"], [{"location": "Paris"}])
        # Two passes, one after the other, never together.
        self.assertEqual(len(engine.passes), 2)
        self.assertEqual(engine.most_at_once, 1)
        self.assertIsNotNone(engine.passes[0].grammar)
        self.assertIsNone(engine.passes[1].grammar)
        self.assertIn("weather: Paris, France: overcast, 17.9°C.",
                      engine.passes[1].messages[-1]["content"])
        self.assertEqual([call.name for call, _ in trace.calls], ["weather"])
        self.assertEqual(trace.calls[0][0].via, "model")
        self.assertTrue(engine.free)

    async def test_the_time_on_the_routing_step_is_the_time_the_pass_took(self) -> None:
        engine = self.scripted(NONE, "A short answer.", prefill=0.05)
        stream = await collect(self.run_message("Suggest a name for my boat", engine))
        routed = support.step(stream, "route", "ok")
        self.assertEqual(routed["detail"], "model")
        self.assertGreaterEqual(routed["ms"], 50)

    async def test_a_skill_makes_one_pass_and_it_is_the_answer(self) -> None:
        engine = self.scripted("It is four.")
        stream = await collect(self.run_message("2+2", engine, skill="do_maths"))
        self.assertEqual(only(stream, "done")[0]["tool_calls"], 1)
        self.assertEqual(len(engine.passes), 1)
        self.assertIsNone(engine.passes[0].grammar)

    async def test_a_run_that_fails_ends_in_an_error_and_not_an_exception(self) -> None:
        engine = self.scripted("never said")

        async def broken(name: str, args: dict) -> ToolResult:
            raise RuntimeError("the dispatcher itself broke")

        stream = await collect(agent.run(
            "/calc 2+2", persona=get_persona("explainer"), history=[], engine=engine,
            call_tool=broken,
        ))
        self.assertEqual(
            stream[-2], ("error", {"message": "RuntimeError: the dispatcher itself broke"})
        )
        self.assertEqual(stream[-1][0], "run")
        self.assertEqual(stream[-1][1]["outcome"], "failed")
        self.assertEqual(engine.passes, [])


class StopTests(AgentCase):
    """Somebody presses Stop. The run is known by one id from its first stage
    to its last, and stopping it works at every one of them."""

    async def test_a_run_goes_by_the_id_it_was_given_and_forgets_it_at_the_end(self) -> None:
        engine = self.scripted("A short answer.")
        stream = await collect(agent.run(
            "Hello", persona=get_persona("explainer"), history=[], engine=engine, request_id="run",
        ))
        self.assertEqual(only(stream, "start")[0]["request_id"], "run")
        self.assertIs(engine.cancel("run"), False)
        self.assertEqual(engine._cancels, {})

    async def test_a_run_given_no_id_makes_one_up(self) -> None:
        engine = self.scripted("A short answer.")
        stream = await collect(self.run_message("Hello", engine, persona="explainer"))
        self.assertTrue(only(stream, "start")[0]["request_id"])

    async def test_stopped_while_choosing_a_tool_it_calls_none_and_writes_nothing(self) -> None:
        engine = self.scripted(WEATHER, "never said", gated=True)
        asked = support.played_tools(self, {"weather": PARIS})
        trace = agent.Trace()
        reading = asyncio.create_task(collect(agent.run(
            "Do I need a coat in Paris", persona=get_persona("agent"), history=[], trace=trace,
            engine=engine, request_id="run",
        )))
        await engine.entered(1)
        self.assertIs(engine.cancel("run"), True)
        engine.release()
        stream = await reading

        self.assertEqual(
            shape(stream), ["step:route:running", "step:route:stopped", "done", "run"]
        )
        done = only(stream, "done")[0]
        self.assertIs(done["cancelled"], True)
        self.assertEqual(done["tool_calls"], 0)
        self.assertIsNone(done["prompt_tokens"])
        self.assertEqual(asked["weather"], [])
        self.assertEqual(len(engine.passes), 1)
        self.assertEqual(trace.answer, "")
        self.assertEqual(trace.calls, [])
        await engine.settled()
        self.assertTrue(engine.free)

    async def test_stopped_while_a_tool_is_out_the_tool_is_called_off(self) -> None:
        engine = self.scripted("never said")
        out = asyncio.Event()
        called_off: list[bool] = []

        async def slow(name: str, args: dict) -> ToolResult:
            out.set()
            try:
                await asyncio.Event().wait()  # a page that never answers
            except asyncio.CancelledError:
                called_off.append(True)
                raise
            return PARIS

        trace = agent.Trace()
        reading = asyncio.create_task(collect(agent.run(
            "/weather Paris", persona=get_persona("explainer"), history=[], trace=trace,
            engine=engine, call_tool=slow, request_id="run",
        )))
        await asyncio.wait_for(out.wait(), 5)
        engine.cancel("run")
        stream = await asyncio.wait_for(reading, 5)

        self.assertEqual(
            shape(stream),
            ["step:route:ok", "step:tool-0:running", "step:tool-0:stopped", "done", "run"],
        )
        self.assertEqual(
            support.step(stream, "tool-0", "stopped"),
            {"id": "tool-0", "kind": "tool", "status": "stopped", "label": "Weather",
             "glyph": "☁", "tool": "weather"},
        )
        self.assertIs(only(stream, "done")[0]["cancelled"], True)
        self.assertEqual(called_off, [True])
        self.assertEqual(trace.calls, [])
        self.assertEqual(engine.passes, [])

    async def test_stopped_between_two_tools_the_second_is_never_called(self) -> None:
        engine = self.scripted("never said")
        seen: list[dict] = []

        async def first_then_stop(name: str, args: dict) -> ToolResult:
            seen.append(args)
            engine.cancel("run")
            return ToolResult(True, f"Wikipedia — {args['query']}.", card="wikipedia")

        trace = agent.Trace()
        stream = await collect(agent.run(
            "Python vs Rust", persona=get_persona("agent"), skill=get_skill("compare"),
            history=[], trace=trace, engine=engine, call_tool=first_then_stop, request_id="run",
        ))
        self.assertEqual(
            shape(stream),
            ["step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card", "done", "run"],
        )
        self.assertEqual(seen, [{"query": "Python"}])
        done = only(stream, "done")[0]
        self.assertIs(done["cancelled"], True)
        # What ran is what is counted, not what was planned.
        self.assertEqual(done["tool_calls"], 1)
        self.assertEqual(len(trace.calls), 1)
        self.assertEqual(engine.passes, [])

    async def test_stopped_while_writing_it_keeps_what_was_written(self) -> None:
        engine = self.scripted("one two three four five six seven eight", delay=0.02)
        trace = agent.Trace()
        run = agent.run(
            "Count to eight", persona=get_persona("explainer"), history=[], trace=trace,
            engine=engine, request_id="run",
        )
        stream = []
        async for item in run:
            stream.append(item)
            if sum(name == "token" for name, _ in stream) == 2:
                engine.cancel("run")
        self.assertEqual(shape(stream), ANSWER)
        done = only(stream, "done")[0]
        self.assertIs(done["cancelled"], True)
        self.assertLess(done["completion_tokens"], 8)
        self.assertTrue(trace.answer.startswith("one two"))
        self.assertTrue(engine.free)

    async def test_stopped_while_the_answer_waits_its_turn_it_never_starts(self) -> None:
        engine = self.scripted("Somebody else's answer.", "never said", gated=True)
        other = asyncio.create_task(collect(self.run_message("Hello", engine, persona="explainer")))
        await engine.entered(1)
        trace = agent.Trace()
        reading = asyncio.create_task(collect(agent.run(
            "Hello again", persona=get_persona("explainer"), history=[], trace=trace,
            engine=engine, request_id="run",
        )))
        await until(lambda: engine.info()["queued"] == 1)
        engine.cancel("run")
        stream = await asyncio.wait_for(reading, 5)

        # Whether a notice of its place got out first is a matter of timing.
        self.assertEqual(
            [word for word in shape(stream) if word != "queue"],
            ["step:answer:running", "step:answer:ok", "done", "run"],
        )
        self.assertIs(only(stream, "done")[0]["cancelled"], True)
        self.assertEqual(trace.answer, "")
        self.assertEqual(len(engine.passes), 1)
        engine.release()
        await other


class LeavingTests(AgentCase):
    """The reader goes away part-way through a run."""

    async def test_a_reader_that_leaves_while_a_tool_is_out_calls_the_tool_off(self) -> None:
        engine = self.scripted("never said")
        out = asyncio.Event()
        called_off: list[bool] = []

        async def slow(name: str, args: dict) -> ToolResult:
            out.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                called_off.append(True)
                raise
            return PARIS

        reading = asyncio.create_task(collect(agent.run(
            "/weather Paris", persona=get_persona("explainer"), history=[], engine=engine,
            call_tool=slow,
        )))
        await asyncio.wait_for(out.wait(), 5)
        reading.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reading
        await asyncio.sleep(0)
        self.assertEqual(called_off, [True])
        self.assertEqual(engine._cancels, {})

    async def test_a_run_closed_while_choosing_a_tool_keeps_the_slot_until_the_thread_has_left(self) -> None:
        engine = self.scripted(WEATHER, "Someone else's answer.", gated=True)
        reading = asyncio.create_task(collect(self.run_message("Do I need a coat in Paris", engine)))
        await engine.entered(1)

        reading.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reading
        self.assertTrue(engine._lock.locked())

        other = asyncio.create_task(collect(self.run_message("Hello", engine, persona="explainer")))
        await until(lambda: engine.info()["queued"] == 1)
        self.assertEqual(len(engine.passes), 1)

        engine.release()
        stream = await other
        self.assertEqual(shape(stream), ["step:answer:running", "queue", *ANSWER[1:]])
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)

    async def test_a_run_closed_mid_answer_stops_the_writing(self) -> None:
        engine = self.scripted("one two three four five six seven eight", delay=0.02)
        run = self.run_message("Count to eight", engine, persona="explainer")
        tokens = 0
        async for name, _ in run:
            tokens += name == "token"
            if tokens == 2:
                break
        await run.aclose()
        await engine.settled()
        self.assertLess(engine.passes[0].yielded, 8)
        self.assertTrue(engine.free)


class TraceTests(AgentCase):
    """What a run writes down about itself: each thing it did, in order, and
    what each cost. The last event of every stream adds it up."""

    async def traced(self, message: str, engine: ScriptedEngine, **more):
        trace = agent.Trace()
        stream = await collect(self.run_message(message, engine, trace=trace, **more))
        return stream, trace

    async def test_every_kind_of_message_leaves_steps_that_add_up_to_its_total(self) -> None:
        support.played_tools(self, {"weather": PARIS})
        cases = (
            # what it is, the message, how it is sent, what the model says, the steps, how it ends
            ("a plain answer", "Why is the sky blue?", {"persona": "explainer"},
             ["Light scatters."], ["answer"], "answered"),
            ("a command", "/calc 2+2", {"persona": "explainer"},
             ["Four."], ["route", "tool", "answer"], "answered"),
            ("a pattern", "15% of 200", {}, ["Thirty."], ["route", "tool", "answer"], "answered"),
            ("the model's choice", "Do I need a coat in Paris", {},
             [WEATHER, "Yes."], ["route", "tool", "answer"], "answered"),
            ("the model declining", "Suggest a name for my boat", {},
             [NONE, "Wren."], ["route", "answer"], "answered"),
            ("a skill", "2+2", {"skill": "do_maths"},
             ["Four."], ["route", "tool", "answer"], "answered"),
            ("a failed tool", "/calc 1/0", {"persona": "explainer"},
             ["It cannot be done."], ["route", "tool", "answer"], "answered"),
            ("a model that raises", "Hello", {"persona": "explainer"},
             [ValueError("boom")], ["answer"], "failed"),
        )
        for name, message, how, script, kinds, outcome in cases:
            with self.subTest(name):
                stream, trace = await self.traced(message, self.scripted(*script), **how)
                self.assertEqual(stream[-1][0], "run")
                totals = stream[-1][1]
                self.assertEqual([s.kind for s in trace.steps], kinds)
                self.assertEqual(totals["steps"], len(kinds))
                self.assertEqual(totals["passes"], sum(s.model for s in trace.steps))
                self.assertEqual(totals["passes"], len(script))
                self.assertEqual(totals["outcome"], outcome)
                self.assertEqual(totals["id"], trace.run_id)
                self.assertAlmostEqual(
                    totals["total_ms"], sum(s.total_ms for s in trace.steps), delta=0.06
                )
                # The stopwatch started before the first step and stopped after the last.
                self.assertGreater(totals["wall_ms"], totals["total_ms"] - 1)

    async def test_a_pass_is_its_wait_its_reading_and_its_writing(self) -> None:
        engine = self.scripted("one two three", prefill=0.03, delay=0.01)
        stream, trace = await self.traced("Hello", engine, persona="explainer")
        done = only(stream, "done")[0]
        (step,) = trace.steps
        self.assertEqual((step.kind, step.model, step.ok), ("answer", True, True))
        self.assertEqual(step.prompt_tokens, done["prompt_tokens"])
        self.assertEqual(step.output_tokens, done["completion_tokens"])
        self.assertEqual(step.queue_ms, done["queue_wait_ms"])
        self.assertEqual(step.first_token_ms, done["ttft_ms"])
        self.assertAlmostEqual(step.total_ms, done["queue_wait_ms"] + done["total_ms"])
        self.assertGreaterEqual(step.first_token_ms, 30)
        # Nothing here can say how much of the prompt the model had cached.
        self.assertIsNone(step.new_tokens)

    async def test_the_wait_for_the_slot_is_part_of_what_a_pass_cost(self) -> None:
        engine = self.scripted("Somebody else's answer.", "Ours.", gated=True)
        other = asyncio.create_task(collect(self.run_message("Hello", engine, persona="explainer")))
        await engine.entered(1)
        trace = agent.Trace()
        ours = asyncio.create_task(
            collect(self.run_message("Hello again", engine, persona="explainer", trace=trace))
        )
        await until(lambda: engine.info()["queued"] == 1)
        engine.release()
        await other
        stream = await ours
        done = only(stream, "done")[0]
        (step,) = trace.steps
        # It waited behind the other run, and that wait is in its cost.
        self.assertGreater(step.queue_ms, 0)
        self.assertEqual(step.queue_ms, done["queue_wait_ms"])
        self.assertAlmostEqual(step.total_ms, step.queue_ms + done["total_ms"])

    async def test_the_stopwatch_and_the_steps_agree_to_within_the_time_between_them(self) -> None:
        engine = self.scripted(NONE, "one two three four", prefill=0.02, delay=0.01)
        stream, _ = await self.traced("Suggest a name for my boat", engine)
        totals = stream[-1][1]
        self.assertGreater(totals["total_ms"], 60)  # two passes that each waited 20 ms and more
        gap = totals["wall_ms"] - totals["total_ms"]
        self.assertGreater(gap, -1)
        self.assertLess(gap, 2000)  # generous: a busy test machine, not a claim about speed

    async def test_a_choice_the_model_made_is_written_down_whole(self) -> None:
        support.played_tools(self, {"weather": PARIS})
        engine = self.scripted(WEATHER, "Yes.")
        _, trace = await self.traced("Do I need a coat in Paris", engine)
        step = trace.steps[0]
        self.assertEqual((step.kind, step.model, step.via, step.name),
                         ("route", True, "model", "weather"))
        self.assertEqual(step.input, {"text": "Do I need a coat in Paris"})
        self.assertEqual(step.output, WEATHER)
        self.assertEqual(step.prompt_tokens, engine.prompt_tokens(engine.passes[0].messages))
        self.assertEqual(step.output_tokens, len(engine.passes[0].pieces))
        self.assertIs(step.ok, True)
        self.assertIsNone(step.error)

    async def test_a_choice_that_could_not_be_used_says_what_was_wrong_with_it(self) -> None:
        cut = '{"tool": "wikipedia", "args": {"query": "the long'
        for said, error in (
            ('{"tool": "telescope", "args": {}}', "model picked an unknown tool: telescope"),
            (cut, "the reply was not a complete JSON object"),
            (ValueError("boom"), "ValueError: boom"),
        ):
            with self.subTest(said=str(said)):
                _, trace = await self.traced(
                    "Suggest a name for my boat", self.scripted(said, "Wren.")
                )
                step = trace.steps[0]
                self.assertEqual((step.kind, step.via, step.name), ("route", "model", None))
                self.assertIs(step.ok, False)
                self.assertEqual(step.error, error)
                # It is still "no tool": the run goes on to answer.
                self.assertEqual(trace.outcome, "answered")

    async def test_a_choice_made_without_the_model_costs_nothing_and_says_how(self) -> None:
        for message, how, via, name, given, why in (
            ("/calc 2+2", {"persona": "explainer"}, "slash", "calculator",
             {"expression": "2+2"}, None),
            ("15% of 200", {}, "heuristic", "calculator",
             {"expression": "15/100*200"}, "looks like arithmetic"),
            ("2+2", {"skill": "do_maths"}, "skill", "do_maths",
             {"calls": [{"tool": "calculator", "args": {"expression": "2+2"}}]}, None),
        ):
            with self.subTest(via=via):
                _, trace = await self.traced(message, self.scripted("Four."), **how)
                step = trace.steps[0]
                self.assertEqual((step.kind, step.model, step.via, step.name),
                                 ("route", False, via, name))
                self.assertEqual(step.input, given)
                self.assertEqual(step.output, why)
                self.assertEqual(step.total_ms, 0.0)
                self.assertIsNone(step.prompt_tokens)

    async def test_a_message_that_was_never_routed_has_no_routing_step(self) -> None:
        _, trace = await self.traced("15% of 200", self.scripted("Thirty."), persona="explainer")
        self.assertEqual([s.kind for s in trace.steps], ["answer"])

    async def test_a_tool_is_written_down_with_what_it_was_given_and_what_came_back(self) -> None:
        _, trace = await self.traced("/calc 2+2", self.scripted("Four."), persona="explainer")
        step = trace.steps[1]
        (_, result), = trace.calls
        self.assertEqual((step.kind, step.model, step.name, step.via),
                         ("tool", False, "calculator", "slash"))
        self.assertEqual(step.input, {"expression": "2+2"})
        self.assertEqual(step.output, "2+2 = 4")
        self.assertEqual(step.total_ms, result.ms)
        self.assertIs(step.ok, True)

        _, trace = await self.traced("/calc 1/0", self.scripted("No."), persona="explainer")
        failed = trace.steps[1]
        self.assertIs(failed.ok, False)
        self.assertEqual(failed.error, "division by zero")
        self.assertTrue(failed.output.startswith("That is not something this calculator handles."))

    async def test_a_prompt_that_lost_its_oldest_turns_says_how_many(self) -> None:
        from app.config import settings

        tight = settings.model_copy(update={"mock": True, "mock_delay": 0.0, "n_ctx": 700})
        engine = ScriptedEngine(["Short."], cfg=tight)
        self.addAsyncCleanup(engine.settled)
        history = []
        for i in range(6):
            history += [{"role": "user", "content": f"Question {i}? " * 12},
                        {"role": "assistant", "content": f"Answer {i}. " * 12}]
        stream, trace = await self.traced("And now?", engine, persona="concise", history=history)
        dropped = only(stream, "trimmed")[0]["dropped"]
        self.assertGreater(dropped, 0)
        self.assertEqual(trace.steps[-1].input, {"dropped": dropped})

    async def test_a_run_stopped_while_choosing_writes_down_one_stopped_pass(self) -> None:
        engine = self.scripted(WEATHER, gated=True)
        trace = agent.Trace()
        reading = asyncio.create_task(collect(agent.run(
            "Do I need a coat in Paris", persona=get_persona("agent"), history=[], trace=trace,
            engine=engine, request_id="run",
        )))
        await engine.entered(1)
        engine.cancel("run")
        engine.release()
        stream = await reading
        (step,) = trace.steps
        self.assertEqual((step.kind, step.model, step.ok, step.error),
                         ("route", True, False, "stopped"))
        totals = stream[-1][1]
        self.assertEqual((totals["outcome"], totals["steps"], totals["passes"]), ("stopped", 1, 1))
        self.assertEqual(totals["id"], "run")

    async def test_a_tool_that_was_called_off_is_written_down_as_stopped(self) -> None:
        engine = self.scripted()
        out = asyncio.Event()

        async def slow(name: str, args: dict) -> ToolResult:
            out.set()
            await asyncio.Event().wait()
            return PARIS

        trace = agent.Trace()
        reading = asyncio.create_task(collect(agent.run(
            "/weather Paris", persona=get_persona("explainer"), history=[], trace=trace,
            engine=engine, call_tool=slow, request_id="run",
        )))
        await asyncio.wait_for(out.wait(), 5)
        engine.cancel("run")
        stream = await asyncio.wait_for(reading, 5)
        self.assertEqual([s.kind for s in trace.steps], ["route", "tool"])
        self.assertEqual((trace.steps[1].ok, trace.steps[1].error), (False, "stopped"))
        self.assertEqual(stream[-1][1]["outcome"], "stopped")
        self.assertEqual(stream[-1][1]["passes"], 0)

    async def test_an_answer_stopped_part_way_is_a_stopped_run_with_its_tokens_counted(self) -> None:
        engine = self.scripted("one two three four five six seven eight", delay=0.02)
        trace = agent.Trace()
        run = agent.run("Count", persona=get_persona("explainer"), history=[], trace=trace,
                        engine=engine, request_id="run")
        stream = []
        async for item in run:
            stream.append(item)
            if sum(name == "token" for name, _ in stream) == 2:
                engine.cancel("run")
        (step,) = trace.steps
        self.assertEqual((step.kind, step.ok, step.error), ("answer", False, "stopped"))
        self.assertEqual(step.output_tokens, only(stream, "done")[0]["completion_tokens"])
        self.assertEqual(stream[-1][1]["outcome"], "stopped")

    async def test_a_reader_that_has_gone_is_not_sent_a_total(self) -> None:
        engine = self.scripted("one two three four five six seven eight", delay=0.02)
        trace = agent.Trace()
        run = self.run_message("Count", engine, persona="explainer", trace=trace)
        async for name, _ in run:
            if name == "token":
                break
        await run.aclose()
        await engine.settled()
        self.assertIsNone(trace.outcome)
        self.assertIsNone(trace.wall_ms)


DOLLARS = '{"tool": "currency", "args": {"amount": 100, "from_currency": "dollars", "to_currency": "EUR"}}'
USD = '{"tool": "currency", "args": {"amount": 100, "from_currency": "USD", "to_currency": "EUR"}}'
RATE = ToolResult(True, "100.00 USD = 92.15 EUR (rate 0.9215, ECB 2026-10-13).", card="currency")
REASON = (
    "from_currency must be one of the 32 values it knows, such as AUD, BGN, BRL; got 'dollars'"
)
EUROS = "How many euros is 100 dollars"


class RepairTests(AgentCase):
    """The model chose a call its tool cannot take. It is told why, once.

    The grammar keeps the real model from most of this; a script is not held
    to the grammar, which is how these replies come to be said at all.
    """

    async def traced(self, message: str, engine: ScriptedEngine, **more):
        trace = agent.Trace()
        stream = await collect(self.run_message(message, engine, trace=trace, **more))
        return stream, trace

    async def test_a_call_the_tool_cannot_take_is_sent_back_once_with_the_reason(self) -> None:
        engine = self.scripted(DOLLARS, USD, "About 92 euros.")
        asked = support.played_tools(self, {"currency": RATE})
        stream, trace = await self.traced(EUROS, engine)

        # On the page it is one choice, then the tool, then the answer.
        self.assertEqual(
            shape(stream),
            ["step:route:running", "step:route:ok", "step:tool-0:running", "step:tool-0:ok",
             "card", *ANSWER],
        )
        self.assertEqual(support.step(stream, "route", "ok")["label"], "Currency")
        self.assertEqual(
            asked["currency"], [{"amount": 100, "from_currency": "USD", "to_currency": "EUR"}]
        )
        self.assertEqual(len(engine.passes), 3)
        self.assertEqual(
            [(s.kind, s.ok) for s in trace.steps],
            [("route", False), ("repair", True), ("tool", True), ("answer", True)],
        )
        self.assertEqual(trace.steps[0].error, "rejected: " + REASON)
        self.assertEqual((trace.steps[1].name, trace.steps[1].via), ("currency", "model"))
        self.assertEqual(trace.steps[1].input, {"rejected": [REASON]})
        self.assertEqual(trace.steps[1].output, USD)

    async def test_the_second_try_reads_the_first_prompt_its_own_reply_and_the_reason(self) -> None:
        engine = self.scripted(DOLLARS, USD, "About 92 euros.")
        support.played_tools(self, {"currency": RATE})
        await self.traced(EUROS, engine)
        first, second = engine.passes[0], engine.passes[1]
        self.assertEqual(second.messages[:2], first.messages)
        self.assertEqual(
            second.messages[2:],
            [
                {"role": "assistant", "content": DOLLARS},
                {"role": "user", "content": (
                    f"That call was not made: {REASON}. Reply with one JSON object only."
                )},
            ],
        )
        # What it has already read is what the second prompt begins with, to
        # the letter, so only the two new turns have to be read.
        self.assertTrue(second.prompt.startswith(first.prompt + DOLLARS))
        self.assertEqual(second.grammar, first.grammar)
        self.assertEqual(second.sampling, first.sampling)

    async def test_the_step_on_the_page_says_what_both_passes_cost(self) -> None:
        engine = self.scripted(DOLLARS, USD, "About 92 euros.", prefill=0.02)
        support.played_tools(self, {"currency": RATE})
        stream, trace = await self.traced(EUROS, engine)
        routed = support.step(stream, "route", "ok")
        first, second = trace.steps[0], trace.steps[1]
        self.assertEqual(routed["tokens_in"], first.prompt_tokens + second.prompt_tokens)
        self.assertEqual(routed["tokens_out"], first.output_tokens + second.output_tokens)
        self.assertGreaterEqual(routed["ms"], 40)
        self.assertEqual(stream[-1][1]["passes"], 3)

    async def test_a_second_refusal_is_not_argued_with_and_is_shown_failing(self) -> None:
        engine = self.scripted(DOLLARS, DOLLARS, "I could not convert that.")
        asked = support.played_tools(self, {"currency": RATE})
        stream, trace = await self.traced(EUROS, engine)
        # Choose, try again, answer. Never a third choice.
        self.assertEqual(len(engine.passes), 3)
        self.assertEqual(
            shape(stream),
            ["step:route:running", "step:route:ok", "step:tool-0:running", "step:tool-0:failed",
             "card", *ANSWER],
        )
        self.assertEqual(support.step(stream, "tool-0", "failed")["detail"], "bad input")
        self.assertEqual(
            only(stream, "card")[0]["data"],
            {"message": "Currency does not know 'dollars' as a currency to convert from."},
        )
        self.assertEqual(asked["currency"], [])
        self.assertEqual(
            [(s.kind, s.ok) for s in trace.steps],
            [("route", False), ("repair", False), ("tool", False), ("answer", True)],
        )
        self.assertEqual(trace.steps[1].error, "rejected: " + REASON)

    async def test_a_second_try_that_gives_up_goes_on_to_answer_without_a_tool(self) -> None:
        engine = self.scripted(DOLLARS, NONE, "I am not sure.")
        asked = support.played_tools(self, {"currency": RATE})
        stream, trace = await self.traced(EUROS, engine)
        self.assertEqual(shape(stream), ["step:route:running", "step:route:ok", *ANSWER])
        self.assertEqual(support.step(stream, "route", "ok")["label"], "No tool needed")
        self.assertEqual(asked["currency"], [])
        self.assertEqual([s.kind for s in trace.steps], ["route", "repair", "answer"])
        self.assertEqual((trace.steps[1].name, trace.steps[1].ok), (None, True))

    async def test_a_call_that_can_be_made_is_not_second_guessed(self) -> None:
        engine = self.scripted(USD, "About 92 euros.")
        support.played_tools(self, {"currency": RATE})
        _, trace = await self.traced(EUROS, engine)
        self.assertEqual(len(engine.passes), 2)
        self.assertEqual([s.kind for s in trace.steps], ["route", "tool", "answer"])

    async def test_only_a_choice_the_model_made_is_ever_sent_back(self) -> None:
        # A person typed this one. It is refused where they can see, and no
        # pass is spent asking the model about it.
        engine = self.scripted("I could not convert that.")
        asked = support.played_tools(self, {"currency": RATE})
        stream, trace = await self.traced("/fx 5 xyz eur", engine, persona="explainer")
        self.assertEqual(len(engine.passes), 1)
        self.assertEqual(support.step(stream, "tool-0", "failed")["detail"], "bad input")
        self.assertEqual(asked["currency"], [])
        self.assertEqual([s.kind for s in trace.steps], ["route", "tool", "answer"])

    async def test_stopped_during_the_second_try_nothing_is_called(self) -> None:
        # The first pass is let through; the second is held inside the model.
        engine = self.scripted(DOLLARS, USD, "never said", gated=True, gate_from=1)
        asked = support.played_tools(self, {"currency": RATE})
        trace = agent.Trace()
        run = agent.run(EUROS, persona=get_persona("agent"), history=[], trace=trace,
                        engine=engine, request_id="run")
        reading = asyncio.create_task(collect(run))
        await engine.entered(2)
        engine.cancel("run")
        engine.release()
        stream = await reading
        self.assertEqual(
            shape(stream), ["step:route:running", "step:route:stopped", "done", "run"]
        )
        self.assertEqual(asked["currency"], [])
        self.assertEqual([(s.kind, s.error) for s in trace.steps][-1], ("repair", "stopped"))
        self.assertEqual(len(engine.passes), 2)


if __name__ == "__main__":
    unittest.main()
