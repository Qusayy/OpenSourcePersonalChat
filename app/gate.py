"""The gate: who is let past the cover.

A visitor is sent a night sky and nothing else. Stars in it can be joined with
a line, and joining the right ones in the right order opens the site. The sky
is in the public repository and so is this file, so none of that is a secret
and none of it is the lock. The lock is here, on the server:

* the order of the stars, which only the owner knows;
* a verifier, a slow hash of that order, read from ``AURORA_GATE_VERIFIER``
  in the server's own ``.env`` and never committed;
* a limit on how many guesses anyone gets.

Until a browser holds a session, every page it asks for is the cover, every
``/api/`` path answers 401, and nothing else exists. That is decided before
the application sees the request, by one piece of ASGI middleware, so a route
added later is closed without anyone remembering to close it.

The site is open to everyone only when ``AURORA_GATE`` is exactly ``off``. A
missing ``.env``, a typing mistake or a verifier that does not parse leaves it
sealed: the sky is shown and nothing opens it. Off, it still answers only to
this machine's own names and to those in ``AURORA_ALLOWED_HOSTS``, so that a
page elsewhere cannot point a name of its own at a server left open on a
developer's machine.

A browser the site knows, one that has opened it before or was enrolled, has
guesses of its own, apart from strangers'. Every time it opens the site it is
known for another DEVICE_DAYS days. A new browser is made known without the
pattern by a link the server's own shell prints, good once and for fifteen
minutes; it still has to draw the pattern.

    python -m app.gate status     what the gate is doing, and for whom
    python -m app.gate revoke     end every open session
    python -m app.gate reset      forget the guesses counted so far
    python -m app.gate enroll     a one-time link that makes a browser known
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from .api import frontend
from .config import settings
from .store import db

log = logging.getLogger("aurora.gate")

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

UNLOCK = "/api/unlock"
# GET /api/enroll/<code>: makes the browser that asks known. See new_enrolment.
ENROL = "/api/enroll/"
ENROL_MINUTES = 15
# What a code may look like: what secrets.token_urlsafe writes, and no longer
# than a cookie the gate would look up. Anything else is refused unlooked-at.
ENROL_CODE = re.compile(r"[A-Za-z0-9_-]{16,128}")

# What a pattern may be. The cover sends nothing shorter than MIN_STARS; the
# rest is so that a request made by hand is refused before it costs anything.
MIN_STARS = 7
MAX_STARS = 48
MAX_STAR = 63
MAX_BODY = 4096

# How many guesses, in how many seconds. A browser that has opened the site
# before is counted on its own, so that strangers playing with the sky cannot
# use up the owner's tries; everyone else is counted by address and then all
# together, and it is that last number that sets how long guessing takes.
STRANGER_EACH = ((5, 600), (20, 86400))
STRANGER_ALL = (100, 86400)
DEVICE_EACH = ((6, 600), (30, 86400))
DEVICE_ALL = (60, 86400)
DAY = 86400
# Checks running at once. Beyond this a guess is refused unchecked.
IN_FLIGHT = 32
# Seconds before a refusal is sent, from when the request arrived, whatever
# the reason for it: wrong, throttled, malformed and sealed are then the same
# answer after the same wait. Opening is not held back.
FLOOR = 1.25

SESSION_COOKIE = "aurora_s"
DEVICE_COOKIE = "aurora_d"
DEVICE_DAYS = 180
# A session's last-seen time is written at most this often, not on every poll.
SEEN_EVERY = 60

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ------------------------------------------------------------- the pattern ---

# gate1.<iterations>.<salt, 32 hex>.<hash, 64 hex>. Letters, digits and dots
# only: the line is read by systemd and by python-dotenv, and both give `$`,
# `#`, quotes and spaces meanings of their own.
VERIFIER = re.compile(r"gate1\.(\d{1,7})\.([0-9a-f]{32})\.([0-9a-f]{64})")
MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 2_000_000


@dataclass(frozen=True)
class Verifier:
    iterations: int
    salt: bytes
    digest: bytes
    # A short name for this verifier, kept with each session it opens, so that
    # changing the pattern ends them all.
    mark: str


@lru_cache(maxsize=8)
def parse_verifier(raw: str) -> Verifier | None:
    found = VERIFIER.fullmatch(raw.strip())
    if not found:
        return None
    iterations = int(found.group(1))
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        return None
    return Verifier(
        iterations=iterations,
        salt=bytes.fromhex(found.group(2)),
        digest=bytes.fromhex(found.group(3)),
        mark=hashlib.sha256(raw.strip().encode("ascii")).hexdigest()[:16],
    )


def canonical(chain: list[int]) -> bytes:
    """The bytes that are hashed. web/src/cover/pattern.ts builds the same."""
    return b"aurora-gate:v1:" + ",".join(str(star) for star in chain).encode("ascii")


def valid_shape(body: object) -> list[int] | None:
    """The chain a request carries, or None if it carries anything else."""
    chain = body.get("chain") if isinstance(body, dict) else None
    if not isinstance(chain, list) or not MIN_STARS <= len(chain) <= MAX_STARS:
        return None
    for star in chain:
        # `type`, not isinstance: True is an int, and is not a star.
        if type(star) is not int or not 0 <= star <= MAX_STAR:
            return None
    return chain


# One check at a time. The hash is the only expensive thing here, and the
# machine has two cores with a language model on them.
_hashing = threading.Lock()


def matches(verifier: Verifier, chain: list[int]) -> bool:
    with _hashing:
        digest = hashlib.pbkdf2_hmac(
            "sha256", canonical(chain), verifier.salt, verifier.iterations, len(verifier.digest)
        )
    return hmac.compare_digest(digest, verifier.digest)


def mode() -> str:
    """``off``, ``armed`` or ``sealed``, read from the settings every time."""
    if settings.gate.strip() == "off":
        return "off"
    return "armed" if parse_verifier(settings.gate_verifier) else "sealed"


# ----------------------------------------------------------------- storage ---

# The gate's own tables. They are made here, the first time they are wanted on
# a connection, and not in db.SCHEMA: nothing else reads them.
SCHEMA = """
CREATE TABLE IF NOT EXISTS gate_sessions (
    token    TEXT PRIMARY KEY,   -- SHA-256 of the cookie's value, never the value
    verifier TEXT NOT NULL,      -- the mark of the pattern that opened it
    opened   REAL NOT NULL,
    seen     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_devices (
    token TEXT PRIMARY KEY,      -- hashed the same way
    since REAL NOT NULL          -- when it last opened the site, or was enrolled
);
CREATE TABLE IF NOT EXISTS gate_attempts (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    at   REAL NOT NULL,
    lane TEXT NOT NULL,          -- 'stranger' or 'device'
    who  TEXT NOT NULL           -- an address, or a device's hashed token
);
CREATE INDEX IF NOT EXISTS gate_attempts_at ON gate_attempts(at);
CREATE TABLE IF NOT EXISTS gate_enrolments (
    code    TEXT PRIMARY KEY,    -- SHA-256 of the code, never the code
    expires REAL NOT NULL,
    used    REAL                 -- when it made a browser known; NULL until then
);
"""

_made_on: sqlite3.Connection | None = None


def _tables() -> None:
    global _made_on
    conn = db.connection()
    if _made_on is not conn:
        with db.transaction() as held:
            held.executescript(SCHEMA)
        _made_on = conn


def _hashed(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _count(lane: str, who: str | None, since: float) -> int:
    if who is None:
        rows = db.rows(
            "SELECT COUNT(*) AS n FROM gate_attempts WHERE lane = ? AND at > ?", (lane, since)
        )
    else:
        rows = db.rows(
            "SELECT COUNT(*) AS n FROM gate_attempts WHERE lane = ? AND who = ? AND at > ?",
            (lane, who, since),
        )
    return int(rows[0]["n"])


def admit(address: str, device: str | None, now: float, *, together: bool = True) -> int | None:
    """Count a guess against whoever is making it, or refuse it.

    Returns the number of the guess, or None when it is over a limit. A refusal
    writes nothing, so the table cannot be grown by being refused.

    ``together=False`` holds a guess to the limits of the one making it and
    not to the count of everyone in its lane: an enrol code, which nobody can
    guess, and which is how the owner gets in when strangers have used that
    count up.
    """
    _tables()
    db.write("DELETE FROM gate_attempts WHERE at < ?", (now - DAY,))
    if device:
        lane, who, each, everyone = "device", device, DEVICE_EACH, DEVICE_ALL
    else:
        lane, who, each, everyone = "stranger", address, STRANGER_EACH, STRANGER_ALL
    for limit, window in each:
        if _count(lane, who, now - window) >= limit:
            return None
    if together and _count(lane, None, now - everyone[1]) >= everyone[0]:
        return None
    with db.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO gate_attempts (at, lane, who) VALUES (?, ?, ?)", (now, lane, who)
        )
    return cursor.lastrowid


def forgive(attempt: int) -> None:
    """A guess that was right is not held against the one who made it."""
    db.write("DELETE FROM gate_attempts WHERE id = ?", (attempt,))


def open_session(verifier: Verifier, now: float) -> str:
    """Start a session and return the value its cookie carries."""
    _tables()
    db.write(
        "DELETE FROM gate_sessions WHERE opened < ?", (now - settings.gate_session_hours * 3600,)
    )
    token = secrets.token_urlsafe(32)
    db.write(
        "INSERT INTO gate_sessions (token, verifier, opened, seen) VALUES (?, ?, ?, ?)",
        (_hashed(token), verifier.mark, now, now),
    )
    return token


def session_open(token: str | None, verifier: Verifier, now: float) -> bool:
    if not token or len(token) > 128:
        return False
    _tables()
    key = _hashed(token)
    rows = db.rows("SELECT verifier, opened, seen FROM gate_sessions WHERE token = ?", (key,))
    if not rows:
        return False
    row = rows[0]
    if row["verifier"] != verifier.mark:
        return False
    if now - row["opened"] > settings.gate_session_hours * 3600:
        return False
    if now - row["seen"] > settings.gate_idle_minutes * 60:
        return False
    if now - row["seen"] > SEEN_EVERY:
        db.write("UPDATE gate_sessions SET seen = ? WHERE token = ?", (now, key))
    return True


def known_device(token: str | None) -> str | None:
    """The stored name of a browser that has opened the site before, or None."""
    if not token or len(token) > 128:
        return None
    _tables()
    key = _hashed(token)
    return key if db.rows("SELECT 1 FROM gate_devices WHERE token = ?", (key,)) else None


def new_device(now: float) -> str:
    _tables()
    db.write("DELETE FROM gate_devices WHERE since < ?", (now - DEVICE_DAYS * DAY,))
    token = secrets.token_urlsafe(32)
    db.write("INSERT INTO gate_devices (token, since) VALUES (?, ?)", (_hashed(token), now))
    return token


def renew_device(device: str, now: float) -> None:
    """A known browser that opens the site is known for DEVICE_DAYS from now.

    Its cookie is sent again with a fresh Max-Age at the same time, so the
    owner's browser is never quietly turned back into a stranger, to be
    counted with everyone else, half a year after it was first let in.
    """
    db.write("UPDATE gate_devices SET since = ? WHERE token = ?", (now, device))


def device_cookie(name: str, token: str, secure: bool) -> str:
    """The Set-Cookie line that makes a browser known: sent back only to the
    unlock address, never to a script, and never from another site's page."""
    tail = "; Secure" if secure else ""
    return (
        f"{name}={token}; Path={UNLOCK}; HttpOnly; SameSite=Strict; "
        f"Max-Age={DEVICE_DAYS * DAY}{tail}"
    )


def new_enrolment(now: float) -> str:
    """A one-time code that makes the browser it is opened in known.

    For a browser the site has never seen while strangers have used up every
    guess they are allowed: it is then counted on its own, and gets in with
    the pattern. Only its hash is kept. It works once, for ENROL_MINUTES, and
    it opens nothing by itself.
    """
    _tables()
    db.write("DELETE FROM gate_enrolments WHERE expires < ?", (now,))
    code = secrets.token_urlsafe(24)  # 192 bits
    db.write(
        "INSERT INTO gate_enrolments (code, expires) VALUES (?, ?)",
        (_hashed(code), now + ENROL_MINUTES * 60),
    )
    return code


def redeem(code: str, now: float) -> bool:
    """Use an enrol code up. True once, for a code that was made and has not
    expired; a code used, expired or never made is the same False."""
    _tables()
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE gate_enrolments SET used = ? WHERE code = ? AND used IS NULL AND expires > ?",
            (now, _hashed(code), now),
        )
    return cursor.rowcount == 1


def revoke_all() -> int:
    _tables()
    count = int(db.rows("SELECT COUNT(*) AS n FROM gate_sessions")[0]["n"])
    db.write("DELETE FROM gate_sessions")
    return count


def forget_attempts() -> int:
    _tables()
    count = int(db.rows("SELECT COUNT(*) AS n FROM gate_attempts")[0]["n"])
    db.write("DELETE FROM gate_attempts")
    return count


# ------------------------------------------------------------------- cover ---

# Sent if the build has no cover in it. Dark, and empty: a visitor is never
# shown the application's "not built" page, which says what this is.
BLANK = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Aurora</title>
<style>html, body { height: 100%; margin: 0; background: #03060c; }</style>
</head>
<body></body>
</html>
"""

# What an enrol link answers once it has worked. It says no more than that.
ENROLLED = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Aurora</title>
<style>
html, body { height: 100%; margin: 0; background: #03060c; color: #c9d3e0; }
body { display: grid; place-items: center; font: 1rem/1.5 system-ui, sans-serif; }
p { max-width: 30rem; margin: 0 1rem; text-align: center; }
a { color: inherit; }
</style>
</head>
<body><p>This browser is now known to the site. <a href="/">Open the site</a> and draw the pattern.</p></body>
</html>
"""


class Cover:
    """The page a visitor without a session is given, whatever they asked for.

    It is `cover.html` from the build, sent as it is: nothing is written into
    it, so nothing of the application can be.
    """

    def __init__(self, dist: Path | None = None):
        self.dist = dist
        self._page: tuple[float, bytes] | None = None

    def body(self) -> bytes:
        path = (self.dist or frontend.DIST) / "cover.html"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._page = None
            return BLANK
        if self._page is None or self._page[0] != mtime:
            self._page = (mtime, path.read_bytes())
        return self._page[1]


cover = Cover()


# ------------------------------------------------------------ the requests ---


def client_key(scope: Scope) -> str:
    """Who a guess is counted against: an IPv4 address, or an IPv6 /64.

    The address is the one uvicorn worked out, which behind the proxy on this
    host is the visitor's and not the proxy's. One household's IPv6 addresses
    are one count: a /64 is what a home is given, and it can make as many
    addresses inside it as it likes.
    """
    client = scope.get("client")
    try:
        address = ipaddress.ip_address(client[0] if client else "")
    except ValueError:
        return "unknown"
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False).network_address)
    return str(address)


def bare_host(value: str) -> str:
    """A host as a Host header or a forwarded address gives it, without the
    port or the brackets round an IPv6 address, in lower case."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        # No closing bracket: kept as it is, which matches nothing.
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        value = value.partition(":")[0]
    return value.rstrip(".")


# This machine's own names. ``AURORA_ALLOWED_HOSTS`` adds to them.
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def answers_to(host: str | None) -> bool:
    """Whether the gate, when off, serves a request made to this host name.

    A page on someone else's site can have its own name looked up as this
    machine's address, and then ask a server left open on loopback for
    anything it holds, as if it were the site's own page. The browser still
    says the name it used, in Host, and a page cannot change that.
    """
    name = bare_host(host or "")
    if not name:
        return False
    if name in LOOPBACK_NAMES:
        return True
    allowed = {bare_host(item) for item in settings.allowed_hosts.split(",")}
    allowed.discard("")
    return name in allowed


def _loopback(text: str) -> bool:
    try:
        address = ipaddress.ip_address(bare_host(text))
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _forwarded_for(name: bytes, value: str) -> list[str]:
    """The addresses a proxy header names. ``Forwarded`` puts them after
    ``for=``; the others are lists of addresses."""
    if name != b"forwarded":
        return [part for part in (p.strip() for p in value.split(",")) if part]
    found = []
    for element in value.split(","):
        for pair in element.split(";"):
            key, _, said = pair.partition("=")
            if key.strip().lower() == "for":
                found.append(said.strip().strip('"'))
    return found


PROXY_HEADERS = (b"forwarded", b"x-forwarded-for", b"x-real-ip")


def from_this_machine(scope: Scope) -> bool:
    """Whether a request was made on this machine, and not passed on by a
    proxy here for someone elsewhere.

    The address is loopback, and so is every address a proxy header names. A
    proxy on this host is itself on loopback; uvicorn trusts it and puts the
    visitor's address in its place, but should that ever not happen, the
    header it added still says where the visitor is. A header that names no
    address, or something that is not one, is not believed to be this machine.
    """
    client = scope.get("client")
    if not client or not _loopback(str(client[0])):
        return False
    for raw_name, raw_value in scope.get("headers") or ():
        name = raw_name.lower()
        if name in PROXY_HEADERS:
            named = _forwarded_for(name, raw_value.decode("latin-1"))
            if not named or not all(_loopback(address) for address in named):
                return False
    return True


def carried_safely(scope: Scope, secure: bool) -> bool:
    """Whether the pattern, or an enrol code, may be taken from this request.

    Always, unless ``AURORA_GATE_HTTPS_ONLY`` is on: then over HTTPS, or in
    the clear only from this machine itself (a tunnel, or curl on the box),
    where nobody on the way can read it.
    """
    return secure or not settings.gate_https_only or from_this_machine(scope)


def read_headers(scope: Scope) -> dict[str, str]:
    """The request's headers by lower-case name. Repeated cookie lines are joined."""
    found: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or ():
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        found[name] = f"{found[name]}; {value}" if name == "cookie" and name in found else value
    return found


def read_cookies(header: str | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in (header or "").split(";"):
        name, eq, value = part.strip().partition("=")
        if eq and name:
            cookies[name] = value
    return cookies


def cookie_names(secure: bool) -> tuple[str, str]:
    """The session's and the device's cookie names for this scheme.

    Over HTTPS they carry the prefixes that make a browser refuse to accept
    them from anything but this origin, over HTTPS. They are different names
    on purpose: a session begun in the clear does not carry over.
    """
    if secure:
        return f"__Host-{SESSION_COOKIE}", f"__Secure-{DEVICE_COOKIE}"
    return SESSION_COOKIE, DEVICE_COOKIE


def foreign(headers: dict[str, str]) -> bool:
    """Whether a request that changes something was sent by another site's page.

    A browser says where a request came from. If that is not the host being
    asked, the request is someone else's page using this visitor's session.
    The host is compared and the scheme is not: a proxy that ends TLS may not
    say that it did. A request with no Origin was not made by a page.
    """
    origin = headers.get("origin")
    if not origin:
        return False
    here = {headers.get("host"), headers.get("x-forwarded-host")}
    return urlsplit(origin).netloc not in here


def page_like(path: str) -> bool:
    """A path that could be a page: not a file, by the look of its last part."""
    return "." not in path.rsplit("/", 1)[-1]


async def respond(
    send: Send,
    status: int,
    body: bytes,
    content_type: str,
    *,
    cookies: tuple[str, ...] = (),
    head: bool = False,
) -> None:
    headers = [
        (b"content-type", content_type.encode("ascii")),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
        (b"vary", b"Cookie"),
    ]
    headers.extend((b"set-cookie", cookie.encode("ascii")) for cookie in cookies)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else body})


async def read_body(receive: Receive) -> bytes | None:
    """The whole request body, or None if it is too long or the visitor left."""
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        body += message.get("body", b"")
        if len(body) > MAX_BODY:
            return None
        if not message.get("more_body"):
            return body


JSON = "application/json"
REFUSED = b'{"ok":false}'
OPENED = b'{"ok":true}'
LOCKED = b'{"error":"locked"}'
HTML = "text/html; charset=utf-8"
TEXT = "text/plain; charset=utf-8"
MISDIRECTED = b"This server does not answer to that name.\n"


class GateMiddleware:
    """Decides, from the request alone, whether the application sees it.

    Plain ASGI and not `@app.middleware("http")`: that kind passes every
    response through a second task and a queue, and the chat stream is timed
    by when its generator is closed. A request that is let through here goes
    to the application exactly as it came.
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]):
        self.app = app
        self.checking = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            announce()
            await self.app(scope, receive, send)
            return
        state = mode()
        if state == "off":
            if scope["type"] in ("http", "websocket") and not answers_to(
                read_headers(scope).get("host")
            ):
                if scope["type"] == "websocket":
                    await receive()
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    head = scope.get("method") == "HEAD"
                    await respond(send, 421, MISDIRECTED, TEXT, head=head)
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # The application has no sockets; if it grows one it is closed too.
            if scope["type"] == "websocket":
                await receive()
                await send({"type": "websocket.close", "code": 1008})
            return

        path: str = scope["path"]
        method: str = scope["method"]
        headers = read_headers(scope)
        cookies = read_cookies(headers.get("cookie"))
        secure = scope.get("scheme") == "https"
        session_name, _ = cookie_names(secure)
        verifier = parse_verifier(settings.gate_verifier)

        if path == UNLOCK and method == "POST":
            await self.unlock(scope, receive, send, verifier, headers, cookies, secure)
            return
        if path.startswith(ENROL) and method == "GET":
            # Before the session: whoever opens the link is enrolled, or is
            # refused as anyone asking for an /api/ path is.
            await self.enrol(scope, send, path[len(ENROL):], secure)
            return

        if verifier is not None and self.has_session(cookies.get(session_name), verifier):
            if method in UNSAFE and foreign(headers):
                await respond(send, 403, b'{"error":"forbidden"}', JSON)
                return
            await self.app(scope, receive, private(send))
            return

        head = method == "HEAD"
        if method in ("GET", "HEAD") and path.startswith("/assets/"):
            # Built files, named for their contents and public already: the
            # repository has them. The cover's own script and font are here.
            await self.app(scope, receive, send)
        elif path == "/api" or path.startswith("/api/"):
            await respond(send, 401, LOCKED, JSON, head=head)
        elif method in ("GET", "HEAD") and page_like(path):
            # At whatever address was asked for, so that the page behind it
            # is the one that opens.
            await respond(send, 200, cover.body(), HTML, head=head)
        else:
            await respond(send, 404, b"Not found", TEXT, head=head)

    def has_session(self, token: str | None, verifier: Verifier) -> bool:
        try:
            return session_open(token, verifier, time.time())
        except Exception:  # noqa: BLE001 - a gate that cannot tell stays shut
            log.exception("the gate could not look a session up")
            return False

    async def unlock(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        verifier: Verifier | None,
        headers: dict[str, str],
        cookies: dict[str, str],
        secure: bool,
    ) -> None:
        arrived = time.monotonic()
        body = await read_body(receive)
        chain = None
        if (
            body is not None
            and headers.get("content-type", "").startswith(JSON)
            and carried_safely(scope, secure)
        ):
            try:
                chain = valid_shape(json.loads(body))
            except Exception:  # noqa: BLE001 - nested past the parser's depth, too: refused like the rest
                chain = None

        if chain is not None and verifier is not None and self.checking < IN_FLIGHT:
            session_name, device_name = cookie_names(secure)
            now = time.time()
            address = client_key(scope)
            try:
                device = known_device(cookies.get(device_name))
                attempt = admit(address, device, now)
            except Exception:  # noqa: BLE001 - a gate that cannot count does not check
                log.exception("the gate could not count a guess")
                attempt = None
            if attempt is not None:
                self.checking += 1
                try:
                    right = await asyncio.to_thread(matches, verifier, chain)
                finally:
                    self.checking -= 1
                made: list[str] = []
                if right:
                    try:
                        forgive(attempt)
                        tail = "; Secure" if secure else ""
                        made.append(
                            f"{session_name}={open_session(verifier, now)}; Path=/; HttpOnly; "
                            f"SameSite=Lax{tail}"
                        )
                        if device is None:
                            token = new_device(now)
                        else:
                            # The cookie it came with: known_device found it.
                            token = cookies[device_name]
                            renew_device(device, now)
                        made.append(device_cookie(device_name, token, secure))
                    except Exception:  # noqa: BLE001 - no session was made, so none is claimed
                        log.exception("the gate could not open a session")
                        made = []
                if made:
                    log.warning("the gate was opened from %s", address)
                    await respond(send, 200, OPENED, JSON, cookies=tuple(made))
                    return
                # Never the chain itself: the owner's own near miss, read in a
                # log, would give most of the pattern away.
                if not right:
                    log.warning(
                        "a wrong pattern from %s (%s)",
                        address,
                        "known browser" if device else "stranger",
                    )

        await asyncio.sleep(max(0.0, FLOOR - (time.monotonic() - arrived)))
        await respond(send, 200, REFUSED, JSON)

    async def enrol(self, scope: Scope, send: Send, code: str, secure: bool) -> None:
        """Make the browser that opens an enrol link known, or refuse it.

        A code is counted like a guess, against the address it came from, and
        not against everyone together: strangers using that count up is what
        the link is for. It could not be guessed in any case. A refusal, for
        whatever reason, is what any /api/ path answers a visitor without a
        session, after the same wait as a refused pattern.
        """
        arrived = time.monotonic()
        token = None
        if ENROL_CODE.fullmatch(code) and carried_safely(scope, secure):
            now = time.time()
            address = client_key(scope)
            attempt = None
            try:
                attempt = admit(address, None, now, together=False)
                if attempt is not None and redeem(code, now):
                    forgive(attempt)
                    token = new_device(now)
            except Exception:  # noqa: BLE001 - a gate that cannot count does not enrol
                log.exception("the gate could not enrol a browser")
                token = None
            if token is not None:
                log.warning("a browser was enrolled from %s", address)
                _, device_name = cookie_names(secure)
                cookie = device_cookie(device_name, token, secure)
                await respond(send, 200, ENROLLED, HTML, cookies=(cookie,))
                return
            # Only what was counted: one past its limit is not logged either.
            if attempt is not None:
                log.warning("an enrol link that did not work, from %s", address)

        await asyncio.sleep(max(0.0, FLOOR - (time.monotonic() - arrived)))
        await respond(send, 401, LOCKED, JSON)


def private(send: Send) -> Send:
    """Mark what a session is shown as not to be kept.

    The same address now answers with two different documents, the cover and
    the page, and the page carries what the site holds. Neither a proxy nor
    the browser's own cache may hand one to someone owed the other. A stream
    is left as it is.
    """

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers") or ())
            kind = next((v for k, v in headers if k.lower() == b"content-type"), b"")
            if kind.startswith((b"text/html", b"application/json")):
                headers = [(k, v) for k, v in headers if k.lower() not in (b"cache-control", b"vary")]
                headers.append((b"cache-control", b"no-store"))
                headers.append((b"vary", b"Cookie"))
                message = {**message, "headers": headers}
        await send(message)

    return wrapped


def announce() -> None:
    """Say at start-up which way the gate is set, where the journal keeps it."""
    state = mode()
    if state == "off":
        log.warning("the gate is off: the whole site is served to everyone")
    elif state == "sealed":
        log.error(
            "the gate is sealed: AURORA_GATE_VERIFIER is missing or malformed, "
            "so only the cover is served and nothing opens it"
        )
    else:
        log.warning("the gate is armed")


# --------------------------------------------------------------------- CLI ---


def status() -> str:
    _tables()
    now = time.time()
    verifier = parse_verifier(settings.gate_verifier)
    lines = [f"gate: {mode()}"]
    sessions = db.rows("SELECT verifier, opened, seen FROM gate_sessions ORDER BY opened DESC")
    live = [
        row
        for row in sessions
        if verifier is not None
        and row["verifier"] == verifier.mark
        and now - row["opened"] <= settings.gate_session_hours * 3600
        and now - row["seen"] <= settings.gate_idle_minutes * 60
    ]
    lines.append(f"sessions open: {len(live)}")
    for row in live:
        opened = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["opened"]))
        lines.append(f"  opened {opened}, last seen {int((now - row['seen']) / 60)} min ago")
    for lane, (limit, window) in (("stranger", STRANGER_ALL), ("device", DEVICE_ALL)):
        used = _count(lane, None, now - window)
        lines.append(f"wrong guesses in the last day, {lane}s: {used} of {limit}")
    devices = int(db.rows("SELECT COUNT(*) AS n FROM gate_devices")[0]["n"])
    lines.append(f"browsers it knows: {devices}")
    waiting = db.rows(
        "SELECT COUNT(*) AS n FROM gate_enrolments WHERE used IS NULL AND expires > ?", (now,)
    )
    lines.append(f"enrol links not yet used: {int(waiting[0]['n'])}")
    return "\n".join(lines)


def enroll() -> str:
    """What `python -m app.gate enroll` prints: the link's path, and what to do with it."""
    code = new_enrolment(time.time())
    return (
        f"{ENROL}{code}\n"
        f"Open that path on the site in the browser it should know, once, within "
        f"{ENROL_MINUTES} minutes; that browser then draws the pattern, counted apart "
        f"from strangers."
    )


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        print(status())
    elif command == "revoke":
        print(f"ended {revoke_all()} session(s)")
    elif command == "reset":
        print(f"forgot {forget_attempts()} guess(es)")
    elif command == "enroll":
        print(enroll())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
