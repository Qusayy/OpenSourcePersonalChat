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

    from app.llm_server import LlamaServer, ServerError
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

URL = "http://127.0.0.1:8081"


def sse(*chunks: dict, done: bool = True) -> list[bytes]:
    """A streamed chat completion: one ``data:`` line per chunk."""
    lines = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    if done:
        lines.append(b"data: [DONE]\n\n")
    return lines


def delta(text: str | None = None, **extra) -> dict:
    piece = dict(extra)
    if text is not None:
        piece["content"] = text
    return {"choices": [{"delta": piece}]}


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
    def test_the_text_is_the_content_of_each_delta_in_order(self) -> None:
        server = Server(httpx.Response(200, stream=Body(sse(
            delta(role="assistant"), delta("Hi"), delta(" there"), delta(),
        ))))
        self.assertEqual(list(server.client().stream({"messages": []})), ["Hi", " there"])

    def test_done_ends_the_stream_whatever_follows(self) -> None:
        parts = sse(delta("one")) + sse(delta("two"), done=False)
        server = Server(httpx.Response(200, stream=Body(parts)))
        self.assertEqual(list(server.client().stream({"messages": []})), ["one"])

    def test_lines_that_are_not_data_are_skipped(self) -> None:
        parts = [b": keep-alive\n\n", *sse(delta("a"), {"choices": []}, delta("b"))]
        server = Server(httpx.Response(200, stream=Body(parts)))
        self.assertEqual(list(server.client().stream({"messages": []})), ["a", "b"])

    def test_the_request_is_the_body_given_streamed_and_offered_to_the_cache(self) -> None:
        server = Server(httpx.Response(200, stream=Body(sse(delta("x")))))
        asked = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.0, "grammar": 'root ::= "x"'}
        list(server.client().stream(asked))
        request = server.asked[0]
        self.assertEqual((request.method, request.url.path), ("POST", "/v1/chat/completions"))
        self.assertEqual(server.body(), {**asked, "stream": True, "cache_prompt": True})

    def test_a_reader_that_stops_early_hangs_up(self) -> None:
        body = Body(sse(delta("one"), delta("two"), delta("three")))
        stream = Server(httpx.Response(200, stream=body)).client().stream({"messages": []})
        self.assertEqual(next(stream), "one")
        stream.close()
        self.assertTrue(body.closed, "the server only stops writing when the connection closes")
        self.assertLess(body.read, 4)

    def test_an_error_status_is_said_without_the_address(self) -> None:
        server = Server(httpx.Response(500, stream=Body([b'{"error": "boom"}'])))
        with self.assertRaises(ServerError) as caught:
            list(server.client().stream({"messages": []}))
        self.assertEqual(str(caught.exception), "the model server answered 500")

    def test_a_server_that_is_not_there_is_said_without_the_address(self) -> None:
        server = Server(httpx.ConnectError(f"[Errno 111] Connection refused to {URL}"))
        with self.assertRaises(ServerError) as caught:
            list(server.client().stream({"messages": []}))
        self.assertEqual(str(caught.exception), "the model server is not answering")
        self.assertNotIn("127.0.0.1", str(caught.exception))

    def test_an_error_in_the_middle_of_the_stream_ends_it(self) -> None:
        parts = sse(delta("half"), {"error": {"message": "out of memory"}}, delta("never"))
        stream = Server(httpx.Response(200, stream=Body(parts))).client().stream({"messages": []})
        self.assertEqual(next(stream), "half")
        with self.assertRaises(ServerError):
            next(stream)

    def test_a_line_that_is_not_json_ends_it(self) -> None:
        server = Server(httpx.Response(200, stream=Body([b"data: {not json\n\n"])))
        with self.assertRaises(ServerError):
            list(server.client().stream({"messages": []}))


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
