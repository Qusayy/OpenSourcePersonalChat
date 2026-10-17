"""The gate: what a visitor without a session is given, and what opens it.

Needs the app's own requirements; without them the whole module is skipped.
The rest of the suite runs with the gate off (tests/__init__.py). Here it is
on, with a pattern made up for the purpose, a hash cheap enough to compute a
few hundred times, and no wait before a refusal.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from dotenv import dotenv_values
    from fastapi.testclient import TestClient

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
        self.assertIs(main.app.user_middleware[0].cls, gate.GateMiddleware)


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
        with_settings(self, gate="off")
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


# ----------------------------------------------------------------- through ---


class GatedChatTests(ChatCase):
    """A request that is let through reaches the application as it came."""

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
        self.assertIn("browsers that have opened it: 1", text)
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
