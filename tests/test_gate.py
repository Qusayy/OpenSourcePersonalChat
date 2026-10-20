"""The gate: what a visitor without a session is given, and what opens it.

Needs the app's own requirements; without them the whole module is skipped.
The rest of the suite runs with the gate off (tests/__init__.py). Here it is
on, with a pattern made up for the purpose, a hash cheap enough to compute a
few hundred times, and no wait before a refusal.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    from dotenv import dotenv_values
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app import gate, main
    from app.api import frontend, pages
    from app.config import Settings
    from app.store import conversations, db
    from tests.support import ChatCase, shape, temp_database, with_settings
    from tests.test_web import make_dist
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

CHAIN = [3, 7, 12, 1, 9, 14, 2]
WRONG = [3, 7, 12, 1, 9, 14, 5]
SALT = bytes(range(16))
# Far below what the gate accepts in use; MIN_ITERATIONS is lowered to match.
QUICK = 1000
COVER = "<!doctype html><title>Aurora</title><canvas id=sky></canvas>"
TITLE = "Marmalade recipe for Thursday"


def every_route(routes):
    """Each route the application answers. FastAPI keeps a router it was
    given as one entry, with that router's own routes inside it."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from every_route(inner.routes)
        else:
            yield route


def verifier_for(chain: list[int], iterations: int = QUICK, salt: bytes = SALT) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", gate.canonical(chain), salt, iterations, 32)
    return f"gate1.{iterations}.{salt.hex()}.{digest.hex()}"


def quick_gate(case: unittest.TestCase) -> None:
    """A hash that takes no time, no wait before a refusal, a clean cache, and
    a gate that keeps what it would log to itself."""
    for target, name, value in (
        (gate, "MIN_ITERATIONS", QUICK),
        (gate, "FLOOR", 0.0),
        (gate.log, "disabled", True),
    ):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    gate.parse_verifier.cache_clear()
    case.addCleanup(gate.parse_verifier.cache_clear)


class GateCase(unittest.TestCase):
    """The real app, armed, over a temporary build and database."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        dist = make_dist(root)
        (dist / "cover.html").write_text(COVER, encoding="utf-8")

        temp_database(self, mock_mode=True)
        for target, name, value in (
            (pages, "frontend", frontend.Frontend(dist)),
            (gate, "cover", gate.Cover(dist)),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        quick_gate(self)
        with_settings(self, gate="on", gate_verifier=verifier_for(CHAIN))

        self.conversation = conversations.create(TITLE)
        # No `with`: the lifespan would open the real database and start
        # loading the model.
        self.client = TestClient(main.app)

    def draw(self, chain: list[int], client: TestClient | None = None):
        return (client or self.client).post("/api/unlock", json={"chain": chain})

    def enrol(self, code: str, client: TestClient | None = None):
        return (client or self.client).get(f"/api/enroll/{code}")

    def assert_locked(self, res) -> None:
        """The answer is the one any /api/ path gives a visitor without a
        session, to the byte, and it makes nothing."""
        expected = TestClient(main.app).get("/api/nothing/here")
        self.assertEqual(expected.status_code, 401)
        self.assertEqual(res.status_code, expected.status_code)
        self.assertEqual(res.content, expected.content)
        for name in ("content-type", "content-length", "cache-control", "vary"):
            self.assertEqual(res.headers[name], expected.headers[name])
        self.assertNotIn("set-cookie", res.headers)


def cookie_parts(cookie: str) -> set[str]:
    """A Set-Cookie line's attributes, in lower case, without the value."""
    return {part.strip().lower() for part in cookie.split(";")[1:]}


def device_cookie(res, name: str = "aurora_d") -> str:
    return next(c for c in res.headers.get_list("set-cookie") if c.startswith(f"{name}="))


# ------------------------------------------------------------- the pattern ---


class VerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        gate.parse_verifier.cache_clear()
        self.addCleanup(gate.parse_verifier.cache_clear)

    def test_a_line_from_the_setup_page_is_read(self) -> None:
        line = f"gate1.600000.{SALT.hex()}.{'ab' * 32}"
        found = gate.parse_verifier(line)
        assert found is not None
        self.assertEqual(found.iterations, 600000)
        self.assertEqual(found.salt, SALT)
        self.assertEqual(found.digest, bytes.fromhex("ab" * 32))
        # Pasted with a newline after it, it is the same line.
        self.assertEqual(gate.parse_verifier(f"  {line}\n"), found)

    def test_anything_else_is_not_a_verifier(self) -> None:
        good = f"gate1.600000.{SALT.hex()}.{'ab' * 32}"
        for name, line in (
            ("empty", ""),
            ("another scheme", good.replace("gate1", "gate2")),
            ("capitals in the hex", good.upper().replace("GATE1", "gate1")),
            ("a short hash", good[:-2]),
            ("a short salt", f"gate1.600000.{SALT.hex()[:-2]}.{'ab' * 32}"),
            ("too few iterations", f"gate1.99999.{SALT.hex()}.{'ab' * 32}"),
            ("too many iterations", f"gate1.2000001.{SALT.hex()}.{'ab' * 32}"),
            ("something after it", f"{good} # the pattern"),
        ):
            with self.subTest(name):
                self.assertIsNone(gate.parse_verifier(line))

    def test_the_hash_is_the_one_the_setup_page_makes(self) -> None:
        # The same figures are asserted in web/src/setup, against the browser's
        # own hashing: if either side changes how a chain is written down or
        # hashed, one of the two tests says so.
        self.assertEqual(gate.canonical(CHAIN), b"aurora-gate:v1:3,7,12,1,9,14,2")
        line = (
            f"gate1.600000.{SALT.hex()}."
            "4549c411cc40d015ba1a0e1ce608a08f06fae4701cd289de3e2be82bfc7c4f0b"
        )
        found = gate.parse_verifier(line)
        assert found is not None
        self.assertTrue(gate.matches(found, CHAIN))
        self.assertFalse(gate.matches(found, WRONG))

    def test_the_line_comes_through_an_env_file_as_it_went_in(self) -> None:
        line = verifier_for(CHAIN, 600000)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(f"AURORA_GATE_VERIFIER={line}\n", encoding="utf-8")
            self.assertEqual(dotenv_values(path)["AURORA_GATE_VERIFIER"], line)

    def test_only_a_list_of_enough_stars_is_a_chain(self) -> None:
        self.assertEqual(gate.valid_shape({"chain": CHAIN}), CHAIN)
        for name, body in (
            ("no chain", {}),
            ("not an object", CHAIN),
            ("too short", {"chain": CHAIN[:-1]}),
            ("too long", {"chain": [1, 2] * 25}),
            ("a string", {"chain": "3,7,12,1,9,14,2"}),
            ("a star that is text", {"chain": [*CHAIN[:-1], "2"]}),
            ("a star that is true", {"chain": [*CHAIN[:-1], True]}),
            ("a star that is a fraction", {"chain": [*CHAIN[:-1], 2.0]}),
            ("a star below the sky", {"chain": [*CHAIN[:-1], -1]}),
            ("a star beyond it", {"chain": [*CHAIN[:-1], 64]}),
        ):
            with self.subTest(name):
                self.assertIsNone(gate.valid_shape(body))


class ModeTests(unittest.TestCase):
    def setUp(self) -> None:
        quick_gate(self)

    def test_only_the_exact_word_turns_the_gate_off(self) -> None:
        with_settings(self, gate_verifier=verifier_for(CHAIN))
        for value, expected in (
            ("off", "off"),
            (" off\n", "off"),
            ("on", "armed"),
            ("", "armed"),
            ("Off", "armed"),
            ("OFF", "armed"),
            ("false", "armed"),
            ("0", "armed"),
            ("of", "armed"),
        ):
            with self.subTest(value=value):
                with mock.patch.object(gate.settings, "gate", value):
                    self.assertEqual(gate.mode(), expected)

    def test_without_a_pattern_the_gate_is_sealed(self) -> None:
        for line in ("", "not a verifier"):
            with self.subTest(line=line):
                with_settings(self, gate="on", gate_verifier=line)
                self.assertEqual(gate.mode(), "sealed")

    def test_settings_that_say_nothing_leave_the_site_closed(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("AURORA_GATE", None)
            os.environ.pop("AURORA_GATE_VERIFIER", None)
            fresh = Settings(_env_file=None)
        self.assertEqual(fresh.gate, "on")
        self.assertEqual(fresh.gate_verifier, "")

    def test_the_gate_stands_in_front_of_everything_else(self) -> None:
        # Only the headers every answer carries are put on outside it, so
        # that the cover and the gate's refusals carry them as well.
        order = [entry.cls for entry in main.app.user_middleware]
        self.assertEqual(order[:2], [main.SecurityHeaders, gate.GateMiddleware])


# -------------------------------------------------------------- shut out ---


class LockedTests(GateCase):
    def test_every_page_is_the_cover_and_nothing_but(self) -> None:
        for path in ("/", "/bench", "/about", "/no/such/page", "/bench/"):
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.text, COVER)
                self.assertEqual(res.headers["cache-control"], "no-store")
                self.assertEqual(res.headers["vary"], "Cookie")

    def test_no_route_the_application_has_answers_without_a_session(self) -> None:
        # Read from the application as it is now, so a route added tomorrow
        # is held to this without anyone adding it here.
        asked = 0
        for route in every_route(main.app.routes):
            path = getattr(route, "path", None)
            if not path or path.startswith("/assets"):
                continue
            path = path.replace("{cid}", self.conversation).replace("{request_id}", "x")
            for method in sorted(getattr(route, "methods", None) or {"GET"}):
                with self.subTest(method=method, path=path):
                    res = self.client.request(method, path)
                    asked += 1
                    self.assertNotIn(TITLE, res.text)
                    self.assertNotIn("bootstrap", res.text)
                    if path.startswith("/api/"):
                        self.assertEqual(res.status_code, 401)
                        if method != "HEAD":
                            self.assertEqual(res.json(), {"error": "locked"})
                    elif method in ("GET", "HEAD") and "." not in path:
                        self.assertEqual(res.status_code, 200)
                    else:
                        self.assertEqual(res.status_code, 404)
        self.assertGreater(asked, 15)

    def test_a_message_sent_without_a_session_is_not_stored(self) -> None:
        res = self.client.post("/api/chat", json={"message": "hello"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(len(conversations.listing()), 1)

    def test_a_conversation_cannot_be_deleted_from_outside(self) -> None:
        res = self.client.delete(f"/api/conversations/{self.conversation}")
        self.assertEqual(res.status_code, 401)
        self.assertIsNotNone(conversations.get(self.conversation))

    def test_what_is_not_a_page_and_not_the_api_does_not_exist(self) -> None:
        for method, path in (
            ("GET", "/openapi.json"),
            ("GET", "/favicon.ico"),
            ("GET", "/.env"),
            ("GET", "/data/aurora.db"),
            ("POST", "/"),
            ("DELETE", "/bench"),
        ):
            with self.subTest(method=method, path=path):
                res = self.client.request(method, path)
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.text, "Not found")

    def test_the_built_files_are_still_served(self) -> None:
        # The cover's own script and font are among them.
        res = self.client.get("/assets/index-abc123.js")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/assets/../secret.txt").status_code, 404)

    def test_a_head_request_gets_the_cover_s_headers_and_no_body(self) -> None:
        res = self.client.head("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"")
        self.assertEqual(res.headers["content-length"], str(len(COVER)))

    def test_a_build_with_no_cover_sends_a_dark_empty_page(self) -> None:
        with mock.patch.object(gate, "cover", gate.Cover(Path(self.dir.name) / "nowhere")):
            res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, gate.BLANK)
        self.assertNotIn("not been built", res.text)

    def test_a_cookie_nobody_issued_opens_nothing(self) -> None:
        self.client.cookies.set("aurora_s", "made-up-by-a-visitor")
        self.assertEqual(self.client.get("/").text, COVER)
        self.assertEqual(self.client.get("/api/conversations").status_code, 401)


# ---------------------------------------------------------------- let in ---


class UnlockTests(GateCase):
    def test_the_right_pattern_opens_the_site(self) -> None:
        res = self.draw(CHAIN)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'{"ok":true}')

        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-page="chat"', page.text)
        self.assertIn(TITLE, page.text)
        # The same address answers with the cover for anyone else, so neither
        # answer may be kept by anything in between.
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.headers["vary"], "Cookie")
        listed = self.client.get("/api/conversations")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.headers["cache-control"], "no-store")

    def test_the_page_that_was_asked_for_is_the_one_that_opens(self) -> None:
        self.assertEqual(self.client.get("/bench").text, COVER)
        self.draw(CHAIN)
        self.assertIn('data-page="bench"', self.client.get("/bench").text)

    def test_the_session_ends_with_the_browser_and_is_not_for_scripts(self) -> None:
        cookies = self.draw(CHAIN).headers.get_list("set-cookie")
        session = next(c for c in cookies if c.startswith("aurora_s="))
        parts = {p.strip().lower() for p in session.split(";")[1:]}
        self.assertEqual(parts, {"path=/", "httponly", "samesite=lax"})

        device = next(c for c in cookies if c.startswith("aurora_d="))
        parts = {p.strip().lower() for p in device.split(";")[1:]}
        self.assertIn("path=/api/unlock", parts)
        self.assertIn("httponly", parts)
        self.assertIn("samesite=strict", parts)
        self.assertIn(f"max-age={gate.DEVICE_DAYS * 86400}", parts)

    def test_over_https_the_cookies_are_bound_to_it(self) -> None:
        client = TestClient(main.app, base_url="https://testserver")
        cookies = self.draw(CHAIN, client).headers.get_list("set-cookie")
        session = next(c for c in cookies if c.startswith("__Host-aurora_s="))
        self.assertIn("Secure", session)
        self.assertIn("Path=/;", session)
        device = next(c for c in cookies if c.startswith("__Secure-aurora_d="))
        self.assertIn("Secure", device)
        self.assertIn('data-page="chat"', client.get("/").text)

    def test_a_session_begun_in_the_clear_does_not_carry_over_to_https(self) -> None:
        token = self.draw(CHAIN).cookies["aurora_s"]
        client = TestClient(main.app, base_url="https://testserver")
        client.cookies.set("aurora_s", token)
        self.assertEqual(client.get("/").text, COVER)

    def test_every_refusal_is_the_same_answer(self) -> None:
        long_body = '{"chain":[' + ",".join(["1"] * 3000) + "]}"
        refusals = {
            "a wrong pattern": self.draw(WRONG),
            "a pattern too short to be one": self.draw(CHAIN[:-1]),
            "not a chain": self.client.post("/api/unlock", json={"chain": "3,7,12"}),
            "not JSON": self.client.post(
                "/api/unlock", content=b"{chain", headers={"content-type": "application/json"}
            ),
            "JSON sent as a form": self.client.post(
                "/api/unlock",
                content=b'{"chain":[3,7,12,1,9,14,2]}',
                headers={"content-type": "text/plain"},
            ),
            "nothing": self.client.post("/api/unlock"),
            "too much": self.client.post(
                "/api/unlock", content=long_body, headers={"content-type": "application/json"}
            ),
        }
        for name, res in refusals.items():
            with self.subTest(name):
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.content, b'{"ok":false}')
                self.assertEqual(res.headers["content-type"], "application/json")
                self.assertEqual(res.headers["cache-control"], "no-store")
                self.assertNotIn("set-cookie", res.headers)
        self.assertEqual(self.client.get("/").text, COVER)

    def test_a_body_nested_deeper_than_the_parser_goes_is_refused_the_same_way(self) -> None:
        deep = "[" * 2000 + "]" * 2000
        for name, body in (("arrays", deep), ("in the chain", '{"chain":' + deep + "}")):
            with self.subTest(name):
                # Under the cap, so it is the parser that meets it, and the
                # parser gives up on it with something other than ValueError.
                self.assertLessEqual(len(body), gate.MAX_BODY)
                with self.assertRaises(RecursionError):
                    json.loads(body)
                res = self.client.post(
                    "/api/unlock", content=body, headers={"content-type": "application/json"}
                )
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.content, b'{"ok":false}')
                self.assertNotIn("set-cookie", res.headers)

    def test_a_sealed_gate_refuses_the_right_pattern_the_same_way(self) -> None:
        with_settings(self, gate_verifier="")
        with mock.patch.object(gate, "matches") as matches:
            res = self.draw(CHAIN)
        self.assertEqual(res.content, b'{"ok":false}')
        matches.assert_not_called()
        self.assertEqual(self.client.get("/").text, COVER)

    def test_a_shape_that_is_wrong_is_refused_without_being_hashed_or_counted(self) -> None:
        with mock.patch.object(gate, "matches") as matches:
            self.draw(CHAIN[:-1])
            self.client.post("/api/unlock", json={"chain": "x"})
        matches.assert_not_called()
        self.assertEqual(db.rows("SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'gate_attempts'")[0]["n"], 0)

    def test_unlock_is_only_ever_a_post(self) -> None:
        self.assertEqual(self.client.get("/api/unlock").status_code, 401)

    def test_the_cookie_s_value_is_not_what_is_stored(self) -> None:
        token = self.draw(CHAIN).cookies["aurora_s"]
        stored = [row["token"] for row in db.rows("SELECT token FROM gate_sessions")]
        self.assertEqual(len(stored), 1)
        self.assertNotEqual(stored[0], token)
        self.assertNotIn(token, stored[0])

    def test_a_new_pattern_ends_every_session(self) -> None:
        self.draw(CHAIN)
        self.assertEqual(self.client.get("/api/conversations").status_code, 200)
        with_settings(self, gate_verifier=verifier_for(WRONG))
        self.assertEqual(self.client.get("/api/conversations").status_code, 401)
        self.assertEqual(self.client.get("/").text, COVER)

    def test_revoking_ends_every_session_at_once(self) -> None:
        self.draw(CHAIN)
        self.assertEqual(gate.revoke_all(), 1)
        self.assertEqual(self.client.get("/api/conversations").status_code, 401)

    def test_turning_the_gate_off_serves_everyone(self) -> None:
        with_settings(self, gate="off", allowed_hosts="testserver")
        page = self.client.get("/")
        self.assertIn(TITLE, page.text)
        self.assertEqual(page.headers["cache-control"], "no-cache")

    def test_another_site_s_page_cannot_use_the_session(self) -> None:
        self.draw(CHAIN)
        path = f"/api/conversations/{self.conversation}"
        for origin in ("http://elsewhere.example", "http://testserver:8080", "null"):
            with self.subTest(origin=origin):
                res = self.client.delete(path, headers={"origin": origin})
                self.assertEqual(res.status_code, 403)
                self.assertIsNotNone(conversations.get(self.conversation))
        # The site's own page says where it is from, and is believed. Behind
        # a proxy that renames the host, the name the visitor used is kept in
        # X-Forwarded-Host.
        res = self.client.delete(
            path,
            headers={"origin": "http://203.0.113.9", "x-forwarded-host": "203.0.113.9"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(conversations.get(self.conversation))

    def test_a_request_that_only_reads_is_not_asked_where_it_came_from(self) -> None:
        self.draw(CHAIN)
        res = self.client.get("/api/conversations", headers={"origin": "http://elsewhere.example"})
        self.assertEqual(res.status_code, 200)


class SessionTests(GateCase):
    def setUp(self) -> None:
        super().setUp()
        found = gate.parse_verifier(verifier_for(CHAIN))
        assert found is not None
        self.verifier = found

    def test_a_session_lasts_while_it_is_used_and_not_past_its_cap(self) -> None:
        with_settings(self, gate_session_hours=12.0, gate_idle_minutes=60.0)
        start = 1_000_000.0
        token = gate.open_session(self.verifier, start)
        now = start
        # Used every fifty minutes, it is never idle for an hour.
        while now + 3000 <= start + 12 * 3600:
            now += 3000
            self.assertTrue(gate.session_open(token, self.verifier, now))
        self.assertFalse(gate.session_open(token, self.verifier, start + 12 * 3600 + 1))

    def test_a_session_left_alone_for_an_hour_is_over(self) -> None:
        with_settings(self, gate_session_hours=12.0, gate_idle_minutes=60.0)
        token = gate.open_session(self.verifier, 1_000_000.0)
        self.assertTrue(gate.session_open(token, self.verifier, 1_000_000.0 + 3599))
        other = gate.open_session(self.verifier, 1_000_000.0)
        self.assertFalse(gate.session_open(other, self.verifier, 1_000_000.0 + 3601))

    def test_a_session_survives_the_server_restarting(self) -> None:
        token = gate.open_session(self.verifier, 1_000_000.0)
        db.close()
        db.init()
        self.assertTrue(gate.session_open(token, self.verifier, 1_000_010.0))

    def test_no_token_and_absurd_tokens_are_no_session(self) -> None:
        for token in (None, "", "x" * 500):
            with self.subTest(token=token):
                self.assertFalse(gate.session_open(token, self.verifier, 1_000_000.0))


# ---------------------------------------------------------------- guesses ---


class LimitTests(GateCase):
    NOW = 2_000_000.0

    def rows(self) -> int:
        return int(db.rows("SELECT COUNT(*) AS n FROM gate_attempts")[0]["n"])

    def test_one_address_gets_five_guesses_in_ten_minutes(self) -> None:
        for i in range(5):
            self.assertIsNotNone(gate.admit("203.0.113.5", None, self.NOW + i))
        self.assertIsNone(gate.admit("203.0.113.5", None, self.NOW + 6))
        # Someone else is not held to it.
        self.assertIsNotNone(gate.admit("203.0.113.6", None, self.NOW + 6))
        # Ten minutes on, the first guess has aged out of the window.
        self.assertIsNotNone(gate.admit("203.0.113.5", None, self.NOW + 601))

    def test_one_address_gets_twenty_guesses_in_a_day(self) -> None:
        admitted = 0
        for i in range(60):
            if gate.admit("203.0.113.5", None, self.NOW + i * 700) is not None:
                admitted += 1
        self.assertEqual(admitted, 20)

    def test_all_strangers_together_get_a_hundred_guesses_in_a_day(self) -> None:
        admitted = 0
        for i in range(150):
            if gate.admit(f"198.51.100.{i}", None, self.NOW + i) is not None:
                admitted += 1
        self.assertEqual(admitted, 100)
        # A day later the count has emptied.
        self.assertIsNotNone(gate.admit("198.51.100.200", None, self.NOW + 86400 + 200))

    def test_a_refusal_writes_nothing(self) -> None:
        for i in range(5):
            gate.admit("203.0.113.5", None, self.NOW + i)
        before = self.rows()
        for i in range(50):
            self.assertIsNone(gate.admit("203.0.113.5", None, self.NOW + 10 + i))
        self.assertEqual(self.rows(), before)

    def test_strangers_cannot_use_up_a_known_browser_s_guesses(self) -> None:
        device = gate.known_device(gate.new_device(self.NOW))
        assert device is not None
        for i in range(150):
            gate.admit(f"198.51.100.{i}", None, self.NOW + i)
        self.assertIsNone(gate.admit("198.51.100.250", None, self.NOW + 200))
        # From the same address as a stranger who is out of guesses, even.
        self.assertIsNotNone(gate.admit("198.51.100.1", device, self.NOW + 200))

    def test_a_known_browser_has_limits_of_its_own(self) -> None:
        device = gate.known_device(gate.new_device(self.NOW))
        assert device is not None
        for i in range(6):
            self.assertIsNotNone(gate.admit("203.0.113.5", device, self.NOW + i))
        self.assertIsNone(gate.admit("203.0.113.5", device, self.NOW + 7))

    def test_a_right_guess_is_not_counted_against_its_maker(self) -> None:
        attempt = gate.admit("203.0.113.5", None, self.NOW)
        assert attempt is not None
        gate.forgive(attempt)
        self.assertEqual(self.rows(), 0)

    def test_the_count_survives_the_server_restarting(self) -> None:
        for i in range(5):
            gate.admit("203.0.113.5", None, self.NOW + i)
        db.close()
        db.init()
        self.assertIsNone(gate.admit("203.0.113.5", None, self.NOW + 6))

    def test_a_device_cookie_nobody_issued_is_a_stranger(self) -> None:
        self.assertIsNone(gate.known_device("made-up-by-a-visitor"))
        self.assertIsNone(gate.known_device(None))

    def test_an_ipv6_household_is_one_address(self) -> None:
        key = gate.client_key({"client": ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", 1)})
        self.assertEqual(key, gate.client_key({"client": ("2001:db8:1:2::1", 2)}))
        self.assertNotEqual(key, gate.client_key({"client": ("2001:db8:1:3::1", 2)}))
        self.assertEqual(gate.client_key({"client": ("::ffff:203.0.113.5", 1)}), "203.0.113.5")
        self.assertEqual(gate.client_key({"client": ("203.0.113.5", 1)}), "203.0.113.5")
        self.assertEqual(gate.client_key({"client": None}), "unknown")

    def test_once_out_of_guesses_the_right_pattern_does_not_open(self) -> None:
        stranger = TestClient(main.app)
        for _ in range(5):
            self.assertEqual(self.draw(WRONG, stranger).content, b'{"ok":false}')
        with mock.patch.object(gate, "matches") as matches:
            res = self.draw(CHAIN, stranger)
        self.assertEqual(res.content, b'{"ok":false}')
        matches.assert_not_called()
        self.assertEqual(stranger.get("/").text, COVER)

    def test_a_browser_that_has_been_in_can_get_back_in_past_strangers(self) -> None:
        # The owner opens the site once, and the browser is remembered.
        self.draw(CHAIN)
        self.assertIn("aurora_d", self.client.cookies)
        gate.revoke_all()
        # Strangers then use every guess there is.
        stranger = TestClient(main.app)
        for _ in range(5):
            self.draw(WRONG, stranger)
        self.assertEqual(self.draw(CHAIN, stranger).content, b'{"ok":false}')
        # The owner's browser is not counted with them.
        self.assertEqual(self.draw(CHAIN).content, b'{"ok":true}')

    def test_opening_the_site_does_not_use_up_guesses(self) -> None:
        for _ in range(12):
            fresh = TestClient(main.app)
            self.assertEqual(self.draw(CHAIN, fresh).content, b'{"ok":true}')
        self.assertEqual(self.rows(), 0)


class WaitTests(GateCase):
    def test_a_refusal_is_held_back_and_an_opening_is_not(self) -> None:
        slept: list[float] = []

        async def note(seconds: float) -> None:
            slept.append(seconds)

        with mock.patch.object(gate, "FLOOR", 1.25), mock.patch.object(gate.asyncio, "sleep", note):
            self.draw(WRONG)
            self.client.post("/api/unlock", json={"chain": "x"})
            self.assertEqual(len(slept), 2)
            for seconds in slept:
                self.assertGreater(seconds, 0.5)
                self.assertLessEqual(seconds, 1.25)
            self.draw(CHAIN)
            self.assertEqual(len(slept), 2)

    def test_a_refused_enrol_link_is_held_back_and_one_that_works_is_not(self) -> None:
        slept: list[float] = []

        async def note(seconds: float) -> None:
            slept.append(seconds)

        with mock.patch.object(gate, "FLOOR", 1.25), mock.patch.object(gate.asyncio, "sleep", note):
            self.enrol(secrets.token_urlsafe(24))
            self.enrol("not-a-code")
            self.assertEqual(len(slept), 2)
            for seconds in slept:
                self.assertGreater(seconds, 0.5)
            self.assertEqual(self.enrol(gate.new_enrolment(time.time())).status_code, 200)
            self.assertEqual(len(slept), 2)


# ---------------------------------------------------------- known browsers ---


class DeviceTests(GateCase):
    """A browser that keeps opening the site stays known to it."""

    def test_a_new_browser_is_made_known_when_it_opens_the_site(self) -> None:
        cookie = device_cookie(self.draw(CHAIN))
        self.assertEqual(
            cookie_parts(cookie),
            {"path=/api/unlock", "httponly", "samesite=strict", f"max-age={gate.DEVICE_DAYS * 86400}"},
        )
        self.assertIsNotNone(gate.known_device(self.client.cookies["aurora_d"]))

    def test_a_known_browser_is_known_for_longer_each_time_it_opens_the_site(self) -> None:
        self.draw(CHAIN)
        token = self.client.cookies["aurora_d"]
        # Nearly half a year on, and the session long over.
        nearly = time.time() - (gate.DEVICE_DAYS - 1) * 86400
        db.write("UPDATE gate_devices SET since = ?", (nearly,))
        gate.revoke_all()

        again = self.draw(CHAIN)
        self.assertEqual(again.content, b'{"ok":true}')
        # The same cookie, sent again to last as long again.
        cookie = device_cookie(again)
        self.assertEqual(cookie.split(";")[0], f"aurora_d={token}")
        self.assertEqual(
            cookie_parts(cookie),
            {"path=/api/unlock", "httponly", "samesite=strict", f"max-age={gate.DEVICE_DAYS * 86400}"},
        )
        stored = db.rows("SELECT since FROM gate_devices")
        self.assertEqual(len(stored), 1)
        self.assertGreater(stored[0]["since"], time.time() - 60)
        # So the clearing-out of old browsers, a few days later, keeps it.
        gate.new_device(time.time() + 5 * 86400)
        self.assertIsNotNone(gate.known_device(token))

    def test_over_https_the_renewed_cookie_is_bound_to_it(self) -> None:
        client = TestClient(main.app, base_url="https://testserver")
        self.draw(CHAIN, client)
        token = client.cookies["__Secure-aurora_d"]
        gate.revoke_all()
        cookie = device_cookie(self.draw(CHAIN, client), "__Secure-aurora_d")
        self.assertEqual(cookie.split(";")[0], f"__Secure-aurora_d={token}")
        self.assertIn("secure", cookie_parts(cookie))

    def test_a_wrong_pattern_renews_nothing(self) -> None:
        self.draw(CHAIN)
        gate.revoke_all()
        db.write("UPDATE gate_devices SET since = ?", (1_000.0,))
        res = self.draw(WRONG)
        self.assertNotIn("set-cookie", res.headers)
        self.assertEqual(db.rows("SELECT since FROM gate_devices")[0]["since"], 1_000.0)


class EnrolTests(GateCase):
    """A link the server's shell prints, that makes one browser known."""

    def test_a_code_makes_the_browser_that_opens_it_known_once(self) -> None:
        code = gate.new_enrolment(time.time())
        res = self.enrol(code)
        self.assertEqual(res.status_code, 200)
        self.assertIn("This browser is now known to the site.", res.text)
        self.assertIn("draw the pattern", res.text)
        self.assertEqual(res.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(res.headers["cache-control"], "no-store")
        self.assertEqual(
            cookie_parts(device_cookie(res)),
            {"path=/api/unlock", "httponly", "samesite=strict", f"max-age={gate.DEVICE_DAYS * 86400}"},
        )
        self.assertIsNotNone(gate.known_device(self.client.cookies["aurora_d"]))
        # It opens nothing by itself.
        self.assertNotIn("aurora_s", self.client.cookies)
        self.assertEqual(self.client.get("/").text, COVER)
        self.assertEqual(self.client.get("/api/conversations").status_code, 401)
        # A second time, from anywhere, it is any other refusal.
        other = TestClient(main.app)
        self.assert_locked(self.enrol(code, other))
        self.assertNotIn("aurora_d", other.cookies)
        self.assertEqual(int(db.rows("SELECT COUNT(*) AS n FROM gate_devices")[0]["n"]), 1)

    def test_the_browser_it_makes_known_gets_in_past_strangers(self) -> None:
        now = time.time()
        for i in range(gate.STRANGER_ALL[0]):
            self.assertIsNotNone(gate.admit(f"198.51.100.{i}", None, now))
        # A new browser, then, is shut out with them, the owner's included.
        self.assertEqual(self.draw(CHAIN).content, b'{"ok":false}')
        # The link is not held to that count, and the browser it makes known
        # is counted on its own.
        self.assertEqual(self.enrol(gate.new_enrolment(now)).status_code, 200)
        self.assertEqual(self.draw(CHAIN).content, b'{"ok":true}')
        self.assertIn(TITLE, self.client.get("/").text)

    def test_a_code_that_has_expired_is_refused(self) -> None:
        code = gate.new_enrolment(time.time() - gate.ENROL_MINUTES * 60 - 1)
        self.assert_locked(self.enrol(code))
        self.assertNotIn("aurora_d", self.client.cookies)
        # A minute inside its time, it works.
        fresh = gate.new_enrolment(time.time() - (gate.ENROL_MINUTES - 1) * 60)
        self.assertEqual(self.enrol(fresh).status_code, 200)

    def test_only_the_hash_of_a_code_is_kept(self) -> None:
        code = gate.new_enrolment(time.time())
        # At least 128 bits, in the characters a path carries as they are.
        self.assertGreaterEqual(len(code), 22)
        self.assertIsNotNone(gate.ENROL_CODE.fullmatch(code))
        stored = db.rows("SELECT * FROM gate_enrolments")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["code"], hashlib.sha256(code.encode("ascii")).hexdigest())
        for value in stored[0].values():
            self.assertNotIn(code, str(value))
        path = db.settings.resolved_db_path
        for file in (path, path.with_name(path.name + "-wal")):
            if file.exists():
                self.assertNotIn(code.encode("ascii"), file.read_bytes())

    def test_every_refused_link_is_the_same_answer(self) -> None:
        used = gate.new_enrolment(time.time())
        self.assertEqual(self.enrol(used, TestClient(main.app)).status_code, 200)
        expired = gate.new_enrolment(time.time() - 3600)
        for name, code in (
            ("never made", secrets.token_urlsafe(24)),
            ("used", used),
            ("expired", expired),
            ("too short", "abc"),
            ("not a code", "a" * 20 + "!"),
            ("more path after it", f"{secrets.token_urlsafe(24)}/more"),
            ("nothing", ""),
        ):
            with self.subTest(name):
                client = TestClient(main.app)
                self.assert_locked(self.enrol(code, client))
                self.assertNotIn("aurora_d", client.cookies)

    def test_only_a_get_uses_a_code(self) -> None:
        code = gate.new_enrolment(time.time())
        self.assertEqual(self.client.head(f"/api/enroll/{code}").status_code, 401)
        self.assertEqual(self.client.post(f"/api/enroll/{code}").status_code, 401)
        self.assertEqual(self.enrol(code).status_code, 200)

    def test_codes_are_counted_like_guesses(self) -> None:
        for _ in range(gate.STRANGER_EACH[0][0]):
            self.assert_locked(self.enrol(secrets.token_urlsafe(24)))
        good = gate.new_enrolment(time.time())
        # That address is out of tries: even a good code is not looked at.
        with mock.patch.object(gate, "redeem") as redeem:
            self.assert_locked(self.enrol(good))
        redeem.assert_not_called()
        # Its guesses at the pattern are spent with them.
        self.assertEqual(self.draw(CHAIN).content, b'{"ok":false}')
        # Another address is not held to it.
        other = TestClient(main.app, client=("203.0.113.7", 50000))
        self.assertEqual(self.enrol(good, other).status_code, 200)

    def test_what_is_logged_names_the_visitor_and_never_the_code(self) -> None:
        code = gate.new_enrolment(time.time())
        with mock.patch.object(gate.log, "disabled", False):
            with self.assertLogs("aurora.gate", level="WARNING") as logged:
                self.enrol(code)
                self.enrol(code)
        text = "\n".join(logged.output)
        self.assertIn("a browser was enrolled from", text)
        self.assertIn("an enrol link that did not work, from", text)
        self.assertNotIn(code, text)

    def test_a_link_that_works_is_not_counted_against_its_maker(self) -> None:
        self.enrol(gate.new_enrolment(time.time()))
        self.assertEqual(int(db.rows("SELECT COUNT(*) AS n FROM gate_attempts")[0]["n"]), 0)

    def test_the_link_comes_before_the_session(self) -> None:
        self.draw(CHAIN)
        res = self.enrol(gate.new_enrolment(time.time()))
        self.assertEqual(res.status_code, 200)
        self.assertIn("now known", res.text)

    def test_over_https_the_cookie_is_bound_to_it(self) -> None:
        client = TestClient(main.app, base_url="https://testserver")
        res = self.enrol(gate.new_enrolment(time.time()), client)
        cookie = device_cookie(res, "__Secure-aurora_d")
        self.assertIn("secure", cookie_parts(cookie))
        self.assertEqual(self.draw(CHAIN, client).content, b'{"ok":true}')

    def test_the_command_prints_a_link_that_works(self) -> None:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(gate.main(["app.gate", "enroll"]), 0)
        path, explanation = printed.getvalue().strip().split("\n")
        self.assertTrue(path.startswith("/api/enroll/"))
        self.assertIn(f"{gate.ENROL_MINUTES} minutes", explanation)
        self.assertIn("once", explanation)
        self.assertIn("enrol links not yet used: 1", gate.status())
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertIn("enrol links not yet used: 0", gate.status())
        self.assertIn("browsers it knows: 1", gate.status())

    def test_the_command_is_in_the_help(self) -> None:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(gate.main(["app.gate", "nonsense"]), 2)
        self.assertIn("python -m app.gate enroll", printed.getvalue())


# --------------------------------------------------------------- over https ---


class HttpsOnlyTests(GateCase):
    """With AURORA_GATE_HTTPS_ONLY on, nothing is taken in the clear but from here."""

    def setUp(self) -> None:
        super().setUp()
        with_settings(self, gate_https_only=True)

    def test_the_pattern_in_the_clear_from_elsewhere_is_refused_unread(self) -> None:
        for name, client in (
            ("the test client", self.client),
            ("an address", TestClient(main.app, client=("203.0.113.9", 50000))),
        ):
            with self.subTest(name):
                with mock.patch.object(gate, "matches") as matches:
                    res = self.draw(CHAIN, client)
                matches.assert_not_called()
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.content, b'{"ok":false}')
                self.assertNotIn("set-cookie", res.headers)
                self.assertEqual(client.get("/").text, COVER)

    def test_over_https_it_opens(self) -> None:
        client = TestClient(main.app, base_url="https://testserver", client=("203.0.113.9", 50000))
        self.assertEqual(self.draw(CHAIN, client).content, b'{"ok":true}')

    def test_from_this_machine_it_opens_in_the_clear(self) -> None:
        for address in ("127.0.0.1", "::1"):
            with self.subTest(address=address):
                client = TestClient(main.app, client=(address, 50000))
                self.assertEqual(self.draw(CHAIN, client).content, b'{"ok":true}')

    def test_a_proxy_here_passing_on_someone_elsewhere_is_not_this_machine(self) -> None:
        client = TestClient(main.app, client=("127.0.0.1", 50000))
        for name, value in (
            ("x-forwarded-for", "203.0.113.9"),
            ("x-forwarded-for", "127.0.0.1, 203.0.113.9"),
            ("x-real-ip", "203.0.113.9"),
            ("forwarded", "for=203.0.113.9;proto=http"),
            ("forwarded", 'for="[2001:db8::1]:4711"'),
            ("forwarded", "for=unknown"),
            ("forwarded", "proto=http"),
        ):
            with self.subTest(name=name, value=value):
                res = client.post("/api/unlock", json={"chain": CHAIN}, headers={name: value})
                self.assertEqual(res.content, b'{"ok":false}')
        # A proxy here passing on a request made here is still here.
        res = client.post("/api/unlock", json={"chain": CHAIN}, headers={"x-forwarded-for": "127.0.0.1"})
        self.assertEqual(res.content, b'{"ok":true}')

    def test_an_enrol_link_in_the_clear_from_elsewhere_is_refused_and_kept(self) -> None:
        code = gate.new_enrolment(time.time())
        self.assert_locked(self.enrol(code))
        client = TestClient(main.app, base_url="https://testserver")
        self.assertEqual(self.enrol(code, client).status_code, 200)

    def test_off_the_clear_is_taken_as_before(self) -> None:
        with_settings(self, gate_https_only=False)
        self.assertEqual(self.draw(CHAIN).content, b'{"ok":true}')


class ThisMachineTests(unittest.TestCase):
    def scope(self, address: str | None, *headers: tuple[bytes, bytes]) -> dict:
        return {"client": (address, 1) if address else None, "headers": list(headers)}

    def test_loopback_with_nothing_forwarded_is_this_machine(self) -> None:
        for address in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            with self.subTest(address=address):
                self.assertTrue(gate.from_this_machine(self.scope(address)))

    def test_anywhere_else_is_not(self) -> None:
        for address in ("203.0.113.9", "10.0.0.1", "2001:db8::1", "testclient", "localhost", None):
            with self.subTest(address=address):
                self.assertFalse(gate.from_this_machine(self.scope(address)))

    def test_every_forwarded_line_is_read(self) -> None:
        scope = self.scope(
            "127.0.0.1",
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"X-Forwarded-For", b"203.0.113.9"),
        )
        self.assertFalse(gate.from_this_machine(scope))
        scope = self.scope("127.0.0.1", (b"forwarded", b'for="[::1]:4711", for=127.0.0.1'))
        self.assertTrue(gate.from_this_machine(scope))


# ------------------------------------------------------------ off, by name ---


class HostTests(GateCase):
    """With the gate off, only names this machine has, or that are listed."""

    def setUp(self) -> None:
        super().setUp()
        with_settings(self, gate="off", allowed_hosts="")

    def at(self, host: str, path: str = "/api/conversations", method: str = "GET"):
        # The Host header itself: the test client cannot read an IPv6 address
        # out of a base URL.
        return TestClient(main.app).request(method, path, headers={"host": host})

    def test_this_machine_s_own_names_are_answered(self) -> None:
        for host in ("localhost:8000", "LocalHost", "127.0.0.1:8000", "127.0.0.1", "[::1]:8000", "[::1]"):
            with self.subTest(host=host):
                res = self.at(host)
                self.assertEqual(res.status_code, 200)
                self.assertIn(TITLE, res.text)

    def test_another_name_is_refused(self) -> None:
        for host in (
            "testserver",
            "rebound.example:8000",
            "localhost.rebound.example",
            "127.0.0.1.nip.io",
            "[::2]:8000",
        ):
            for path in ("/api/conversations", "/"):
                with self.subTest(host=host, path=path):
                    res = self.at(host, path)
                    self.assertEqual(res.status_code, 421)
                    self.assertEqual(res.headers["content-type"], "text/plain; charset=utf-8")
                    self.assertEqual(res.content, gate.MISDIRECTED)
                    self.assertNotIn(TITLE, res.text)
        res = self.at("rebound.example", "/", "HEAD")
        self.assertEqual((res.status_code, res.content), (421, b""))

    def test_names_in_the_setting_are_answered_whatever_their_case(self) -> None:
        with_settings(self, allowed_hosts=" Aurora.Example , testserver,[2001:db8::5]:8000")
        for host in ("aurora.example:8000", "AURORA.example", "testserver", "[2001:DB8::5]", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(self.at(host).status_code, 200)
        self.assertEqual(self.at("elsewhere.example").status_code, 421)

    def test_a_request_without_a_name_is_not_answered(self) -> None:
        for host in (None, "", " ", "[::1", ":8000"):
            with self.subTest(host=host):
                self.assertFalse(gate.answers_to(host))

    def test_a_socket_to_another_name_is_closed(self) -> None:
        client = TestClient(main.app, base_url="http://rebound.example")
        with self.assertRaises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/ws"):
                pass
        self.assertEqual(closed.exception.code, 1008)

    def test_with_the_gate_on_the_name_is_not_asked(self) -> None:
        with_settings(self, gate="on")
        self.assertEqual(self.at("rebound.example", "/").text, COVER)


# ----------------------------------------------------------------- headers ---


HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": "frame-ancestors 'none'",
}


class HeaderTests(GateCase):
    def assert_headers(self, res) -> None:
        for name, value in HEADERS.items():
            self.assertEqual(res.headers.get_list(name), [value], name)

    def test_what_a_visitor_is_given_carries_them(self) -> None:
        for name, res in (
            ("the cover", self.client.get("/")),
            ("a locked /api/ path", self.client.get("/api/conversations")),
            ("a refused pattern", self.draw(WRONG)),
            ("a refused enrol link", self.enrol(secrets.token_urlsafe(24))),
            ("nothing there", self.client.get("/favicon.ico")),
            ("a built file", self.client.get("/assets/index-abc123.js")),
            ("the cover, by HEAD", self.client.head("/")),
        ):
            with self.subTest(name):
                self.assert_headers(res)

    def test_what_a_session_is_given_carries_them(self) -> None:
        opened = self.draw(CHAIN)
        for name, res in (
            ("the opening", opened),
            ("a page", self.client.get("/")),
            ("the api", self.client.get("/api/conversations")),
            ("the application's 404", self.client.get("/api/no/such/thing")),
        ):
            with self.subTest(name):
                self.assert_headers(res)

    def test_the_gate_off_carries_them_too(self) -> None:
        with_settings(self, gate="off", allowed_hosts="testserver")
        self.assert_headers(self.client.get("/"))
        self.assert_headers(TestClient(main.app, base_url="http://rebound.example").get("/"))

    def test_a_header_the_response_sets_is_kept_and_a_stream_goes_through(self) -> None:
        start = {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"X-Frame-Options", b"SAMEORIGIN"), (b"content-security-policy", b"default-src 'self'")],
        }
        pieces = [
            {"type": "http.response.body", "body": b"one", "more_body": True},
            {"type": "http.response.body", "body": b"two", "more_body": False},
        ]

        async def inner(scope, receive, send) -> None:
            await send(start)
            for piece in pieces:
                await send(piece)

        sent: list[dict] = []

        async def send(message) -> None:
            sent.append(message)

        async def receive() -> dict:
            return {"type": "http.request"}

        asyncio.run(main.SecurityHeaders(inner)({"type": "http"}, receive, send))
        headers = [(bytes(k).lower(), v) for k, v in sent[0]["headers"]]
        self.assertEqual([v for k, v in headers if k == b"x-frame-options"], [b"SAMEORIGIN"])
        self.assertEqual([v for k, v in headers if k == b"content-security-policy"], [b"default-src 'self'"])
        self.assertIn((b"x-content-type-options", b"nosniff"), headers)
        self.assertIn((b"referrer-policy", b"no-referrer"), headers)
        # The body is handed on as it was sent, piece by piece.
        self.assertEqual(len(sent), 3)
        for given, passed in zip(pieces, sent[1:]):
            self.assertIs(passed, given)
        # The original start message is not changed in place.
        self.assertEqual(len(start["headers"]), 2)

    def test_anything_but_http_goes_through_untouched(self) -> None:
        seen = []

        async def inner(scope, receive, send) -> None:
            seen.append(send)

        async def send(message) -> None:
            pass

        asyncio.run(main.SecurityHeaders(inner)({"type": "lifespan"}, None, send))
        self.assertIs(seen[0], send)


# ----------------------------------------------------------------- through ---


class GatedChatTests(ChatCase):
    """A request that is let through reaches the application as it came."""

    def setUp(self) -> None:
        super().setUp()
        # The test client's name, which the gate, off, does not otherwise know.
        with_settings(self, allowed_hosts="testserver")

    def test_a_stream_through_the_gate_is_the_stream_without_it(self) -> None:
        open_stream = self.chat("What is a curtain made of?")

        quick_gate(self)
        with_settings(self, gate="on", gate_verifier=verifier_for(CHAIN))
        self.assertEqual(self.client.post("/api/chat", json={"message": "hi"}).status_code, 401)
        self.assertEqual(self.client.post("/api/unlock", json={"chain": CHAIN}).json(), {"ok": True})
        gated_stream = self.chat("What is a curtain made of?")

        self.assertEqual(shape(gated_stream), shape(open_stream))
        self.assertEqual(len(conversations.listing()), 2)


class CommandTests(GateCase):
    def test_status_says_how_the_gate_stands(self) -> None:
        self.draw(WRONG)
        self.draw(CHAIN)
        text = gate.status()
        self.assertIn("gate: armed", text)
        self.assertIn("sessions open: 1", text)
        self.assertIn("strangers: 1 of 100", text)
        self.assertIn("browsers it knows: 1", text)
        self.assertNotIn(self.client.cookies["aurora_s"], text)

    def test_what_is_logged_names_the_visitor_and_never_the_pattern(self) -> None:
        with mock.patch.object(gate.log, "disabled", False):
            with self.assertLogs("aurora.gate", level="WARNING") as logged:
                self.draw(WRONG)
                self.draw(CHAIN)
        text = "\n".join(logged.output)
        self.assertIn("a wrong pattern from", text)
        self.assertIn("the gate was opened from", text)
        for chain in (WRONG, CHAIN):
            self.assertNotIn(",".join(str(star) for star in chain), text.replace(" ", ""))

    def test_reset_forgets_the_guesses(self) -> None:
        for _ in range(5):
            self.draw(WRONG)
        self.assertEqual(gate.forget_attempts(), 5)
        self.assertEqual(self.draw(CHAIN).content, b'{"ok":true}')


if __name__ == "__main__":
    unittest.main()
