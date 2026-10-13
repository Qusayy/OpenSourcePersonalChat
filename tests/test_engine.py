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
