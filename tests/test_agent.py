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
ANSWER = ["step:answer:running", "start", "token", "step:answer:ok", "done"]


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
        self.assertEqual(stream[-1], ("error", {"message": "RuntimeError: the dispatcher itself broke"}))
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

        self.assertEqual(shape(stream), ["step:route:running", "step:route:stopped", "done"])
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
            ["step:route:ok", "step:tool-0:running", "step:tool-0:stopped", "done"],
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
            ["step:route:ok", "step:tool-0:running", "step:tool-0:ok", "card", "done"],
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
            ["step:answer:running", "step:answer:ok", "done"],
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


if __name__ == "__main__":
    unittest.main()
