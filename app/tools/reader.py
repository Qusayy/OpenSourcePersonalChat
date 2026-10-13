"""read_url — fetch a page and hand the model its readable text.

This is the only tool that takes an arbitrary address from an untrusted user
and makes the server connect to it, so the guard is the point of the file.
Without it, `http://169.254.169.254/latest/meta-data/` turns a chat box into a
cloud-credential reader, and `http://127.0.0.1:8000/` turns it into a mirror
for anything else bound to localhost.

Layers, in order:

1. Scheme must be http or https. No file:, gopher:, data:, ftp:.
2. Resolve the hostname and check **every** address it answers with. One
   address in a private, loopback, link-local, CGNAT, reserved or multicast
   range fails the whole request.
3. Redirects are followed manually, at most three hops, re-validating the
   destination at every hop — a public URL that 302s to 10.0.0.1 is the
   classic bypass.
4. Body is streamed with a hard byte cap so a multi-gigabyte file cannot
   exhaust a 4 GB box, and only text/html or text/plain is read at all.

Residual risk, stated honestly: between our DNS check and httpx's own
resolution there is a small window for a DNS-rebinding attack. Closing it
completely means pinning the connection to the validated IP, which breaks TLS
certificate validation for the hostname. The cap on redirects, the byte limit
and the text-only rule keep the blast radius small.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings
from .base import Arg, Tool, ToolResult, register

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_TEXT = 6000
ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")


class BlockedURL(ValueError):
    pass


def _check_ip(raw: str) -> None:
    ip = ipaddress.ip_address(raw)
    # ::ffff:127.0.0.1 must be judged as 127.0.0.1, not as an opaque v6 address.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        raise BlockedURL(f"address {ip} is not a public address")


async def _validate(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"scheme {parts.scheme or '(none)'} is not allowed")
    host = parts.hostname
    if not host:
        raise BlockedURL("no hostname")

    port = parts.port or (443 if parts.scheme == "https" else 80)

    # A literal address skips DNS but still has to pass the range check.
    try:
        ipaddress.ip_address(host)
        _check_ip(host)
        return
    except BlockedURL:
        raise
    except ValueError:
        pass

    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve {host}") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise BlockedURL(f"cannot resolve {host}")
    for addr in addresses:
        _check_ip(addr)


# ---------------------------------------------------------------- extract ---

SKIP_TAGS = {
    "script", "style", "noscript", "svg", "canvas", "nav", "footer", "header",
    "aside", "form", "button", "iframe", "template", "select",
}
BLOCK_TAGS = {
    "p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3",
    "h4", "h5", "h6", "blockquote", "pre",
}


class Extractor(HTMLParser):
    """Just enough HTML understanding to get readable prose out.

    A dependency like readability-lxml would do this better, but it pulls in a
    C extension for one tool on a box where build time is measured in minutes.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._chunks: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
            return
        if self._skip:
            return
        text = data.strip()
        if text:
            self._chunks.append(text + " ")

    def text(self) -> str:
        joined = "".join(self._chunks)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*\n+", "\n\n", joined)
        return joined.strip()


# ------------------------------------------------------------------- tool ---


async def _read_url(url: str) -> ToolResult:
    target = (url or "").strip()
    if not target:
        return ToolResult(False, "No URL given.", error="empty")
    if "://" not in target:
        target = "https://" + target

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.tool_timeout),
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
            },
            follow_redirects=False,  # every hop is validated by hand below
        ) as client:
            body = b""
            content_type = ""
            final = target

            for _ in range(MAX_REDIRECTS + 1):
                await _validate(final)
                async with client.stream("GET", final) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return ToolResult(False, "That link redirected to nowhere.",
                                              error="bad redirect")
                        final = urljoin(final, location)
                        continue

                    response.raise_for_status()
                    content_type = (response.headers.get("content-type") or "").lower()
                    if not any(t in content_type for t in ALLOWED_TYPES):
                        return ToolResult(
                            False,
                            f"That link returns {content_type.split(';')[0] or 'a file'}, "
                            "not a web page I can read.",
                            error="not a page",
                        )
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            break
                    break
            else:
                return ToolResult(
                    False,
                    "That link kept redirecting and never arrived at a page.",
                    error="too many redirects",
                )

    except BlockedURL as exc:
        reason = str(exc)
        if "scheme" in reason:
            message = "That is not a web link. Give an http:// or https:// address."
        elif "resolve" in reason:
            message = ("That address does not resolve. Check the spelling of the "
                       "domain.")
        else:
            message = ("That address is on a private network, so this server will "
                       "not open it. Use a public link.")
        return ToolResult(False, message, error="blocked")
    except httpx.HTTPStatusError as exc:
        return ToolResult(
            False,
            f"That page returned {exc.response.status_code} — the link may be "
            "wrong, or the page may have moved.",
            error=f"http {exc.response.status_code}",
        )
    except httpx.HTTPError as exc:
        return ToolResult(
            False,
            "Could not reach that site. It may be down, or the address may be "
            "mistyped.",
            error="unreachable",
        )

    html = body.decode("utf-8", errors="replace")
    if "text/plain" in content_type:
        title, text = "", html.strip()
    else:
        parser = Extractor()
        try:
            parser.feed(html)
        except Exception:  # noqa: BLE001 — malformed HTML is normal
            pass
        title, text = parser.title, parser.text()

    if not text:
        return ToolResult(
            False,
            "That page had no readable text — it may build its content with "
            "JavaScript, which this reader does not run.",
            error="no text",
        )

    truncated = len(text) > MAX_TEXT
    text = text[:MAX_TEXT]
    words = len(text.split())
    domain = urlparse(final).netloc
    lede = text[:400].rsplit(" ", 1)[0] + ("…" if len(text) > 400 else "")

    return ToolResult(
        True,
        f"Page “{title or domain}” ({domain}): {lede}",
        card="url",
        data={
            "url": final,
            "domain": domain,
            "title": title or domain,
            "text": text,
            "lede": lede,
            "words": words,
            "reading_minutes": round(words / 200, 1),
            "truncated": truncated,
        },
    )


register(Tool(
    name="read_url",
    label="Read page",
    glyph="⧉",
    description="Fetch a web page and extract its readable text so it can be summarised.",
    args=(Arg("url", "string", True, "the link to read"),),
    run=_read_url,
    card="url",
    network=True,
    example="/read https://example.com",
))
