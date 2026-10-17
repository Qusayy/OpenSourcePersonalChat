"""The built front-end: where it is, how it is served, and what a page is told at load.

The interface is a React app under `web/`, built by Vite into `web/dist`. This
module never runs Node. It serves the files that build produced:

  /assets/<name>   content-hashed files, cacheable for good
  index.html       the one page, for every route, never cached

and writes the first values the page shows into that HTML, so they are on screen
without a request. When `web/dist` is not there, it says so in words instead of
failing.
"""

from __future__ import annotations

import html
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from ..config import BASE_DIR

log = logging.getLogger("aurora.web")

DIST = BASE_DIR / "web" / "dist"

# Python's table has no entry for woff2 before 3.13 (and none at all on some
# Windows installs), so the typeface would be sent as octet-stream.
mimetypes.add_type("font/woff2", ".woff2")

# The strings in web/index.html that are rewritten for each request. They are
# plain text there, so the page still works if one is ever missed: it asks
# /api/bootstrap instead and sets its own title. tests/test_web.py holds
# web/index.html to these.
MARK_BOOTSTRAP = '<script type="application/json" id="bootstrap">null</script>'
MARK_TITLE = "<title>Aurora</title>"
MARK_DESCRIPTION = (
    '<meta name="description" content="A small language model running locally, '
    'with the numbers on screen.">'
)
MARK_PAGE = '<html lang="en" data-page="chat">'

# A year, and never revalidated: a file under /assets/ is named for its
# contents, so a new build is a new name.
CACHE_FOREVER = "public, max-age=31536000, immutable"
# The page names those files, so it must be asked for every time.
CACHE_PAGE = "no-cache"

NOT_BUILT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aurora — not built</title>
<style>
  body { margin: 48px 24px; font: 17px/1.55 system-ui, sans-serif; color: #1c231d; background: #f8fbf9; }
  main { max-width: 42rem; margin: 0 auto; }
  code, pre { font: 15px/1.5 ui-monospace, Consolas, monospace; }
  pre { padding: 12px 16px; border: 1px solid #d5dbd6; border-radius: 4px; overflow-x: auto; }
</style>
</head>
<body>
<main>
<h1>The interface has not been built.</h1>
<p>The server is running and the API answers, but <code>web/dist</code> is missing,
so there is no page to send. Either:</p>
<p>Build it here (needs Node):</p>
<pre>cd web
npm ci
npm run build</pre>
<p>or run a copy that already has it, which is what a server should do:</p>
<pre>git fetch origin
git checkout release</pre>
<p>Then reload. No restart is needed.</p>
</main>
</body>
</html>
"""


def script_json(data: Any) -> str:
    """JSON that is safe inside a <script> element.

    `json.dumps` leaves `</script>` and `<!--` as they are, and either would end
    or derail the element. Escaped as \\uXXXX they are still the same JSON.
    U+2028 and U+2029 are legal in JSON but were line terminators in JavaScript.
    """
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


class Frontend:
    """The files of one build. The directory is a parameter so tests can aim it."""

    def __init__(self, dist: Path = DIST):
        self.dist = Path(dist)
        self._index: tuple[float, str] | None = None

    # -- files ----------------------------------------------------------------

    def built(self) -> bool:
        return (self.dist / "index.html").is_file()

    def _template(self) -> str | None:
        """index.html, re-read when the file changes (a pull replaces it)."""
        path = self.dist / "index.html"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._index = None
            return None
        if self._index is None or self._index[0] != mtime:
            text = path.read_text(encoding="utf-8")
            for name, mark in (
                ("bootstrap", MARK_BOOTSTRAP),
                ("title", MARK_TITLE),
                ("description", MARK_DESCRIPTION),
                ("page", MARK_PAGE),
            ):
                if mark not in text:
                    log.warning("web/dist/index.html has no %s marker; it will not be filled in", name)
            self._index = (mtime, text)
        return self._index[1]

    def asset(self, name: str) -> Response:
        """One file from dist/assets, or a plain 404. Never the page.

        A browser holding last week's page asks for last week's file names. If
        the answer to a missing one were index.html, it would be parsed as
        JavaScript and fail somewhere confusing.
        """
        root = (self.dist / "assets").resolve()
        try:
            path = (root / name).resolve()
        except (OSError, ValueError):
            return not_found()
        if root not in path.parents or not path.is_file():
            return not_found()
        return FileResponse(path, headers={"Cache-Control": CACHE_FOREVER})

    # -- the page -------------------------------------------------------------

    def page(
        self,
        *,
        page: str,
        title: str,
        description: str,
        bootstrap: dict,
        status_code: int = 200,
    ) -> Response:
        template = self._template()
        if template is None:
            return HTMLResponse(NOT_BUILT, status_code=503, headers={"Cache-Control": "no-store"})
        body = (
            template.replace(MARK_PAGE, f'<html lang="en" data-page="{html.escape(page, quote=True)}">', 1)
            .replace(MARK_TITLE, f"<title>{html.escape(title, quote=False)}</title>", 1)
            .replace(
                MARK_DESCRIPTION,
                f'<meta name="description" content="{html.escape(description, quote=True)}">',
                1,
            )
            .replace(
                MARK_BOOTSTRAP,
                f'<script type="application/json" id="bootstrap">{script_json(bootstrap)}</script>',
                1,
            )
        )
        return HTMLResponse(body, status_code=status_code, headers={"Cache-Control": CACHE_PAGE})


def not_found() -> Response:
    return PlainTextResponse("Not found", status_code=404, headers={"Cache-Control": "no-store"})
