"""look_back: what was said earlier in this conversation, found again.

When a conversation outgrows the window, its oldest turns are given to the
model only as a brief. The turns themselves are kept, and this is how the
model reads what the brief left out: the words of an old message, what a tool
returned then, or what was answered. It finds the parts of earlier turns that
share the most words with what it is asked for, or reads one turn whole.

What it searches is what the run was given of the conversation
(``CALL.earlier``), never an argument the model can give, so it reads this
conversation and no other. It returns nothing that was not read in an earlier
turn already, so it adds nothing a run is not already counted as having read.
"""

from __future__ import annotations

import math

from ..text import estimate, excerpt, terms
from .base import CALL, Arg, Tool, ToolResult, register

# The most pieces of earlier turns one search gives back.
HITS = 6


def _pieces(earlier: tuple[dict, ...]) -> list[tuple[int, str, str]]:
    """Every piece of every earlier turn, as (turn number, whose, text)."""
    out: list[tuple[int, str, str]] = []
    for number, turn in enumerate(earlier, start=1):
        if turn.get("question"):
            out.append((number, "you asked", turn["question"]))
        for tool, text in turn.get("found") or ():
            if text:
                out.append((number, f"{tool} returned", text))
        if turn.get("answer"):
            out.append((number, "I answered", turn["answer"]))
    return out


def _ranked(pieces: list[tuple[int, str, str]], query: str) -> list[tuple[int, str, str]]:
    """The pieces that share words with ``query``, the best first: each word
    weighted by how few pieces have it, and a later turn ahead of an earlier
    one where they tie."""
    asked = set(terms(query))
    if not asked:
        return []
    have = [set(terms(text)) for _, _, text in pieces]
    seen: dict[str, int] = {}
    for words in have:
        for word in words & asked:
            seen[word] = seen.get(word, 0) + 1
    weight = {word: math.log(1 + len(pieces) / count) for word, count in seen.items()}
    scored = [
        (sum(weight[w] for w in words & asked), piece)
        for words, piece in zip(have, pieces)
    ]
    scored = [(score, piece) for score, piece in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], -item[1][0]))
    return [piece for _, piece in scored]


def _lines(found: list[tuple[int, str, str]], query: str, room: int) -> list[str]:
    """The pieces as lines, in the conversation's order, each cut to its
    share of ``room`` around what was asked for."""
    if not found:
        return []
    share = max(30, room // len(found))
    found = sorted(found, key=lambda piece: piece[0])
    return [f"Turn {number}, {whose}: {excerpt(' '.join(text.split()), share, query)}"
            for number, whose, text in found]


async def _look_back(query: str = "", turn: float | None = None) -> ToolResult:
    earlier = CALL.get().earlier
    query = " ".join((query or "").split())
    if not earlier:
        return ToolResult(True, "There is nothing earlier in this conversation.", card="note",
                          data={"action": "look_back", "query": query, "notes": []})
    pieces = _pieces(earlier)
    if turn is not None:
        number = int(turn)
        if not 1 <= number <= len(earlier):
            return ToolResult(False, f"There is no turn {number}; this conversation has "
                                     f"{len(earlier)} earlier turns.", error="no such turn")
        found = [p for p in pieces if p[0] == number]
        if query:
            found = _ranked(found, query) or found
    else:
        if not terms(query):
            return ToolResult(False, "Say what to look for: a few words from what was said.",
                              error="empty")
        found = _ranked(pieces, query)[:HITS]
    if not found:
        said = f"Nothing earlier in this conversation matches {query!r}."
        return ToolResult(True, said, card="note",
                          data={"action": "look_back", "query": query, "notes": []})
    lines = _lines(found, query, LOOK_BACK.budget)
    return ToolResult(
        True, "\n".join(lines), card="note",
        data={"action": "look_back", "query": query, "notes": lines,
              "turns": len(earlier)},
    )


def _observe(result: ToolResult, purpose: str, budget: int) -> str:
    """The pieces found, as many whole lines as the budget has room for."""
    lines = (result.data or {}).get("notes") or []
    if not lines:
        return result.summary
    kept: list[str] = []
    for line in lines:
        if kept and estimate("\n".join([*kept, line])) > budget:
            break
        kept.append(line)
    return "\n".join(kept)


LOOK_BACK = register(Tool(
    name="look_back",
    label="Look back",
    glyph="↶",
    description=(
        "Find what was said earlier in this conversation that is no longer shown in full: "
        "an old message, what a tool returned then, or an answer. Give a few words to look "
        "for, or a turn's number to read it whole."
    ),
    args=(
        Arg("query", "string", False, "words from what was said, to look for", max_len=200),
        Arg("turn", "number", False, "the number of an earlier turn, to read it whole"),
    ),
    run=_look_back,
    card="note",
    budget=800,
    observe=_observe,
))
