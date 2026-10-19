"""The one inference slot: who holds it, for how long, and who hands it back.

Needs the app's own requirements; without them the whole module is skipped.
It needs no model. The engine under test is the real one with a script where
llama.cpp would be, so the lock, the queue, the worker thread and the cancel
flag are the code the server runs.

Nothing here sleeps to let something happen. A test waits for the thing itself:
a pass reaching the model, a request joining the queue, the engine settling.

Further down: how a pass ends (at a stop string, at its limit, or where the
model ends it), and what each place the model can live in is asked for.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

try:
    from app.config import settings
    from app.llm import Engine, Stops
    from app.llm.mock import MOCK_THOUGHT, MOCK_VOCAB
    from app.llm.server import Reading, ServerError
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
# MESSAGES as the model reads them, up to where the answer begins.
HEAD = (
    "<|im_start|>system\nAnswer briefly.<|im_end|>\n"
    "<|im_start|>user\nCount to eight.<|im_end|>\n"
    "<|im_start|>assistant\n"
)


def names(stream: list) -> list[str]:
    """The events of a stream by name. A run of tokens is one word, and so is a
    run of queue notices: how many of either arrive is a matter of timing."""
    out: list[str] = []
    for name, _ in stream:
        if name in ("token", "queue") and out and out[-1] == name:
            continue
        out.append(name)
    return out


async def answer(engine, *args, **options) -> dict:
    """One pass through ``stream_chat``, read to its end: what ``done`` said."""
    stream = await collect(engine.stream_chat(*args, **options))
    name, payload = stream[-1]
    if name != "done":
        raise AssertionError(f"the pass ended in {name}: {payload}")
    return payload


def thinking(mode: str, **update):
    """The settings of a model that thinks the way ``mode`` says, in mock mode."""
    return settings.model_copy(update={"mock": True, "mock_delay": 0.0, "thinking": mode, **update})


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
            {"text", "cancelled", "ttft_ms", "queue_wait_ms", "prompt_tokens", "read_tokens",
             "cached_tokens", "completion_tokens", "prefill_tps", "gen_tps", "total_ms", "rss_mb",
             "finish", "word"},
        )
        self.assertEqual(done["text"], EIGHT)
        self.assertIs(done["cancelled"], False)
        self.assertEqual(done["completion_tokens"], 8)
        self.assertEqual(done["prompt_tokens"], engine.prompt_tokens(MESSAGES))
        # The model ended it, at no stop string.
        self.assertEqual((done["finish"], done["word"]), ("eos", None))

    async def test_the_slot_is_free_the_moment_the_stream_ends(self) -> None:
        engine = self.scripted(EIGHT, EIGHT)
        await collect(engine.stream_chat(MESSAGES, SAMPLING))
        # Checked without giving the loop another turn: the last event must not
        # arrive before the slot has been handed back.
        self.assertTrue(engine.free)
        self.assertIs(engine.info()["busy"], False)
        self.assertEqual(engine.info()["completed"], 1)

    async def test_the_pass_is_given_its_prefix_seed_and_stop_words(self) -> None:
        engine = self.scripted("three four")
        done = await answer(
            engine, MESSAGES, {"temperature": 0.0, "top_k": 1, "max_tokens": 96},
            prefix="one two ", seed=7, stop=["\n"],
        )
        said = engine.passes[0]
        self.assertEqual(said.messages, MESSAGES)
        self.assertEqual(said.sampling, {"temperature": 0.0, "top_k": 1, "max_tokens": 96})
        self.assertEqual((said.prefix, said.seed, said.stop), ("one two ", 7, ["\n"]))
        self.assertIs(said.continued, False)
        # What is already written is part of the prompt, and is counted as such.
        self.assertEqual(said.prompt, engine.render(MESSAGES, "one two "))
        self.assertEqual(done["prompt_tokens"], engine.prompt_tokens(MESSAGES, "one two "))
        self.assertGreater(done["prompt_tokens"], engine.prompt_tokens(MESSAGES))

    async def test_a_pass_given_no_prefix_seed_or_stop_words_asks_for_none(self) -> None:
        engine = self.scripted(EIGHT)
        await answer(engine, MESSAGES, SAMPLING)
        said = engine.passes[0]
        self.assertEqual((said.prefix, said.seed, said.stop), ("", None, None))

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
        self.assertEqual(done[1]["finish"], "cancelled")
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


class EndingTests(EngineCase):
    """How a pass ends, and how ``done`` says so. A reply can be written in
    more than one pass: one ends at a stop string, and the next goes on from
    what was written."""

    async def test_a_pass_ends_at_a_stop_string_and_says_which(self) -> None:
        engine = self.scripted("one two STOP three four")
        done = await answer(engine, MESSAGES, SAMPLING, stop=["STOP"])
        # The string itself is not passed on, nor anything after it.
        self.assertEqual(done["text"], "one two ")
        self.assertEqual((done["finish"], done["word"]), ("word", "STOP"))
        self.assertEqual(done["completion_tokens"], 2)
        self.assertTrue(engine.free)

    async def test_the_next_pass_goes_on_from_the_stop_with_what_was_written_as_its_prefix(self) -> None:
        engine = self.scripted("one two STOP three four", EIGHT)
        first = await answer(engine, MESSAGES, SAMPLING, stop=["STOP"])
        second = await answer(engine, MESSAGES, SAMPLING, prefix=first["text"] + "STOP")
        self.assertEqual(second["text"], " three four")
        self.assertEqual((second["finish"], second["word"]), ("eos", None))
        went_on = engine.passes[1]
        self.assertIs(went_on.continued, True)
        self.assertEqual(went_on.prefix, "one two STOP")
        self.assertEqual(engine.used, 1, "one message of the model, written in two requests")
        # And the one after that is the model's next message.
        self.assertEqual((await answer(engine, MESSAGES, SAMPLING))["text"], EIGHT)
        self.assertIs(engine.passes[2].continued, False)

    async def test_the_earliest_of_several_stop_strings_is_the_one_that_ends_it(self) -> None:
        engine = self.scripted("a b <x> c </y> d")
        done = await answer(engine, MESSAGES, SAMPLING, stop=["</y>", "<x>"])
        self.assertEqual((done["text"], done["word"]), ("a b ", "<x>"))

    async def test_a_stop_string_at_the_very_end_leaves_nothing_to_go_on_with(self) -> None:
        engine = self.scripted("Done.</tool_call>", EIGHT)
        done = await answer(engine, MESSAGES, SAMPLING, stop=["</tool_call>"])
        self.assertEqual((done["text"], done["finish"], done["word"]), ("Done.", "word", "</tool_call>"))
        self.assertEqual((await answer(engine, MESSAGES, SAMPLING))["text"], EIGHT)
        self.assertIs(engine.passes[1].continued, False)

    async def test_a_pass_given_stop_strings_it_never_meets_ends_where_the_model_does(self) -> None:
        engine = self.scripted(EIGHT)
        done = await answer(engine, MESSAGES, SAMPLING, stop=["STOP", "</tool_call>"])
        self.assertEqual((done["text"], done["finish"], done["word"]), (EIGHT, "eos", None))

    async def test_a_pass_cut_off_at_its_token_limit_says_so_and_its_rest_is_gone(self) -> None:
        engine = self.scripted(EIGHT, "nine ten")
        done = await answer(engine, MESSAGES, {**SAMPLING, "max_tokens": 3})
        self.assertEqual(done["text"], "one two three ")
        self.assertEqual((done["finish"], done["word"]), ("limit", None))
        self.assertEqual(done["completion_tokens"], 3)
        # A reply cut off is not picked up again: the next pass is the next message.
        self.assertEqual((await answer(engine, MESSAGES, SAMPLING))["text"], "nine ten")

    async def test_a_limit_that_falls_before_a_stop_string_wins(self) -> None:
        engine = self.scripted("one two three STOP four", "nine ten")
        done = await answer(engine, MESSAGES, {**SAMPLING, "max_tokens": 2}, stop=["STOP"])
        self.assertEqual((done["text"], done["finish"], done["word"]), ("one two ", "limit", None))
        self.assertEqual((await answer(engine, MESSAGES, SAMPLING))["text"], "nine ten")


class ThinkingScriptTests(EngineCase):
    """Where the model thinks, its answer begins inside a thinking block. A
    script entry that has no thinking of its own thought nothing."""

    async def test_an_entry_without_thinking_closes_an_empty_block_first(self) -> None:
        engine = self.scripted("Hello.", cfg=thinking("on"))
        done = await answer(engine, MESSAGES, SAMPLING)
        self.assertEqual(done["text"], "</think>\n\nHello.")
        self.assertEqual(engine.passes[0].output, "</think>\n\nHello.")

    async def test_an_entry_that_thinks_is_said_as_it_is(self) -> None:
        engine = self.scripted("Counting.\n</think>\n\nOne.", cfg=thinking("on"))
        done = await answer(engine, MESSAGES, SAMPLING)
        self.assertEqual(done["text"], "Counting.\n</think>\n\nOne.")

    async def test_a_reply_that_goes_on_from_a_prefix_is_not_given_a_block(self) -> None:
        engine = self.scripted("written.", cfg=thinking("on"))
        done = await answer(engine, MESSAGES, SAMPLING, prefix="Partly ")
        self.assertEqual(done["text"], "written.")

    async def test_the_thought_ends_at_its_stop_and_the_answer_goes_on_after_it(self) -> None:
        engine = self.scripted("Counting.\n</think>\n\nOne.", cfg=thinking("on"))
        thought = await answer(engine, MESSAGES, SAMPLING, stop=["</think>"])
        self.assertEqual((thought["text"], thought["word"]), ("Counting.\n", "</think>"))
        said = await answer(engine, MESSAGES, SAMPLING, prefix=thought["text"] + "</think>")
        self.assertEqual(said["text"], "\n\nOne.")
        self.assertIs(engine.passes[1].continued, True)

    async def test_where_the_model_does_not_think_an_entry_is_said_as_it_is(self) -> None:
        for mode in ("off", "none"):
            with self.subTest(thinking=mode):
                engine = self.scripted("Hello.", cfg=thinking(mode))
                self.assertEqual((await answer(engine, MESSAGES, SAMPLING))["text"], "Hello.")


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
        engine = self.scripted("nine ten", EIGHT)
        with engine.hold("run") as stop:
            first = await answer(engine, MESSAGES, SAMPLING, request_id="run")
            self.assertEqual(first["finish"], "eos")
            # One pass ending is not somebody pressing Stop.
            self.assertIs(stop.is_set(), False)
            self.assertIn("run", engine._cancels)
            stream = await collect(engine.stream_chat(MESSAGES, SAMPLING, request_id="run"))
            self.assertIs(stream[-1][1]["cancelled"], False)
            self.assertEqual(stream[-1][1]["text"], EIGHT)

    async def test_a_pass_that_ends_at_a_stop_string_is_not_a_stop_either(self) -> None:
        engine = self.scripted("one two STOP three four")
        with engine.hold("run") as stop:
            first = await answer(engine, MESSAGES, SAMPLING, request_id="run", stop=["STOP"])
            self.assertEqual(first["finish"], "word")
            self.assertIs(first["cancelled"], False)
            self.assertIs(stop.is_set(), False)
            second = await answer(engine, MESSAGES, SAMPLING, request_id="run", prefix="one two STOP")
            self.assertEqual(second["text"], " three four")

    async def test_a_run_stopped_between_passes_starts_no_other(self) -> None:
        engine = self.scripted("nine ten", EIGHT)
        with engine.hold("run"):
            await answer(engine, MESSAGES, SAMPLING, request_id="run")
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


THOUGHT = " ".join(MOCK_THOUGHT)
VOCAB = " ".join(MOCK_VOCAB)
ROOM = {"max_tokens": 1000}


class MockModeTests(unittest.IsolatedAsyncioTestCase):
    """Without a model, a pass writes the same paragraph every time, through
    the slot like any other, and ends as a model's pass does."""

    def mocked(self, mode: str = "none") -> Engine:
        engine = Engine(thinking(mode))
        engine.load_blocking()
        return engine

    async def test_the_mock_writes_its_paragraph_as_a_real_pass(self) -> None:
        engine = self.mocked()
        self.assertTrue(engine.ready)
        stream = await collect(engine.stream_chat(MESSAGES, ROOM))
        self.assertEqual(names(stream), ["start", "token", "done"])
        done = stream[-1][1]
        self.assertEqual(done["text"], VOCAB)
        self.assertEqual((done["finish"], done["word"]), ("eos", None))
        self.assertEqual(done["completion_tokens"], len(MOCK_VOCAB))
        self.assertEqual(engine.info()["completed"], 1)
        self.assertIs(engine.info()["busy"], False)
        self.assertFalse(engine._lock.locked())

    async def test_where_the_model_thinks_it_thinks_first_and_closes_the_block(self) -> None:
        done = await answer(self.mocked("on"), MESSAGES, ROOM)
        self.assertEqual(done["text"], THOUGHT + "\n</think>\n\n" + VOCAB)

    async def test_where_the_model_does_not_think_there_is_no_thought(self) -> None:
        for mode in ("off", "none"):
            with self.subTest(thinking=mode):
                done = await answer(self.mocked(mode), MESSAGES, ROOM)
                self.assertEqual(done["text"], VOCAB)

    async def test_it_stops_at_a_stop_string_and_goes_on_from_its_prefix(self) -> None:
        engine = self.mocked("on")
        thought = await answer(engine, MESSAGES, ROOM, stop=["</think>"])
        self.assertEqual(thought["text"], THOUGHT + "\n")
        self.assertEqual((thought["finish"], thought["word"]), ("word", "</think>"))
        # Picked up after the block is closed, it does not think again.
        said = await answer(engine, MESSAGES, ROOM, prefix=thought["text"] + "</think>\n\n")
        self.assertEqual(said["text"], VOCAB)
        self.assertEqual(said["finish"], "eos")

    async def test_it_ends_at_its_token_limit(self) -> None:
        done = await answer(self.mocked(), MESSAGES, {"max_tokens": 5})
        self.assertEqual(done["text"], " ".join(MOCK_VOCAB[:5]))
        self.assertEqual((done["finish"], done["word"]), ("limit", None))
        self.assertEqual(done["completion_tokens"], 5)


class PromptTests(unittest.TestCase):
    """The prompt the engine writes: every message as it is, then the answer
    begun the way the model thinks, then whatever of it is already written."""

    def test_the_answer_begins_with_the_opener_of_how_the_model_thinks(self) -> None:
        for mode, opener in (("none", ""), ("off", "<think>\n\n</think>\n\n"), ("on", "<think>\n")):
            with self.subTest(thinking=mode):
                engine = Engine(thinking(mode))
                self.assertEqual(engine.render(MESSAGES), HEAD + opener)
                self.assertEqual(engine.render(MESSAGES, "So far"), HEAD + opener + "So far")

    def test_what_is_counted_is_the_prompt_as_written(self) -> None:
        engine = Engine(thinking("on"))
        with mock.patch.object(engine, "count_tokens", return_value=7) as count:
            self.assertEqual(engine.prompt_tokens(MESSAGES, "So far"), 7)
        count.assert_called_once_with(HEAD + "<think>\nSo far")


class StopsTests(unittest.TestCase):
    """The app's own reading of stop strings, for the model in this process,
    the mock and a script: what may be passed on, and when it has to wait."""

    def test_text_with_no_stop_string_in_it_is_passed_on_whole(self) -> None:
        stops = Stops(["</s>"])
        self.assertEqual(stops.feed("hello world"), "hello world")
        self.assertEqual(stops.flush(), "")
        self.assertIsNone(stops.word)

    def test_the_text_ends_before_the_stop_string_and_nothing_after_it_is_passed_on(self) -> None:
        stops = Stops(["</s>"])
        self.assertEqual(stops.feed("done</s> and more"), "done")
        self.assertEqual(stops.word, "</s>")
        self.assertEqual(stops.feed("later text"), "")
        self.assertEqual(stops.flush(), "")

    def test_a_tail_that_could_begin_a_stop_string_is_held_back(self) -> None:
        stops = Stops(["</think>"])
        self.assertEqual(stops.feed("so </th"), "so ")
        self.assertEqual(stops.feed("ink> the answer"), "")
        self.assertEqual(stops.word, "</think>")

    def test_a_held_tail_that_turns_out_not_to_be_one_is_passed_on(self) -> None:
        stops = Stops(["</think>"])
        self.assertEqual(stops.feed("a <"), "a ")
        self.assertEqual(stops.feed("b>"), "<b>")
        self.assertIsNone(stops.word)

    def test_a_stop_string_written_one_character_at_a_time_is_still_found(self) -> None:
        stops = Stops(["</tool_call>"])
        out = "".join(stops.feed(c) for c in "call it</tool_call>after")
        self.assertEqual(out, "call it")
        self.assertEqual(stops.word, "</tool_call>")

    def test_the_earliest_of_several_is_the_one_that_stops_it(self) -> None:
        for words in (["</tool_call>", "\n"], ["\n", "</tool_call>"]):
            with self.subTest(words=words):
                stops = Stops(words)
                self.assertEqual(stops.feed("x\ny</tool_call>"), "x")
                self.assertEqual(stops.word, "\n")

    def test_one_stop_string_found_while_the_start_of_another_is_held(self) -> None:
        stops = Stops(["abc", "x"])
        self.assertEqual(stops.feed("ab"), "")
        self.assertEqual(stops.feed("x"), "ab")
        self.assertEqual(stops.word, "x")

    def test_text_that_ends_without_a_stop_gives_back_what_was_held(self) -> None:
        stops = Stops(["</s>"])
        self.assertEqual(stops.feed("ends with </"), "ends with ")
        self.assertEqual(stops.flush(), "</")
        self.assertIsNone(stops.word)
        self.assertEqual(stops.flush(), "", "held text is given back once")

    def test_with_no_stop_strings_nothing_is_held(self) -> None:
        for words in (None, [], [""]):
            with self.subTest(words=words):
                stops = Stops(words)
                self.assertEqual(stops.feed("abc <"), "abc <")
                self.assertEqual(stops.flush(), "")
                self.assertIsNone(stops.word)


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
        engine = self.scripted("one two STOP three", EIGHT)
        await take(engine, "run")
        said = await answer(engine, MESSAGES, SAMPLING, "run", held=True, stop=["STOP"])
        self.assertEqual(said["queue_wait_ms"], 0.0)
        self.assertEqual(said["finish"], "word")
        # Between two passes of the run the slot is still the run's.
        self.assertTrue(engine._lock.locked())
        self.assertIs(engine.info()["busy"], True)
        stream = await collect(engine.stream_chat(
            MESSAGES, SAMPLING, "run", held=True, prefix="one two STOP",
        ))
        self.assertEqual(names(stream), ["start", "token", "done"])
        self.assertEqual(stream[-1][1]["text"], " three")
        self.assertEqual(stream[-1][1]["queue_wait_ms"], 0.0)
        self.assertTrue(engine._lock.locked())
        self.assertEqual(engine.info()["completed"], 2)
        engine.give_slot("run")
        self.assertTrue(engine.free)

    async def test_another_request_waits_for_the_whole_run_not_for_one_pass(self) -> None:
        engine = self.scripted("seven", EIGHT, "nine ten")
        await take(engine, "run")
        other = asyncio.create_task(collect(engine.stream_chat(MESSAGES, SAMPLING)))
        await until(lambda: engine.info()["queued"] == 1)

        await answer(engine, MESSAGES, SAMPLING, "run", held=True)
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
        engine = self.scripted("seven", "nine ten", EIGHT)
        await take(engine, "run")
        await answer(engine, MESSAGES, SAMPLING, "run", held=True)
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
        engine = self.scripted(EIGHT, EIGHT, cfg=thinking("none"))
        await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(engine.passes[0].prompt, HEAD)
        self.assertEqual(engine.passes[0].prompt, engine.render(MESSAGES))
        await collect(engine.stream_chat(MESSAGES, SAMPLING, prefix="one "))
        self.assertEqual(engine.passes[1].prompt, HEAD + "one ")

    async def test_what_a_pass_was_given_to_say_and_how_it_ended_are_kept(self) -> None:
        engine = self.scripted("one two STOP three", EIGHT)
        await collect(engine.stream_chat(MESSAGES, SAMPLING, stop=["STOP"]))
        await collect(engine.stream_chat(MESSAGES, {**SAMPLING, "max_tokens": 2}, prefix="one two STOP"))
        await collect(engine.stream_chat(MESSAGES, {**SAMPLING, "max_tokens": 2}))
        kept = [(p.output, p.yielded, p.finish, p.word) for p in engine.passes]
        self.assertEqual(kept, [
            ("one two ", 2, "word", "STOP"),
            (" three", 2, "eos", None),
            ("one two ", 2, "limit", None),
        ])


class FakeLlama:
    """Stands where llama.cpp would: writes ``texts`` as llama-cpp-python
    streams a completion, ends with ``finish`` ("stop" where the model ended
    it, "length" where it ran out of tokens), and keeps what it was asked for,
    how many pieces were read from it and whether its stream was closed."""

    def __init__(self, *texts: str, finish: str = "stop") -> None:
        self.texts = texts or ("Hi", " there")
        self.finish = finish
        self.asked: list[dict] = []
        self.sent = 0
        self.closed = 0

    def create_completion(self, **kwargs):
        self.asked.append(kwargs)
        return self._stream()

    def _stream(self):
        try:
            for text in self.texts:
                self.sent += 1
                yield {"choices": [{"text": text, "index": 0, "finish_reason": None}]}
            yield {"choices": [{"text": "", "index": 0, "finish_reason": self.finish}]}
        finally:
            self.closed += 1


class ModelCallTests(unittest.TestCase):
    """What llama.cpp is actually asked for. No thread, no loop: the one
    function that makes the call, called directly."""

    def setUp(self) -> None:
        self.engine = Engine(settings.model_copy(update={"mock": False, "thinking": "none"}))
        self.llama = FakeLlama()
        self.engine._llm = self.llama

    def given(self, *texts: str, finish: str = "stop") -> FakeLlama:
        self.llama = FakeLlama(*texts, finish=finish)
        self.engine._llm = self.llama
        return self.llama

    def test_an_answer_is_asked_for_as_the_prompt_written_out_with_its_settings(self) -> None:
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING))
        self.assertEqual(said, ["Hi", " there"])
        self.assertEqual(self.llama.asked, [{"prompt": HEAD, "stream": True, **SAMPLING}])

    def test_the_prompt_goes_on_from_the_part_already_written(self) -> None:
        self.engine.cfg = settings.model_copy(update={"mock": False, "thinking": "on"})
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, prefix="Let me count"))
        self.assertEqual(self.llama.asked[0]["prompt"], HEAD + "<think>\nLet me count")
        self.assertEqual(self.llama.asked[0]["prompt"], self.engine.render(MESSAGES, "Let me count"))

    def test_only_the_settings_a_pass_names_are_passed_on(self) -> None:
        list(self.engine._iter_chunks(
            MESSAGES, {"temperature": 0.0, "top_k": 1, "max_tokens": 96, "grammar": 'root ::= "x"'},
        ))
        self.assertEqual(
            self.llama.asked,
            [{"prompt": HEAD, "stream": True, "temperature": 0.0, "top_k": 1, "max_tokens": 96}],
        )

    def test_a_seed_is_passed_only_when_given(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, seed=0))
        self.assertEqual(self.llama.asked[0]["seed"], 0)
        list(self.engine._iter_chunks(MESSAGES, SAMPLING))
        self.assertNotIn("seed", self.llama.asked[1])

    def test_stop_strings_are_read_here_and_not_passed_to_llama_cpp(self) -> None:
        llama = self.given("Hi", " the", "re</s> more", " and more")
        reading = Reading()
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=["</s>"], reading=reading))
        self.assertEqual(said, ["Hi", " the", "re"])
        self.assertEqual((reading.finish, reading.word), ("word", "</s>"))
        self.assertNotIn("stop", llama.asked[0])
        # Nothing more is read from the model once it has said the string, and
        # its stream is closed: llama.cpp stops writing.
        self.assertEqual((llama.sent, llama.closed), (3, 1))

    def test_a_stop_string_split_across_pieces_is_still_caught(self) -> None:
        self.given("Hi </", "s> more")
        reading = Reading()
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=["</s>"], reading=reading))
        self.assertEqual("".join(said), "Hi ")
        self.assertEqual((reading.finish, reading.word), ("word", "</s>"))

    def test_a_tail_that_was_not_a_stop_string_after_all_is_not_lost(self) -> None:
        self.given("Hi </", "b>")
        reading = Reading()
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=["</s>"], reading=reading))
        self.assertEqual("".join(said), "Hi </b>")
        self.assertEqual((reading.finish, reading.word), ("eos", None))

    def test_a_reply_the_model_ends_ends_in_eos(self) -> None:
        reading = Reading()
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, reading=reading))
        self.assertEqual((reading.finish, reading.word), ("eos", None))
        self.assertEqual(self.llama.closed, 1)

    def test_a_reply_that_runs_out_of_tokens_ends_at_its_limit(self) -> None:
        self.given("Hi", " there", finish="length")
        reading = Reading()
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING, reading=reading))
        self.assertEqual(said, ["Hi", " there"])
        self.assertEqual((reading.finish, reading.word), ("limit", None))

    def test_a_reader_that_stops_early_closes_the_stream(self) -> None:
        llama = self.given("one", " two", " three")
        chunks = self.engine._iter_chunks(MESSAGES, SAMPLING)
        self.assertEqual(next(chunks), "one")
        chunks.close()
        self.assertEqual((llama.sent, llama.closed), (1, 1))


class ThreadTests(unittest.TestCase):
    """How many threads the model is given, and what the pages are told."""

    def engine(self, n_threads: int) -> Engine:
        return Engine(settings.model_copy(update={"mock": True, "n_threads": n_threads}))

    def test_zero_means_every_cpu_this_process_may_use(self) -> None:
        with mock.patch("app.llm.core.usable_cpu_count", return_value=4):
            engine = self.engine(0)
            self.assertEqual(engine.threads, 4)
            self.assertEqual(engine.info()["n_threads"], 4)

    def test_a_number_pins_it_whatever_the_machine_has(self) -> None:
        with mock.patch("app.llm.core.usable_cpu_count", return_value=4):
            self.assertEqual(self.engine(2).threads, 2)

    def test_a_machine_that_cannot_be_measured_gets_one(self) -> None:
        with mock.patch("app.llm.core.usable_cpu_count", return_value=None):
            self.assertEqual(self.engine(0).threads, 1)


SERVER_URL = "http://127.0.0.1:8081"


class FakeServer:
    """Stands where a llama-server would: answers when asked whether it is up,
    counts a word as a token, and writes ``texts``. It keeps every request.

    ``finish`` and ``word`` are how it says the reply ended, on its last
    piece; left out, it does not say."""

    def __init__(
        self, *texts: str, fail: Exception | None = None, up_after: int = 0,
        read: Reading | None = None, finish: str | None = None, word: str | None = None,
    ) -> None:
        self.texts = texts or ("Hi", " there")
        self.fail = fail
        self.up_after = up_after
        # What it says it read of each prompt, once it has written the answer.
        self.read = read
        self.finish = finish
        self.word = word
        # Every text it was asked to count.
        self.counted: list[str] = []
        self.asked: list[dict] = []
        # The engine's status at each time it asked whether the server was up.
        self.seen: list[str] = []
        self.engine: Engine | None = None

    def ready(self) -> bool:
        if self.engine is not None:
            self.seen.append(self.engine.state.status)
        return len(self.seen) > self.up_after

    def tokenize(self, text: str) -> int:
        self.counted.append(text)
        return len(text.split())

    def stream(self, body: dict, reading: Reading | None = None):
        self.asked.append(body)
        if self.fail is not None:
            raise self.fail
        yield from self.texts
        if reading is not None and self.read is not None:
            reading.prompt, reading.read, reading.cached = (
                self.read.prompt, self.read.read, self.read.cached
            )
        if reading is not None:
            reading.finish, reading.word = self.finish, self.word


def served(server: FakeServer, **update) -> Engine:
    """A real engine whose model is ``server``, polled without waiting."""
    engine = Engine(settings.model_copy(update={"mock": False, "llm_url": SERVER_URL, **update}))
    engine.SERVER_POLL = 0.0
    engine.QUEUE_TICK = 0.01
    engine._connect = lambda url: server
    server.engine = engine
    return engine


class ServerLoadTests(unittest.TestCase):
    """Waiting for a model server instead of loading a model."""

    def test_the_engine_is_ready_once_the_server_answers(self) -> None:
        server = FakeServer(up_after=2)
        engine = served(server)
        engine.load_blocking()
        self.assertTrue(engine.ready)
        self.assertEqual(server.seen, ["loading", "loading", "loading"])
        self.assertIsNone(engine._llm, "no weights are loaded into this process")
        self.assertEqual(engine.info()["runtime"], "llama-server")

    def test_a_server_that_never_answers_is_an_error_without_its_address(self) -> None:
        engine = served(FakeServer(up_after=10**6))
        engine.SERVER_PATIENCE = 0.0
        with self.assertLogs("aurora.llm", "ERROR") as logged:
            engine.load_blocking()
        self.assertEqual(engine.state.status, "error")
        self.assertEqual(
            engine.state.error, "The model server is not answering. The server log has the detail."
        )
        self.assertIn(SERVER_URL, "\n".join(logged.output), "the log says where it looked")

    def test_the_site_keeps_waiting_and_comes_back_when_the_server_does(self) -> None:
        server = FakeServer(up_after=3)
        engine = served(server)
        engine.SERVER_PATIENCE = 0.0
        with self.assertLogs("aurora.llm", "ERROR"):
            engine.load_blocking(keep_waiting=True)
        self.assertEqual(server.seen, ["loading", "error", "error", "error"])
        self.assertTrue(engine.ready)
        self.assertIsNone(engine.state.error)

    def test_the_servers_memory_is_counted_once_it_answers(self) -> None:
        server = FakeServer()
        server.port = 8081
        engine = served(server)
        with mock.patch("app.llm.core.follow_server") as follow:
            engine.load_blocking()
        follow.assert_called_once_with(8081)

    def test_tokens_are_counted_by_the_server_once_it_answers(self) -> None:
        engine = served(FakeServer())
        self.assertEqual(engine.count_tokens("one two three four"), 4, "an estimate before it answers")
        engine.load_blocking()
        self.assertEqual(engine.count_tokens("one two three"), 3)

    def test_the_server_is_asked_to_count_a_text_once(self) -> None:
        server = FakeServer()
        engine = served(server)
        engine.load_blocking()
        for _ in range(3):
            self.assertEqual(engine.prompt_tokens(MESSAGES), engine.prompt_tokens(MESSAGES))
        self.assertEqual(len(server.counted), 1)

    def test_it_remembers_only_so_many_counts(self) -> None:
        server = FakeServer()
        engine = served(server)
        engine.COUNTS_KEPT = 2
        engine.load_blocking()
        for text in ("one", "two", "three", "one"):
            engine.count_tokens(text)
        self.assertEqual(server.counted, ["one", "two", "three", "one"])

    def test_without_an_address_the_model_is_in_this_process(self) -> None:
        engine = Engine(settings.model_copy(update={"mock": True}))
        self.assertEqual(engine.info()["runtime"], "llama-cpp-python")


class ServerCallTests(unittest.TestCase):
    """What the server is asked for. The same calls as ModelCallTests, made
    to a server instead of llama.cpp: the prompt written out whole, and the
    sampling settings by the names llama-server knows them by."""

    def setUp(self) -> None:
        self.server = FakeServer()
        self.engine = served(self.server, thinking="none")
        self.engine.load_blocking()

    def test_settings_a_pass_does_not_name_are_the_ones_llama_cpp_python_would_use(self) -> None:
        said = list(self.engine._iter_chunks(MESSAGES, {"temperature": 0.0, "top_k": 1, "max_tokens": 96}))
        self.assertEqual(said, ["Hi", " there"])
        self.assertEqual(
            self.server.asked,
            [{
                "prompt": HEAD,
                "temperature": 0.0, "top_p": 0.95, "top_k": 1, "min_p": 0.05,
                "repeat_penalty": 1.0, "n_predict": 96,
            }],
        )

    def test_an_answer_keeps_all_five_of_its_settings_with_its_limit_as_n_predict(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, SAMPLING))
        named = {k: v for k, v in SAMPLING.items() if k != "max_tokens"}
        self.assertEqual(
            self.server.asked[0], {"prompt": HEAD, "min_p": 0.05, **named, "n_predict": 64},
        )

    def test_a_pass_with_no_limit_sends_none(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, {"temperature": 0.0}))
        self.assertNotIn("n_predict", self.server.asked[0])
        self.assertNotIn("max_tokens", self.server.asked[0])

    def test_only_the_settings_a_pass_names_are_sent_and_never_its_messages(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, {"temperature": 0.0, "grammar": 'root ::= "x"'}))
        self.assertEqual(
            set(self.server.asked[0]),
            {"prompt", "temperature", "top_p", "top_k", "min_p", "repeat_penalty"},
        )

    def test_the_prompt_goes_on_from_the_part_already_written(self) -> None:
        server = FakeServer()
        engine = served(server, thinking="on")
        engine.load_blocking()
        list(engine._iter_chunks(MESSAGES, SAMPLING, prefix="Let me count"))
        self.assertEqual(server.asked[0]["prompt"], HEAD + "<think>\nLet me count")

    def test_seed_and_stop_strings_go_only_when_given(self) -> None:
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, seed=0, stop=["</s>"]))
        self.assertEqual(self.server.asked[0]["seed"], 0)
        self.assertEqual(self.server.asked[0]["stop"], ["</s>"])
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=[]))
        for key in ("seed", "stop", "grammar"):
            self.assertNotIn(key, self.server.asked[1])

    def test_how_the_server_says_the_reply_ended_is_kept(self) -> None:
        self.server.finish, self.server.word = "word", "</tool_call>"
        reading = Reading()
        list(self.engine._iter_chunks(
            MESSAGES, SAMPLING, stop=["</tool_call>", "<tool_call>"], reading=reading,
        ))
        self.assertEqual((reading.finish, reading.word), ("word", "</tool_call>"))

    def test_a_stop_the_server_does_not_name_is_the_one_it_was_given_if_there_was_one(self) -> None:
        self.server.finish = "word"
        reading = Reading()
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=["</think>"], reading=reading))
        self.assertEqual((reading.finish, reading.word), ("word", "</think>"))
        # Of two, which one it was is not guessed.
        reading = Reading()
        list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=["</think>", "\n"], reading=reading))
        self.assertEqual((reading.finish, reading.word), ("word", None))

    def test_the_server_reads_the_stop_strings_itself(self) -> None:
        # What the server writes is passed on as it is: it stops where it was
        # told to, and what it wrote is what is in its cache.
        self.server.texts = ("Hi", " </s> there")
        said = list(self.engine._iter_chunks(MESSAGES, SAMPLING, stop=["</s>"]))
        self.assertEqual(said, ["Hi", " </s> there"])


class ServerPassTests(unittest.IsolatedAsyncioTestCase):
    """A whole pass through the slot, with the model in a server."""

    async def test_a_pass_streams_and_ends_like_any_other_and_frees_the_slot(self) -> None:
        engine = served(FakeServer("one", " two", finish="eos"))
        engine.load_blocking()
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertEqual(names(stream), ["start", "token", "done"])
        done = stream[-1][1]
        self.assertEqual(done["text"], "one two")
        self.assertEqual(done["prompt_tokens"], engine.prompt_tokens(MESSAGES))
        self.assertEqual((done["finish"], done["word"]), ("eos", None))
        self.assertFalse(engine._lock.locked())

    async def test_the_prompt_counted_is_the_prompt_sent(self) -> None:
        server = FakeServer("two")
        engine = served(server)
        engine.load_blocking()
        await collect(engine.stream_chat(MESSAGES, SAMPLING, prefix="one "))
        self.assertEqual(server.counted, [server.asked[0]["prompt"]])
        self.assertEqual(server.asked[0]["prompt"], engine.render(MESSAGES, "one "))

    async def test_what_the_server_read_of_the_prompt_is_in_done(self) -> None:
        engine = served(FakeServer("one", " two", read=Reading(prompt=108, read=8, cached=100)))
        engine.load_blocking()
        done = (await collect(engine.stream_chat(MESSAGES, SAMPLING)))[-1][1]
        self.assertEqual((done["read_tokens"], done["cached_tokens"]), (8, 100))
        # Our own count stays ours: the server's is the other two together.
        self.assertEqual(done["prompt_tokens"], engine.prompt_tokens(MESSAGES))

    async def test_where_the_server_stopped_is_in_done(self) -> None:
        engine = served(FakeServer("Calling.", finish="word", word="</tool_call>"))
        engine.load_blocking()
        done = await answer(engine, MESSAGES, SAMPLING, stop=["</tool_call>"])
        self.assertEqual(
            (done["text"], done["finish"], done["word"]), ("Calling.", "word", "</tool_call>"),
        )

    async def test_a_server_that_does_not_say_leaves_the_figures_empty(self) -> None:
        engine = served(FakeServer("one"))
        engine.load_blocking()
        done = (await collect(engine.stream_chat(MESSAGES, SAMPLING)))[-1][1]
        self.assertEqual((done["read_tokens"], done["cached_tokens"]), (None, None))
        # Nor is how it ended guessed.
        self.assertEqual((done["finish"], done["word"]), (None, None))

    async def test_a_server_that_fails_is_an_error_event_and_frees_the_slot(self) -> None:
        engine = served(FakeServer(fail=ServerError("the model server is not answering")))
        engine.load_blocking()
        stream = await collect(engine.stream_chat(MESSAGES, SAMPLING))
        self.assertIn(("error", {"message": "ServerError: the model server is not answering"}), stream)
        self.assertFalse(engine._lock.locked())


if __name__ == "__main__":
    unittest.main()
