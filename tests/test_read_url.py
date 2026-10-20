"""The page reader's guard: where it lets the server connect, and that the
connection goes where the check said it would.

Needs the app's own requirements; without them the whole module is skipped.
No socket is opened: the resolver and the transport are both stand-ins.
"""

from __future__ import annotations

import functools
import socket
import unittest
from unittest import mock

try:
    import httpx

    from app.tools import read_url
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

PAGE = "<html><head><title>Tides</title></head><body><p>High water at six.</p></body></html>"


def answers(*addresses: str):
    """A resolver that gives each lookup the next address, the last one for
    every lookup after: a name that changes its answer, as a rebinding one
    does."""
    queue = list(addresses)

    def resolve(host, port, *args, **kwargs):
        address = queue.pop(0) if len(queue) > 1 else queue[0]
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


class PinnedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.asked: list[httpx.Request] = []

    def serve(self, handler) -> None:
        def record(request: httpx.Request) -> httpx.Response:
            self.asked.append(request)
            return handler(request)

        client = functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(record))
        patcher = mock.patch.object(read_url.httpx, "AsyncClient", client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def resolve(self, *addresses: str) -> None:
        patcher = mock.patch.object(read_url.socket, "getaddrinfo", answers(*addresses))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_the_connection_goes_to_the_address_that_was_checked(self) -> None:
        # Public for the check, loopback the moment after: the connection
        # still goes to the public one, and the site is asked for by name.
        self.resolve("93.184.216.34", "127.0.0.1")
        self.serve(lambda r: httpx.Response(200, html=PAGE))
        result = await read_url._read_url("https://example.org/tides")
        self.assertTrue(result.ok, result.summary)
        request = self.asked[0]
        self.assertEqual(request.url.host, "93.184.216.34")
        self.assertEqual(request.headers["host"], "example.org")
        self.assertEqual(request.extensions.get("sni_hostname"), "example.org")

    async def test_a_port_is_kept_for_the_address_and_the_name(self) -> None:
        self.resolve("93.184.216.34")
        self.serve(lambda r: httpx.Response(200, html=PAGE))
        await read_url._read_url("http://example.org:8080/tides")
        request = self.asked[0]
        self.assertEqual((request.url.host, request.url.port), ("93.184.216.34", 8080))
        self.assertEqual(request.headers["host"], "example.org:8080")
        self.assertNotIn("sni_hostname", request.extensions)

    async def test_a_name_that_answers_privately_is_refused_before_any_request(self) -> None:
        self.resolve("10.0.0.7")
        self.serve(lambda r: httpx.Response(200, html=PAGE))
        result = await read_url._read_url("https://intranet.example/")
        self.assertEqual((result.ok, result.error), (False, "blocked"))
        self.assertEqual(self.asked, [])

    async def test_each_hop_of_a_redirect_is_checked_and_pinned_again(self) -> None:
        self.resolve("93.184.216.34", "10.0.0.7")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://inside.example/secret"})

        self.serve(handler)
        result = await read_url._read_url("https://example.org/tides")
        self.assertEqual((result.ok, result.error), (False, "blocked"))
        self.assertEqual(len(self.asked), 1)

    async def test_a_relative_redirect_keeps_the_sites_name(self) -> None:
        self.resolve("93.184.216.34")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "/tides"})
            return httpx.Response(200, html=PAGE)

        self.serve(handler)
        result = await read_url._read_url("https://example.org/old")
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([r.headers["host"] for r in self.asked], ["example.org", "example.org"])
        self.assertEqual(self.asked[1].url.path, "/tides")

    def test_an_address_written_as_one_is_used_as_it_is(self) -> None:
        url, headers, extensions = read_url._pinned("https://93.184.216.34/x", None)
        self.assertEqual((url, headers, extensions), ("https://93.184.216.34/x", {}, {}))

    def test_an_ipv6_address_is_bracketed(self) -> None:
        url, headers, _ = read_url._pinned("https://example.org/x", "2606:2800:220:1::1")
        self.assertEqual(url, "https://[2606:2800:220:1::1]/x")
        self.assertEqual(headers, {"Host": "example.org"})


if __name__ == "__main__":
    unittest.main()
