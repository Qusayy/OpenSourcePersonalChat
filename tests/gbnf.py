"""A reader for the part of GBNF the tool grammar is written in.

llama.cpp is not installed where these tests run, so nothing here can compile
the grammar for real. What can be checked is what the grammar *says*: that a
reply it is meant to allow is allowed, and that one it is meant to rule out is
ruled out. This reads the grammar's text and answers that.

It is not llama.cpp's parser and does not try to be. It understands what the
generator emits and nothing else: one rule to a line, literals, character
classes, references to other rules, groups, alternation, and the repetitions
``?``, ``*``, ``+`` and ``{m,n}``. Anything outside that raises, so a generator
that starts using more of GBNF is noticed here rather than silently misread.
That the real grammar compiles is still something only the server can show.
"""

from __future__ import annotations

from functools import lru_cache

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "]": "]", "[": "["}


class Grammar:
    def __init__(self, text: str) -> None:
        self.rules: dict[str, tuple] = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            name, sep, body = line.partition(" ::= ")
            if not sep:
                raise ValueError(f"not a rule: {line!r}")
            self.rules[name.strip()] = _Parser(body).parse()
        self._ends = lru_cache(maxsize=None)(self._ends_of_rule)

    def accepts(self, text: str, rule: str = "root") -> bool:
        """Whether the whole of ``text`` is something ``rule`` can produce."""
        return len(text) in self._ends(rule, text, 0)

    # -- matching: every place a node can end, having started at `at` ------

    def _ends_of_rule(self, name: str, text: str, at: int) -> frozenset[int]:
        return self._match(self.rules[name], text, at)

    def _match(self, node: tuple, text: str, at: int) -> frozenset[int]:
        kind = node[0]
        if kind == "lit":
            return frozenset({at + len(node[1])}) if text.startswith(node[1], at) else frozenset()
        if kind == "cls":
            if at >= len(text):
                return frozenset()
            _, negated, singles, ranges = node
            inside = text[at] in singles or any(lo <= text[at] <= hi for lo, hi in ranges)
            return frozenset({at + 1}) if inside != negated else frozenset()
        if kind == "ref":
            return self._ends(node[1], text, at)
        if kind == "seq":
            places = frozenset({at})
            for part in node[1]:
                places = frozenset(e for p in places for e in self._match(part, text, p))
                if not places:
                    break
            return places
        if kind == "alt":
            return frozenset(e for part in node[1] for e in self._match(part, text, at))
        if kind == "rep":
            _, inner, least, most = node
            found = {at} if least == 0 else set()
            places, count = frozenset({at}), 0
            while places and (most is None or count < most):
                # A step that consumes nothing would go round for ever.
                places = frozenset(e for p in places for e in self._match(inner, text, p) if e > p)
                count += 1
                if count >= least:
                    found |= places
            return frozenset(found)
        raise ValueError(f"unknown node {kind!r}")


class _Parser:
    def __init__(self, body: str) -> None:
        self.s = body
        self.i = 0

    def parse(self) -> tuple:
        node = self._alt()
        self._space()
        if self.i != len(self.s):
            raise ValueError(f"could not read {self.s[self.i:]!r}")
        return node

    def _space(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def _alt(self) -> tuple:
        parts = [self._seq()]
        while self._peek() == "|":
            self.i += 1
            parts.append(self._seq())
        return parts[0] if len(parts) == 1 else ("alt", parts)

    def _seq(self) -> tuple:
        parts = []
        while self._peek() not in ("", "|", ")"):
            parts.append(self._repeated(self._atom()))
        return parts[0] if len(parts) == 1 else ("seq", parts)

    def _peek(self) -> str:
        self._space()
        return self.s[self.i] if self.i < len(self.s) else ""

    def _repeated(self, node: tuple) -> tuple:
        while self.i < len(self.s) and self.s[self.i] in "?*+{":
            mark = self.s[self.i]
            self.i += 1
            if mark == "?":
                node = ("rep", node, 0, 1)
            elif mark == "*":
                node = ("rep", node, 0, None)
            elif mark == "+":
                node = ("rep", node, 1, None)
            else:
                close = self.s.index("}", self.i)
                least, comma, most = self.s[self.i:close].partition(",")
                self.i = close + 1
                node = ("rep", node, int(least), int(most) if most else (None if comma else int(least)))
        return node

    def _atom(self) -> tuple:
        ch = self._peek()
        if ch == '"':
            return ("lit", self._quoted())
        if ch == "[":
            return self._class()
        if ch == "(":
            self.i += 1
            node = self._alt()
            if self._peek() != ")":
                raise ValueError("a group was never closed")
            self.i += 1
            return node
        start = self.i
        while self.i < len(self.s) and (self.s[self.i].isalnum() or self.s[self.i] == "-"):
            self.i += 1
        if start == self.i:
            raise ValueError(f"could not read {self.s[start:]!r}")
        return ("ref", self.s[start:self.i])

    def _char(self) -> str:
        """One character of a literal or class, escapes read."""
        ch = self.s[self.i]
        self.i += 1
        if ch != "\\":
            return ch
        esc = self.s[self.i]
        self.i += 1
        if esc == "x":
            self.i += 2
            return chr(int(self.s[self.i - 2:self.i], 16))
        if esc not in _ESCAPES:
            raise ValueError(f"unknown escape \\{esc}")
        return _ESCAPES[esc]

    def _quoted(self) -> str:
        self.i += 1
        out = []
        while self.s[self.i] != '"':
            out.append(self._char())
        self.i += 1
        return "".join(out)

    def _class(self) -> tuple:
        self.i += 1
        negated = self.s[self.i] == "^"
        if negated:
            self.i += 1
        singles, ranges = set(), []
        while self.s[self.i] != "]":
            lo = self._char()
            if self.s[self.i] == "-" and self.s[self.i + 1] != "]":
                self.i += 1
                ranges.append((lo, self._char()))
            else:
                singles.add(lo)
        self.i += 1
        return ("cls", negated, frozenset(singles), tuple(ranges))
