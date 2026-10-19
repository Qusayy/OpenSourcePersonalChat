"""The client for a model server, against a server that is only a function.

``httpx.MockTransport`` stands where llama-server would: each test says what
the server answers and reads back what it was asked. No socket is opened, so
these run anywhere and cannot reach a real server by mistake.
"""

from __future__ import annotations

import json
import unittest

try:
    import httpx

    from app.llm.server import LlamaServer, Reading, ServerError
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

URL = "http://127.0.0.1:8081"


def sse(*chunks: dict, done: bool = False) -> list[bytes]:
    """A streamed completion: one ``data:`` line per chunk."""
    lines = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    if done:
        lines.append(b"data: [DONE]\n\n")
    return lines


def piece(text: str | None = None, **extra) -> dict:
    """One piece of a completion as llama-server streams it."""
    out = {"stop": False, **extra}
    if text is not None:
        out["content"] = text
    return out


def last(**extra) -> dict:
    """The last piece: it says how the reply ended and what was read."""
    return {"content": "", "stop": True, **extra}


class Body(httpx.SyncByteStream):
    """A response body that remembers whether the client hung up on it."""

    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts
        self.read = 0
        self.closed = False

    def __iter__(self):
        for part in self.parts:
            self.read += 1
            yield part

    def close(self) -> None:
        self.closed = True


class Server:
    """Answers each request with the next of ``answers`` and keeps the requests."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(self) -> LlamaServer:
        return LlamaServer(URL, transport=httpx.MockTransport(self))

    def body(self, index: int = 0) -> dict:
        return json.loads(self.asked[index].content)


class StreamTests(unittest.TestCase):
    def test_the_text_is_the_content_of_each_piece_in_order(self) -> None:
        server = Server(httpx.Response(200, stream=Body(sse(
            piece("Hi"), piece(" there"), piece(), last(stop_type="eos"),
        ))))
        self.assertEqual(list(server.client().stream({"prompt": "x"})), ["Hi", " there"])

    def test_the_last_piece_ends_the_stream_whatever_follows(self) -> None:
        parts = sse(piece("one"), last(stop_type="eos"), piece("two"))
        server = Server(httpx.Response(200, stream=Body(parts)))
        self.assertEqual(list(server.client().stream({"prompt": "x"})), ["one"])

    def test_done_ends_it_too(self) -> None:
        parts = sse(piece("one"), done=True) + sse(piece("two"))
        server = Server(httpx.Response(200, stream=Body(parts)))
        self.assertEqual(list(server.client().stream({"prompt": "x"})), ["one"])

    def test_lines_that_are_not_data_are_skipped(self) -> None:
        parts = [b": keep-alive\n\n", *sse(piece("a"), piece(), piece("b"), last())]
        server = Server(httpx.Response(200, stream=Body(parts)))
        self.assertEqual(list(server.client().stream({"prompt": "x"})), ["a", "b"])

    def test_the_prompt_goes_to_completion_streamed_and_offered_to_the_cache(self) -> None:
        server = Server(httpx.Response(200, stream=Body(sse(piece("x"), last()))))
        asked = {"prompt": "<|im_start|>user\nhi<|im_end|>\n", "temperature": 0.0, "stop": ["</think>"]}
        list(server.client().stream(asked))
        request = server.asked[0]
        self.assertEqual((request.method, request.url.path), ("POST", "/completion"))
        self.assertEqual(server.body(), {**asked, "stream": True, "cache_prompt": True})

    def test_a_reader_that_stops_early_hangs_up(self) -> None:
        body = Body(sse(piece("one"), piece("two"), piece("three"), last()))
        stream = Server(httpx.Response(200, stream=body)).client().stream({"prompt": "x"})
        self.assertEqual(next(stream), "one")
        stream.close()
        self.assertTrue(body.closed, "the server only stops writing when the connection closes")
        self.assertLess(body.read, 4)

    def test_an_error_status_is_said_without_the_address(self) -> None:
        server = Server(httpx.Response(500, stream=Body([b'{"error": "boom"}'])))
        with self.assertRaises(ServerError) as caught:
            list(server.client().stream({"prompt": "x"}))
        self.assertEqual(str(caught.exception), "the model server answered 500")

    def test_a_server_that_is_not_there_is_said_without_the_address(self) -> None:
        server = Server(httpx.ConnectError(f"[Errno 111] Connection refused to {URL}"))
        with self.assertRaises(ServerError) as caught:
            list(server.client().stream({"prompt": "x"}))
        self.assertEqual(str(caught.exception), "the model server is not answering")
        self.assertNotIn("127.0.0.1", str(caught.exception))

    def test_an_error_in_the_middle_of_the_stream_ends_it(self) -> None:
        parts = sse(piece("half"), {"error": {"message": "out of memory"}}, piece("never"))
        stream = Server(httpx.Response(200, stream=Body(parts))).client().stream({"prompt": "x"})
        self.assertEqual(next(stream), "half")
        with self.assertRaises(ServerError):
            next(stream)

    def test_a_line_that_is_not_json_ends_it(self) -> None:
        server = Server(httpx.Response(200, stream=Body([b"data: {not json\n\n"])))
        with self.assertRaises(ServerError):
            list(server.client().stream({"prompt": "x"}))


class ReadingTests(unittest.TestCase):
    """What the server says it did with the prompt and how the reply ended,
    kept from its last piece."""

    def read(self, *chunks: dict) -> Reading:
        reading = Reading()
        server = Server(httpx.Response(200, stream=Body(sse(*chunks))))
        list(server.client().stream({"prompt": "x"}, reading))
        return reading

    def test_what_was_read_and_what_was_cached_come_from_the_timings(self) -> None:
        reading = self.read(
            piece("Hi"),
            last(stop_type="eos", tokens_evaluated=312,
                 timings={"prompt_n": 12, "cache_n": 300, "prompt_ms": 150.0}),
        )
        self.assertEqual(reading, Reading(prompt=312, read=12, cached=300, finish="eos"))

    def test_the_string_a_reply_stopped_at_is_kept(self) -> None:
        reading = self.read(piece("Hmm"), last(stop_type="word", stopping_word="</think>"))
        self.assertEqual((reading.finish, reading.word), ("word", "</think>"))

    def test_an_older_server_says_how_it_ended_in_its_own_words(self) -> None:
        for flags, finish in (({"stopped_word": True}, "word"), ({"stopped_eos": True}, "eos"),
                              ({"stopped_limit": True}, "limit")):
            with self.subTest(finish=finish):
                reading = self.read(piece("x"), last(stopping_word="</tool_call>" if finish == "word" else "", **flags))
                self.assertEqual(reading.finish, finish)
        self.assertEqual(self.read(piece("x"), last(stopped_word=True, stopping_word="</think>")).word,
                         "</think>")

    def test_a_server_that_does_not_say_how_it_ended_leaves_it_unknown(self) -> None:
        self.assertIsNone(self.read(piece("x"), last()).finish)

    def test_the_one_figure_the_other_two_give_is_filled_in(self) -> None:
        cached = self.read(piece("x"), last(tokens_evaluated=90, timings={"prompt_n": 10}))
        self.assertEqual((cached.prompt, cached.read, cached.cached), (90, 10, 80))
        whole = self.read(piece("x"), last(timings={"prompt_n": 10, "cache_n": 80}))
        self.assertEqual((whole.prompt, whole.read, whole.cached), (90, 10, 80))

    def test_a_server_that_reports_nothing_leaves_every_figure_empty(self) -> None:
        self.assertEqual(self.read(piece("Hi"), piece(" there")), Reading())

    def test_a_figure_the_server_does_not_have_is_not_taken(self) -> None:
        reading = self.read(piece("x"), last(timings={"prompt_n": -1, "cache_n": -1}))
        self.assertEqual((reading.prompt, reading.read, reading.cached), (None, None, None))

    def test_an_answer_stopped_before_its_end_reports_nothing(self) -> None:
        reading = Reading()
        body = Body(sse(piece("one"), piece("two"),
                        last(stop_type="eos", timings={"prompt_n": 5, "cache_n": 95})))
        stream = Server(httpx.Response(200, stream=body)).client().stream({"prompt": "x"}, reading)
        self.assertEqual(next(stream), "one")
        stream.close()
        self.assertEqual(reading, Reading())

    def test_without_a_reading_to_fill_the_stream_is_the_same(self) -> None:
        server = Server(httpx.Response(200, stream=Body(sse(
            piece("Hi"), last(timings={"prompt_n": 3}),
        ))))
        self.assertEqual(list(server.client().stream({"prompt": "x"})), ["Hi"])


class TokenizeTests(unittest.TestCase):
    def test_the_count_is_the_number_of_tokens_returned(self) -> None:
        server = Server(httpx.Response(200, json={"tokens": [9707, 1879, 11]}))
        self.assertEqual(server.client().tokenize("Hello world,"), 3)
        request = server.asked[0]
        self.assertEqual(request.url.path, "/tokenize")
        self.assertEqual(server.body(), {"content": "Hello world,", "add_special": False})

    def test_a_failure_is_a_server_error(self) -> None:
        for answer in (httpx.Response(503), httpx.Response(200, json={}), httpx.ReadTimeout("slow")):
            with self.subTest(answer=answer), self.assertRaises(ServerError):
                Server(answer).client().tokenize("x")


class ReadyTests(unittest.TestCase):
    def test_up_once_the_model_is_loaded_and_while_it_is_busy(self) -> None:
        server = Server(
            httpx.Response(503, json={"status": "loading model"}),
            httpx.Response(200, json={"status": "ok"}),
            httpx.Response(200, json={"status": "no slot available"}),
        )
        client = server.client()
        self.assertEqual([client.ready(), client.ready(), client.ready()], [False, True, True])
        self.assertEqual(server.asked[0].url.path, "/health")

    def test_a_server_that_is_not_there_is_not_ready(self) -> None:
        self.assertFalse(Server(httpx.ConnectError("refused")).client().ready())

    def test_the_port_is_read_from_the_address(self) -> None:
        self.assertEqual(LlamaServer(URL).port, 8081)
        self.assertEqual(LlamaServer("http://localhost").port, 80)


if __name__ == "__main__":
    unittest.main()
