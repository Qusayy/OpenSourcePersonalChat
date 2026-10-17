"""The files in an answer, found as it is written.

An answer that writes a project puts each file in a fenced block with its
path just above it::

    ### app/main.py
    ```python
    print("hello")
    ```

A path in the fence's own line (```python app/main.py, ```python:app/main.py,
```python title="app/main.py") is read as well, and so is a header written as
**app/main.py** or `app/main.py`. A fenced block with no path is an ordinary
code block and is left alone, and so is anything inside it.

The reader takes the answer piece by piece, as tokens arrive, and says what
happens as it happens: a file opens, a line of it arrives, it closes. Only a
whole line is ever read, so a path or a fence split across tokens is never
mistaken for something else. A Markdown file may hold fenced blocks of its
own; inside one of those, its closing fence is not the file's.

The page reads answers with the same rules (web/src/lib/files.ts); the two
are held to one set of cases (web/src/lib/__fixtures__/file-blocks.json).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import paths

_PATH = r"(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+"
# A line that is only a path: under a heading mark, in bold, in backticks, or
# after "File:".
HEADER = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|[-*]\s+)?(?:\*\*|__)?`?(?:file:\s*)?"
    rf"(?P<path>{_PATH})`?(?:\*\*|__)?\s*:?\s*$",
    re.I,
)
# A fence that opens a block: its language, and a path after it, if any.
OPENING = re.compile(
    r"^\s{0,3}(?P<fence>`{3,})(?P<lang>[A-Za-z0-9_+#.-]*)"
    rf"(?:[ \t]*[: \t][ \t]*(?:title=)?[\"']?(?P<path>{_PATH})[\"']?)?[ \t]*$"
)
CLOSING = re.compile(r"^\s{0,3}`{3,}\s*$")


@dataclass
class _Open:
    path: str
    lang: str
    fence: str
    lines: list[str] = field(default_factory=list)
    # How deep inside fenced blocks of its own a Markdown file is.
    depth: int = 0


class FileBlocks:
    """Reads an answer as it arrives. ``feed`` takes the next piece of text
    and returns what happened, as events:

    * ``{"type": "open", "path", "lang"}``
    * ``{"type": "line", "path", "text"}``, one whole line with its newline
    * ``{"type": "close", "path", "lang", "content"}``

    ``finish`` reads a last line with no newline after it. A file still open
    at the end was never finished, and no ``close`` is said of it.
    """

    def __init__(self) -> None:
        self._partial = ""
        self._pending: str | None = None
        self._file: _Open | None = None
        # Inside a fenced block that is not a file, and its fence.
        self._other: str | None = None

    def feed(self, text: str) -> list[dict]:
        self._partial += text
        out: list[dict] = []
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            out.extend(self._line(line))
        return out

    def finish(self) -> list[dict]:
        last, self._partial = self._partial, ""
        out = self._line(last, ending=False) if last else []
        return out

    @property
    def writing(self) -> str | None:
        """The path of the file being written, if one is."""
        return self._file.path if self._file else None

    def _line(self, line: str, *, ending: bool = True) -> list[dict]:
        if self._file is not None:
            return self._inside(line, ending)
        if self._other is not None:
            if CLOSING.match(line) and line.strip().startswith(self._other):
                self._other = None
            return []
        opened = OPENING.match(line)
        if opened:
            path = paths.safe(opened.group("path") or "") or self._pending
            self._pending = None
            if path is None:
                self._other = opened.group("fence")
                return []
            lang = paths.language(path, opened.group("lang") or "")
            self._file = _Open(path, lang, opened.group("fence"))
            return [{"type": "open", "path": path, "lang": lang}]
        header = HEADER.match(line)
        if header:
            self._pending = paths.safe(header.group("path"))
        elif line.strip():
            self._pending = None
        return []

    def _inside(self, line: str, ending: bool) -> list[dict]:
        f = self._file
        bare = CLOSING.match(line) and line.strip().startswith(f.fence)
        if bare and f.depth == 0:
            self._file = None
            return [{"type": "close", "path": f.path, "lang": f.lang, "content": "".join(f.lines)}]
        if f.lang == "markdown":
            if bare:
                f.depth -= 1
            elif OPENING.match(line) and line.strip("` \t"):
                f.depth += 1
        text = line + ("\n" if ending else "")
        f.lines.append(text)
        return [{"type": "line", "path": f.path, "text": text}]


def files_in(answer: str) -> list[dict]:
    """Every file an answer wrote out whole, in order: the ``close`` events."""
    reader = FileBlocks()
    events = reader.feed(answer) + reader.finish()
    return [e for e in events if e["type"] == "close"]
