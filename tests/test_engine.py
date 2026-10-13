"""The one inference slot: who holds it, for how long, and who hands it back.

Needs the app's own requirements; without them the whole module is skipped.
It needs no model. The engine under test is the real one with a script where
llama.cpp would be, so the lock, the queue, the worker thread and the cancel
flag are the code the server runs.

Nothing here sleeps to let something happen. A test waits for the thing itself:
a pass reaching the model, a request joining the queue, the engine settling.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

try:
    from app.config import settings
    from app.llm import Engine
    from evals.scripted import ScriptedEngine
    from tests.support import collect, until
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

MESSAGES = [
    {"role": "system", "content": "Answer briefly."},
    {"role": "user", "content": "Count to eight."},
]
SAMPLING = {"temperature": 0.7, "top_p": 0.9, "top_k": 40, "repeat_penalty": 1.05, "max_tokens": 64}
EIGHT = "one two three four five six seven eight"


def names(stream: list) -> list[str]:
    """The events of a stream by name. A run of tokens is one word, and so is a
    run of queue notices: how many of either arrive is a matter of timing."""
    out: list[str] = []
    for name, _ in stream:
        if name in ("token", "queue") and out and out[-1] == name:
            continue
        out.append(name)
    return out


class EngineCase(unittest.IsolatedAsyncioTestCase):
    def scripted(self, *script, **options) -> ScriptedEngine:
        engine = ScriptedEngine(script, **options)
        engine.QUEUE_TICK = 0.01
        # No worker thread may outlive the loop it reports to.
        self.addAsyncCleanup(engine.settled)
        return engine


class OnePassTests(EngineCase):
    async def test_a_pass_is_start_then_its_tokens_then_done(self) -> None:
        engine = self.scripted(EIGHT)
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(names(stream), ["start", "token", "done"])
        self.assertEqual("".join(d["t"] for n, d in stream if n == "token"), EIGHT)

    async def test_done_has_the_text_and_the_figures(self) -> None:
        engine = self.scripted(EIGHT)
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        done = stream[-1][1]
        self.assertEqual(
            set(done),
            {"text", "cancelled", "ttft_ms", "queue_wait_ms", "prompt_tokens",
             "completion_tokens", "prefill_tps", "gen_tps", "total_ms", "rss_mb"},
        )
        self.assertEqual(done["text"], EIGHT)
        self.assertIs(done["cancelled"], False)
        self.assertEqual(done["completion_tokens"], 8)
        self.assertEqual(done["prompt_tokens"], engine.prompt_tokens(MESSAGES))

    async def test_the_slot_is_free_the_moment_the_stream_ends(self) -> None:
        engine = self.scripted(EIGHT, EIGHT)
        await collect(engine.stream_chat(MESSAGES, SAMPLING))
        # Checked without giving the loop another turn: the last event must not
        # arrive before the slot has been handed back.
        self.assertTrue(engine.free)
        self.assertEqual(engine.info()["completed"], 1)

    async def test_the_pass_is_given_its_grammar_seed_and_stop_words(self) -> None:
        engine = self.scripted('{"tool": "none"}')
        await collect(engine.stream_chat(
            MESSAGES, {"temperature": 0.0, "top_k": 1, "max_tokens": 96},
            grammar='root ::= "x"', seed=7, stop=["\n"],
        ))
        said = engine.passes[0]
        self.assertEqual(said.messages, MESSAGES)
        self.assertEqual(said.sampling, {"temperature": 0.0, "top_k": 1, "max_tokens": 96})
        self.assertEqual((said.grammar, said.seed, said.stop), ('root ::= "x"', 7, ["\n"]))

    async def test_a_model_that_raises_ends_the_stream_in_an_error_and_frees_the_slot(self) -> None:
        engine = self.scripted(ValueError("boom"))
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(names(stream), ["start", "error"])
        self.assertEqual(stream[-1][1], {"message": "ValueError: boom"})
        self.assertTrue(engine.free)

    async def test_an_engine_that_is_not_ready_says_so_and_takes_no_slot(self) -> None:
        engine = self.scripted(EIGHT)
        engine.state.status = "loading"
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(
            stream,
            [("error", {"message": "The model is still loading. Give it a moment, then send again."})],
        )
        self.assertEqual(engine.passes, [])
        self.assertEqual(engine._cancels, {})

    async def test_an_engine_that_failed_to_load_says_why(self) -> None:
        engine = self.scripted(EIGHT)
        engine.state.status = "error"
        engine.state.error = "The model file is missing on the server."
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(stream, [("error", {"message": "The model file is missing on the server."})])


class QueueTests(EngineCase):
    async def test_a_second_pass_waits_its_turn_and_is_told_its_place(self) -> None:
        engine = self.scripted(EIGHT, "nine ten", gated=True)
        first = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await engine.entered(1)

        seen: list = []

        async def read() -> None:
            async for item in engine.stream_chat(MESSAGES, SAMPLING):
                seen.append(item)

        second = asyncio.create_task(read())
        await until(lambda: any(name == "queue" for name, _ in seen))
        self.assertEqual(seen[0], ("queue", {"position": 0, "first": True}))
        self.assertEqual(engine.info()["queued"], 1)
        self.assertIs(engine.info()["busy"], True)
        self.assertEqual(len(engine.passes), 1)  # the second has not reached the model

        engine.release()
        await first
        await second
        self.assertEqual(names(seen), ["queue", "start", "token", "done"])
        self.assertGreater(seen[-1][1]["queue_wait_ms"], 0)
        self.assertEqual(engine.info()["queued"], 0)
        self.assertEqual(engine.most_at_once, 1)

    async def test_a_later_notice_is_not_marked_as_the_first(self) -> None:
        engine = self.scripted(EIGHT, "nine ten", gated=True)
        first = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await engine.entered(1)
        seen: list = []

        async def read() -> None:
            async for item in engine.stream_chat(MESSAGES, SAMPLING):
                seen.append(item)

        second = asyncio.create_task(read())
        await until(lambda: sum(name == "queue" for name, _ in seen) >= 2)
        self.assertIs(seen[1][1]["first"], False)
        engine.release()
        await first
        await second

    async def test_however_many_ask_at_once_only_one_is_ever_inside_the_model(self) -> None:
        engine = self.scripted(*[EIGHT] * 6, delay=0.001)
        streams = await asyncio.gather(
            *[collect(engine.stream_chat(MESSAGES, SAMPLING)) for _ in range(6)]
        )
        self.assertEqual(engine.most_at_once, 1)
        self.assertEqual(len(engine.passes), 6)
        for stream in streams:
            self.assertEqual(stream[-1][0], "done")
        self.assertTrue(engine.free)

    async def test_a_request_cancelled_while_it_waits_gives_its_place_up(self) -> None:
        engine = self.scripted(EIGHT, "nine ten", gated=True)
        first = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await engine.entered(1)
        second = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(lambda: engine.info()["queued"] == 1)

        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertEqual(engine.info()["queued"], 0)
        self.assertEqual(len(engine._cancels), 1)  # the first, still writing

        engine.release()
        await first
        self.assertEqual(len(engine.passes), 1)
        self.assertTrue(engine.free)
        # The slot was not lost with the request that left: the next one runs.
        third = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(third[-1][1]["text"], "nine ten")


class LeavingTests(EngineCase):
    """A reader that goes away. The writing stops, and it is the thread that
    hands the slot back, once it has left the model."""

    async def test_a_reader_that_leaves_mid_answer_stops_the_writing(self) -> None:
        engine = self.scripted(EIGHT, delay=0.02)
        stream = engine.stream_chat(MESSAGES, SAMPLING)
        tokens = 0
        async for name, _ in stream:
            tokens += name == "token"
            if tokens == 2:
                break
        await stream.aclose()
        await engine.settled()
        self.assertLess(engine.passes[0].yielded, 8)
        self.assertEqual(engine._cancels, {})
        self.assertTrue(engine.free)

    async def test_a_reader_that_leaves_as_the_pass_starts_stops_the_writing_too(self) -> None:
        engine = self.scripted(EIGHT, delay=0.02)
        stream = engine.stream_chat(MESSAGES, SAMPLING)
        async for name, _ in stream:
            if name == "start":
                break
        await stream.aclose()
        await engine.settled()
        self.assertLess(engine.passes[0].yielded, 8)
        self.assertEqual(engine._cancels, {})

    async def test_the_slot_stays_taken_until_the_thread_has_left_the_model(self) -> None:
        engine = self.scripted(EIGHT, "nine ten", gated=True)
        reader = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await engine.entered(1)

        reader.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reader
        # The reader is gone and the thread is still inside the model.
        self.assertTrue(engine._lock.locked())
        self.assertIs(engine.info()["busy"], True)

        # Someone else asks. They wait; they do not join it in there.
        waiting = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(lambda: engine.info()["queued"] == 1)
        self.assertEqual(len(engine.passes), 1)

        engine.release()
        stream = await waiting
        self.assertEqual(stream[-1][1]["text"], "nine ten")
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)

    async def test_a_failure_after_the_slot_is_taken_and_before_the_thread_starts_frees_it(self) -> None:
        engine = self.scripted(EIGHT)
        with mock.patch.object(engine, "prompt_tokens", side_effect=RuntimeError("tokenizer broke")):
            with self.assertRaises(RuntimeError):
                await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertTrue(engine.free)
        self.assertEqual(engine.passes, [])
        self.assertEqual(engine._cancels, {})
        self.assertEqual(engine.info()["queued"], 0)
        # And the engine is not left unusable by it.
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(stream[-1][1]["text"], EIGHT)


class StopTests(EngineCase):
    async def test_a_pass_stopped_by_its_id_ends_in_done_marked_cancelled(self) -> None:
        engine = self.scripted(EIGHT, gated=True)
        reader = asyncio.create_task(
            collect(engine.stream_chat(MESSAGES, SAMPLING, request_id="abc"))
        )
        await engine.entered(1)
        self.assertIs(engine.cancel("abc"), True)
        engine.release()
        stream = await reader
        self.assertEqual(stream[0][1]["request_id"], "abc")
        done = stream[-1]
        self.assertEqual(done[0], "done")
        self.assertIs(done[1]["cancelled"], True)
        self.assertLess(done[1]["completion_tokens"], 8)
        self.assertTrue(engine.free)

    async def test_an_id_nobody_is_running_cannot_be_stopped(self) -> None:
        engine = self.scripted(EIGHT)
        self.assertIs(engine.cancel("abc"), False)
        await collect(engine.stream_chat(MESSAGES, SAMPLING, request_id="abc"))
        self.assertIs(engine.cancel("abc"), False)

    async def test_a_pass_stopped_while_it_waits_gives_its_place_up_having_read_nothing(self) -> None:
        engine = self.scripted(EIGHT, gated=True)
        first = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await engine.entered(1)
        second = asyncio.create_task(
            collect(engine.stream_chat(MESSAGES, SAMPLING, request_id="waiting"))
        )
        await until(lambda: engine.info()["queued"] == 1)

        self.assertIs(engine.cancel("waiting"), True)
        stream = await second  # it ends by itself; the first is still held
        # Whether a notice of its place got out first is a matter of timing.
        self.assertEqual([n for n in names(stream) if n != "queue"], ["done"])
        done = stream[-1][1]
        self.assertIs(done["cancelled"], True)
        self.assertEqual((done["text"], done["completion_tokens"]), ("", 0))
        self.assertIsNone(done["prompt_tokens"])
        self.assertIsNone(done["total_ms"])
        self.assertGreater(done["queue_wait_ms"], 0)
        self.assertEqual(engine.info()["queued"], 0)
        self.assertEqual(len(engine.passes), 1)

        engine.release()
        self.assertEqual((await first)[-1][1]["text"], EIGHT)
        self.assertTrue(engine.free)


class HoldTests(EngineCase):
    """A run is several passes with other work in between. Its id is held open
    around all of it, so Stop finds the run and not just whichever pass is on."""

    async def test_a_held_id_can_be_stopped_when_no_pass_is_running(self) -> None:
        engine = self.scripted()
        self.assertIs(engine.cancel("run"), False)
        with engine.hold("run") as stop:
            self.assertIs(stop.is_set(), False)
            self.assertIs(engine.cancel("run"), True)
            self.assertIs(stop.is_set(), True)
        self.assertIs(engine.cancel("run"), False)
        self.assertEqual(engine._cancels, {})

    async def test_a_pass_that_ends_leaves_the_run_held_and_unstopped(self) -> None:
        engine = self.scripted(NONE, EIGHT)
        with engine.hold("run") as stop:
            await engine.complete_json(MESSAGES, GRAMMAR, request_id="run")
            # One pass ending is not somebody pressing Stop.
            self.assertIs(stop.is_set(), False)
            self.assertIn("run", engine._cancels)
            stream = await collect(engine.stream_chat(MESSAGES, SAMPLING, request_id="run"))
            self.assertIs(stream[-1][1]["cancelled"], False)
            self.assertEqual(stream[-1][1]["text"], EIGHT)

    async def test_a_run_stopped_between_passes_starts_no_other(self) -> None:
        engine = self.scripted(NONE, EIGHT)
        with engine.hold("run"):
            await engine.complete_json(MESSAGES, GRAMMAR, request_id="run")
            engine.cancel("run")
            stream = await collect(engine.stream_chat(MESSAGES, SAMPLING, request_id="run"))
        self.assertEqual(names(stream), ["done"])
        self.assertIs(stream[-1][1]["cancelled"], True)
        self.assertEqual(len(engine.passes), 1)
        self.assertTrue(engine.free)

    async def test_a_hold_inside_a_hold_is_the_same_stop_and_the_outer_one_owns_it(self) -> None:
        engine = self.scripted()
        with engine.hold("run") as outer:
            with engine.hold("run") as inner:
                self.assertIs(inner, outer)
            self.assertIn("run", engine._cancels)
        self.assertNotIn("run", engine._cancels)

    async def test_a_stop_can_be_waited_for(self) -> None:
        engine = self.scripted()
        with engine.hold("run") as stop:
            waiting = asyncio.create_task(stop.wait())
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            engine.cancel("run")
            await asyncio.wait_for(waiting, 5)

    async def test_the_hold_ends_even_if_the_run_inside_it_raises(self) -> None:
        engine = self.scripted()
        with self.assertRaises(RuntimeError):
            with engine.hold("run"):
                raise RuntimeError("the run broke")
        self.assertEqual(engine._cancels, {})


GRAMMAR = 'root ::= "{\\"tool\\": \\"none\\"}"'
NONE = '{"tool": "none"}'
WEATHER = '{"tool": "weather", "args": {"location": "Paris"}}'


class RoutingPassTests(EngineCase):
    """The short pass that chooses a tool. It is a pass like any other: it
    queues, it runs on a worker thread, and the thread hands the slot back."""

    async def test_a_routing_pass_returns_what_was_said_parsed_with_what_it_cost(self) -> None:
        engine = self.scripted(WEATHER)
        said = await engine.complete_json(MESSAGES, GRAMMAR)
        self.assertEqual(said.parsed, {"tool": "weather", "args": {"location": "Paris"}})
        self.assertEqual(said.raw, WEATHER)
        self.assertIsNone(said.error)
        self.assertIs(said.cancelled, False)
        self.assertEqual(said.prompt_tokens, engine.prompt_tokens(MESSAGES))
        self.assertEqual(said.completion_tokens, len(engine.passes[0].pieces))
        self.assertIsNotNone(said.ttft_ms)
        # The whole call is never shorter than the part spent inside the slot.
        self.assertGreaterEqual(said.ms, said.total_ms)

    async def test_it_is_asked_greedily_under_its_grammar_within_the_routing_limit(self) -> None:
        engine = self.scripted(NONE, NONE)
        await engine.complete_json(MESSAGES, GRAMMAR)
        asked = engine.passes[0]
        self.assertEqual(asked.grammar, GRAMMAR)
        self.assertEqual(
            asked.sampling,
            {"temperature": 0.0, "top_k": 1, "max_tokens": engine.cfg.route_max_tokens},
        )
        await engine.complete_json(MESSAGES, GRAMMAR, 32, seed=3)
        self.assertEqual(engine.passes[1].sampling["max_tokens"], 32)
        self.assertEqual(engine.passes[1].seed, 3)

    async def test_a_routing_pass_that_ends_leaves_the_engine_free_and_not_busy(self) -> None:
        engine = self.scripted(NONE)
        await engine.complete_json(MESSAGES, GRAMMAR)
        self.assertTrue(engine.free)
        self.assertIs(engine.info()["busy"], False)
        self.assertEqual(engine.info()["completed"], 1)

    async def test_a_cancelled_routing_pass_keeps_the_slot_until_its_thread_has_left(self) -> None:
        engine = self.scripted(WEATHER, EIGHT, gated=True)
        choosing = asyncio.create_task(engine.complete_json(MESSAGES, GRAMMAR))
        await engine.entered(1)

        # The visitor closes the page while the model is choosing.
        choosing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await choosing
        self.assertTrue(engine._lock.locked())
        self.assertIs(engine.info()["busy"], True)

        # The next request arrives. It waits; it does not enter the model
        # while the first thread is still in there.
        answer = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(lambda: engine.info()["queued"] == 1)
        self.assertEqual(len(engine.passes), 1)

        engine.release()
        stream = await answer
        self.assertEqual(stream[-1][1]["text"], EIGHT)
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)
        self.assertIs(engine.info()["busy"], False)

    async def test_a_routing_pass_waits_in_the_same_queue_as_an_answer(self) -> None:
        engine = self.scripted(EIGHT, NONE, gated=True)
        writing = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await engine.entered(1)

        seen: list = []

        async def choose() -> None:
            async for item in engine.stream_json(MESSAGES, GRAMMAR):
                seen.append(item)

        choosing = asyncio.create_task(choose())
        await until(lambda: any(name == "queue" for name, _ in seen))
        self.assertEqual(engine.info()["queued"], 1)

        engine.release()
        await writing
        await choosing
        self.assertEqual(names(seen), ["queue", "completion"])
        said = seen[-1][1]
        self.assertEqual(said.parsed, {"tool": "none"})
        self.assertGreater(said.queue_wait_ms, 0)
        self.assertGreaterEqual(said.ms, said.queue_wait_ms)
        self.assertEqual(engine.most_at_once, 1)

    async def test_a_reply_that_is_not_json_is_kept_raw_and_parsed_as_nothing(self) -> None:
        cut = '{"tool": "wikipedia", "args": {"query": "the long'
        for raw in (cut, '["a", "list"]', "42", ""):
            with self.subTest(raw=raw):
                engine = self.scripted(raw)
                said = await engine.complete_json(MESSAGES, GRAMMAR)
                self.assertIsNone(said.parsed)
                self.assertEqual(said.raw, raw)
                self.assertIsNone(said.error)

    async def test_a_model_that_raises_is_no_decision_and_never_an_exception(self) -> None:
        engine = self.scripted(ValueError("boom"))
        said = await engine.complete_json(MESSAGES, GRAMMAR)
        self.assertIsNone(said.parsed)
        self.assertEqual(said.error, "ValueError: boom")
        self.assertIsNone(said.prompt_tokens)
        self.assertTrue(engine.free)

    async def test_a_failure_on_the_way_in_is_no_decision_either(self) -> None:
        engine = self.scripted(NONE)
        with mock.patch.object(engine, "prompt_tokens", side_effect=RuntimeError("tokenizer broke")):
            said = await engine.complete_json(MESSAGES, GRAMMAR)
        self.assertEqual(said.error, "RuntimeError: tokenizer broke")
        self.assertTrue(engine.free)

    async def test_an_engine_that_is_not_ready_makes_no_decision(self) -> None:
        engine = self.scripted(NONE)
        engine.state.status = "loading"
        said = await engine.complete_json(MESSAGES, GRAMMAR)
        self.assertIsNone(said.parsed)
        self.assertIn("still loading", said.error)
        self.assertEqual(engine.passes, [])

    async def test_a_routing_pass_can_be_stopped_by_its_id(self) -> None:
        engine = self.scripted(WEATHER, gated=True)
        choosing = asyncio.create_task(engine.complete_json(MESSAGES, GRAMMAR, request_id="abc"))
        await engine.entered(1)
        self.assertIs(engine.cancel("abc"), True)
        engine.release()
        said = await choosing
        self.assertIs(said.cancelled, True)
        self.assertIsNone(said.parsed)
        self.assertTrue(engine.free)


class MockModeTests(unittest.IsolatedAsyncioTestCase):
    """Without a model, a decision is always "no tool", made through the slot."""

    async def test_the_mock_decides_none_as_a_real_pass(self) -> None:
        engine = Engine(settings.model_copy(update={"mock": True, "mock_delay": 0.0}))
        engine.state.status = "ready"
        said = await engine.complete_json(MESSAGES, GRAMMAR)
        self.assertEqual(said.parsed, {"tool": "none"})
        self.assertEqual(said.completion_tokens, 1)
        self.assertEqual(engine.info()["completed"], 1)
        self.assertIs(engine.info()["busy"], False)
        self.assertFalse(engine._lock.locked())


async def take(engine, request_id: str) -> dict:
    """Take the slot for a run. Returns what the engine said about it."""
    said = {}
    async for name, payload in engine.take_slot(request_id):
        if name == "slot":
            said = payload
    return said


class HeldSlotTests(EngineCase):
    """A run of several passes takes the slot once and keeps it between them.

    The rule the rest of this file is about still holds: while a thread is
    inside the model the slot is taken, whoever lets go of what.
    """

    async def test_taking_the_slot_takes_it_and_says_how_long_the_wait_was(self) -> None:
        engine = self.scripted()
        said = await take(engine, "run")
        self.assertIs(said["held"], True)
        self.assertGreaterEqual(said["queue_wait_ms"], 0)
        self.assertTrue(engine._lock.locked())
        self.assertIs(engine.info()["busy"], True)
        self.assertEqual(engine.info()["queued"], 0)
        engine.give_slot("run")
        self.assertTrue(engine.free)

    async def test_the_passes_of_the_run_do_not_wait_and_do_not_give_it_back(self) -> None:
        engine = self.scripted(NONE, EIGHT)
        await take(engine, "run")
        said = await engine.complete_json(MESSAGES, GRAMMAR, request_id="run", held=True)
        self.assertEqual(said.queue_wait_ms, 0.0)
        # Between two passes of the run the slot is still the run's.
        self.assertTrue(engine._lock.locked())
        self.assertIs(engine.info()["busy"], True)
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True))
        self.assertEqual(names(stream), ["start", "token", "done"])
        self.assertEqual(stream[-1][1]["queue_wait_ms"], 0.0)
        self.assertTrue(engine._lock.locked())
        self.assertEqual(engine.info()["completed"], 2)
        engine.give_slot("run")
        self.assertTrue(engine.free)

    async def test_another_request_waits_for_the_whole_run_not_for_one_pass(self) -> None:
        engine = self.scripted(NONE, EIGHT, "nine ten")
        await take(engine, "run")
        other = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(lambda: engine.info()["queued"] == 1)

        await engine.complete_json(MESSAGES, GRAMMAR, request_id="run", held=True)
        await collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True))
        # Two passes of the run have come and gone, and the other is still waiting.
        self.assertFalse(other.done())
        self.assertEqual(len(engine.passes), 2)

        engine.give_slot("run")
        stream = await other
        self.assertEqual(stream[-1][1]["text"], "nine ten")
        self.assertGreater(stream[-1][1]["queue_wait_ms"], 0)
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)

    async def test_a_second_run_queues_behind_the_first_and_is_told_its_place(self) -> None:
        engine = self.scripted()
        await take(engine, "first")
        seen: list = []

        async def wait_for_it() -> None:
            async for item in engine.take_slot("second"):
                seen.append(item)

        second = asyncio.create_task(wait_for_it())
        await until(lambda: any(name == "queue" for name, _ in seen))
        self.assertEqual(seen[0], ("queue", {"position": 0, "first": True}))
        self.assertIs(engine.others_waiting(), True)

        engine.give_slot("first")
        await second
        self.assertEqual(seen[-1][0], "slot")
        self.assertIs(seen[-1][1]["held"], True)
        self.assertGreater(seen[-1][1]["queue_wait_ms"], 0)
        self.assertIs(engine.others_waiting(), False)
        engine.give_slot("second")
        self.assertTrue(engine.free)

    async def test_letting_go_while_a_thread_is_inside_frees_nothing_until_it_leaves(self) -> None:
        engine = self.scripted(EIGHT, "nine ten", gated=True)
        await take(engine, "run")
        reader = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True)))
        await engine.entered(1)

        # The run goes away mid-pass: its reader is cancelled and it lets go.
        reader.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reader
        engine.give_slot("run")
        self.assertTrue(engine._lock.locked())
        self.assertIs(engine.info()["busy"], True)

        # Somebody else asks. They wait; they do not join the thread in there.
        other = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(lambda: engine.info()["queued"] == 1)
        self.assertEqual(len(engine.passes), 1)

        engine.release()
        stream = await other
        self.assertEqual(stream[-1][1]["text"], "nine ten")
        self.assertEqual(engine.most_at_once, 1)
        self.assertTrue(engine.free)

    async def test_a_reader_that_leaves_a_pass_does_not_cost_the_run_its_slot(self) -> None:
        engine = self.scripted(EIGHT, "nine ten", gated=True)
        await take(engine, "run")
        reader = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True)))
        await engine.entered(1)
        reader.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await reader
        engine.release()
        await until(lambda: not engine._inside)
        # The thread is out, and the slot is still with the run that took it.
        self.assertTrue(engine._lock.locked())
        self.assertEqual(engine._holder, "run")
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True))
        self.assertEqual(stream[-1][1]["text"], "nine ten")
        engine.give_slot("run")
        self.assertTrue(engine.free)

    async def test_a_failure_on_the_way_into_a_pass_leaves_the_slot_with_the_run(self) -> None:
        engine = self.scripted(EIGHT)
        await take(engine, "run")
        with mock.patch.object(engine, "prompt_tokens", side_effect=RuntimeError("tokenizer broke")):
            with self.assertRaises(RuntimeError):
                await collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True))
        self.assertTrue(engine._lock.locked())
        self.assertEqual(engine._holder, "run")
        engine.give_slot("run")
        self.assertTrue(engine.free)

    async def test_a_run_stopped_while_it_waits_for_the_slot_does_not_get_it(self) -> None:
        engine = self.scripted()
        await take(engine, "first")
        waiting = asyncio.create_task(take(engine, "second"))
        await until(lambda: engine.info()["queued"] == 1)
        self.assertIs(engine.cancel("second"), True)
        said = await asyncio.wait_for(waiting, 5)
        self.assertIs(said["held"], False)
        self.assertEqual(engine.info()["queued"], 0)
        self.assertEqual(engine._holder, "first")
        engine.give_slot("first")
        self.assertTrue(engine.free)

    async def test_a_run_cancelled_while_it_waits_for_the_slot_gives_its_place_up(self) -> None:
        engine = self.scripted(EIGHT)
        await take(engine, "first")
        waiting = asyncio.create_task(take(engine, "second"))
        await until(lambda: engine.info()["queued"] == 1)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertEqual(engine.info()["queued"], 0)
        engine.give_slot("first")
        self.assertTrue(engine.free)
        # Nothing was lost with it: the next request runs.
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(stream[-1][1]["text"], EIGHT)

    async def test_only_the_run_that_holds_the_slot_can_give_it_back(self) -> None:
        engine = self.scripted()
        await take(engine, "run")
        engine.give_slot("somebody-else")
        self.assertTrue(engine._lock.locked())
        engine.give_slot("run")
        self.assertTrue(engine.free)
        engine.give_slot("run")  # twice is nothing
        self.assertTrue(engine.free)

    async def test_taking_a_slot_already_held_by_the_run_is_immediate(self) -> None:
        engine = self.scripted()
        await take(engine, "run")
        said = await asyncio.wait_for(take(engine, "run"), 5)
        self.assertEqual(said, {"held": True, "queue_wait_ms": 0.0})
        engine.give_slot("run")
        self.assertTrue(engine.free)

    async def test_a_pass_that_claims_a_slot_its_run_does_not_hold_waits_like_any_other(self) -> None:
        engine = self.scripted(EIGHT, "nine ten")
        # Nobody holds the slot: the pass takes it and gives it back itself.
        await collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True))
        self.assertTrue(engine.free)
        # Another run holds it: the pass queues behind that run.
        await take(engine, "other")
        reader = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True)))
        await until(lambda: engine.info()["queued"] == 1)
        self.assertEqual(len(engine.passes), 1)
        engine.give_slot("other")
        self.assertEqual((await reader)[-1][1]["text"], "nine ten")
        self.assertTrue(engine.free)

    async def test_whether_anybody_is_waiting_can_be_asked(self) -> None:
        engine = self.scripted(EIGHT)
        self.assertIs(engine.others_waiting(), False)
        await take(engine, "run")
        self.assertIs(engine.others_waiting(), False)
        other = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(engine.others_waiting)
        engine.give_slot("run")
        await other
        self.assertIs(engine.others_waiting(), False)

    async def test_giving_the_slot_up_between_passes_lets_one_request_in_and_then_waits_its_turn(self) -> None:
        engine = self.scripted(NONE, "nine ten", EIGHT)
        await take(engine, "run")
        await engine.complete_json(MESSAGES, GRAMMAR, request_id="run", held=True)
        other = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(engine.others_waiting)

        # As a run does while a tool of its own is out on the network.
        engine.give_slot("run")
        again = await take(engine, "run")
        self.assertIs(again["held"], True)
        self.assertTrue(other.done())  # the other went first
        self.assertEqual(other.result()[-1][1]["text"], "nine ten")
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING, "run", held=True))
        self.assertEqual(stream[-1][1]["text"], EIGHT)
        self.assertEqual(engine.most_at_once, 1)
        engine.give_slot("run")
        self.assertTrue(engine.free)


class ScriptTests(EngineCase):
    """The stand-in itself: it must say what it was given, exactly."""

    async def test_the_pieces_of_an_output_put_back_together_are_the_output(self) -> None:
        for text in ("one two", "  leading", "trailing  ", "a\n\nb", '{"tool": "none"}', ""):
            with self.subTest(text=text):
                engine = self.scripted(text)
                stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
                self.assertEqual(stream[-1][1]["text"], text)

    async def test_a_pass_the_script_has_nothing_for_fails_out_loud(self) -> None:
        engine = self.scripted()
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(stream[-1], ("error", {"message": "IndexError: the script has nothing for pass 1"}))
        self.assertTrue(engine.free)

    async def test_the_prompt_of_a_pass_is_kept_as_the_engine_counts_it(self) -> None:
        engine = self.scripted(EIGHT)
        await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(
            engine.passes[0].prompt,
            "<|im_start|>system\nAnswer briefly.<|im_end|>\n"
            "<|im_start|>user\nCount to eight.<|im_end|>\n"
            "<|im_start|>assistant\n",
        )


class FakeLlama:
    """Stands where llama.cpp would, and keeps what it was asked for."""

    def __init__(self) -> None:
        self.asked: list[dict] = []

    def create_chat_completion(self, **kwargs):
        self.asked.append(kwargs)
        return iter([
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {"content": " there"}}]},
            {"choices": [{"delta": {}}]},
        ])


class ModelCallTests(unittest.TestCase):
    """What llama.cpp is actually asked for. No thread, no loop: the one
    function that makes the call, called directly."""

    def setUp(self) -> None:
        self.engine = Engine(settings.model_copy(update={"mock": False}))
        self.llama = FakeLlama()
        self.engine._llm = self.llama

    def test_an_answer_is_asked_for_with_its_five_sampling_settings_and_nothing_else(self) -> None:
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING))
        self.assertEqual(said, ["Hi", " there"])
        self.assertEqual(self.llama.asked, [{"messages": MESSAGES, "stream": True, **SAMPLING}])

    def test_only_the_settings_a_pass_names_are_passed_on(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, {"temperature": 0.0, "top_k": 1, "max_tokens": 96}))
        self.assertEqual(
            self.llama.asked,
            [{"messages": MESSAGES, "stream": True, "temperature": 0.0, "top_k": 1, "max_tokens": 96}],
        )

    def test_a_seed_and_stop_words_are_passed_only_when_given(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, seed=0, stop=["</s>"]))
        self.assertEqual(self.llama.asked[0]["seed"], 0)
        self.assertEqual(self.llama.asked[0]["stop"], ["</s>"])
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=[]))
        self.assertNotIn("seed", self.llama.asked[1])
        self.assertNotIn("stop", self.llama.asked[1])

    def test_a_grammar_is_compiled_once_and_passed_compiled(self) -> None:
        compiled = object()
        with mock.patch.object(self.engine, "_grammar", return_value=compiled) as compile_:
            list(self.engine._iter_chunks(MESSAGES, SAMPLING, grammar='root ::= "x"'))
        compile_.assert_called_once_with('root ::= "x"')
        self.assertIs(self.llama.asked[0]["grammar"], compiled)


if __name__ == "__main__":
    unittest.main()
