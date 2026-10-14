"""The part of a long text worth reading, chosen to fit a budget.

A fetched page can run to thousands of words, and the model reads all of what
it is given on every pass after. So a tool's result reaches the prompt as an
excerpt: whole sentences, picked from anywhere in the text, in the order the
text has them, with " … " wherever something was left out.

How a sentence is picked:

* With a question, by the words it shares with the question, each weighted by
  how rare it is in this text. A word on every line of the page says little
  about which line holds the answer; a name that appears twice says a lot. A
  sentence near the top gets a little more, since a page tends to say what it
  is about first.
* With no question, or none of its words in the text, by how much a sentence
  says for its length, the three thirds of the text taking turns, so the
  excerpt spans the page instead of reading its opening paragraph.

Everything here is a pure function of its arguments: the same text, budget and
question always give the same excerpt. The budget is in tokens, counted as a
byte in three, which is a little more than a model's tokenizer makes of most
text and costs nothing to work out. The excerpt never counts more than that.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

GAP = " … "

# Words that say nothing about which sentence holds an answer.
STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers herself him himself his how i if in into is it
    its itself just me more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours yourself yourselves
    tell say said says please give find show know get one two many much may might must
    """.split()
)

# A sentence ends at ., ! or ? followed by space and something that can begin
# one, and every line is a sentence of its own: a page's text keeps headings
# and list items on lines of their own.
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9À-ÖØ-Þ])")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")


def estimate(text: str) -> int:
    """Tokens in ``text``, counted as a byte in three, rounded up."""
    return -(-len(text.encode("utf-8")) // 3)


def sentences(text: str) -> list[str]:
    """The text as a list of sentences, each with its spacing made single."""
    out: list[str] = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        out.extend(part.strip() for part in _SENTENCE_END.split(line) if part.strip())
    return out


def terms(text: str) -> list[str]:
    """The words of ``text`` that can tell sentences apart, lower-cased and
    with a plural's s taken off."""
    out = []
    for word in _WORD.findall(text.lower()):
        word = word.replace("’", "'")
        if word.endswith("'s"):
            word = word[:-2]
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if len(word) > 1 and word not in STOPWORDS:
            out.append(word)
    return out


@dataclass(frozen=True)
class _Sentence:
    index: int
    text: str
    terms: frozenset[str]
    words: int


def excerpt(text: str, budget: int, focus: str = "") -> str:
    """The sentences of ``text`` that best fit ``budget`` tokens, chosen for
    ``focus`` when it is given.

    A text that fits is returned whole. Otherwise what is returned is whole
    sentences in the text's order, joined by " … " where any were left out,
    and never more than ``budget`` tokens. When not even one sentence fits,
    the best one is cut at a word and ends in "…". With a budget too small
    for anything, it is the empty string.
    """
    text = text.strip()
    if budget <= 0 or not text:
        return ""
    if estimate(text) <= budget:
        return text

    found = [
        _Sentence(i, s, frozenset(terms(s)), len(s.split()))
        for i, s in enumerate(sentences(text))
    ]
    if not found:
        return ""
    weight = _rarity(found)
    asked = set(terms(focus)) & set(weight)
    if asked:
        queues = [sorted(found, key=lambda s: (-_relevance(s, asked, weight), s.index))]
    else:
        queues = _thirds(found, weight)

    # The queues take turns. On its turn a queue offers its sentences in
    # order until one fits; one that does not fit now never will, since what
    # is left only shrinks, and is let go.
    chosen: list[_Sentence] = []
    room = budget * 3  # in bytes
    used = 0
    queues = [list(q) for q in queues]
    first = queues[0][0] if queues[0] else found[0]
    while any(queues):
        for queue in queues:
            while queue:
                candidate = queue.pop(0)
                size = len(candidate.text.encode("utf-8"))
                if used + size > room:
                    continue  # cannot fit, whatever joins it to the rest
                if any(s.text == candidate.text for s in chosen):
                    continue  # a line the page repeats is read once
                trial = sorted([*chosen, candidate], key=lambda s: s.index)
                if estimate(_render(trial, len(found))) <= budget:
                    chosen, used = trial, used + size
                    break
    if chosen:
        return _render(chosen, len(found))
    return _cut(first, budget)


def _rarity(found: list[_Sentence]) -> dict[str, float]:
    """How much each word tells: more, the fewer sentences it is in."""
    seen: dict[str, int] = {}
    for s in found:
        for term in s.terms:
            seen[term] = seen.get(term, 0) + 1
    n = len(found)
    return {term: math.log(1 + n / count) for term, count in seen.items()}


def _lead(s: _Sentence) -> float:
    """A little for being near the top of the text."""
    return 1.0 / (2 + s.index)


def _relevance(s: _Sentence, asked: set[str], weight: dict[str, float]) -> float:
    return sum(weight[term] for term in s.terms & asked) + _lead(s)


def _substance(s: _Sentence, weight: dict[str, float]) -> float:
    """How much a sentence says for its length."""
    return sum(weight[term] for term in s.terms) / math.sqrt(1 + s.words) + _lead(s)


def _thirds(found: list[_Sentence], weight: dict[str, float]) -> list[list[_Sentence]]:
    """The sentences of each third of the text, from the one that says the
    most to the one that says the least."""
    by_worth = sorted(found, key=lambda s: (-_substance(s, weight), s.index))
    n = len(found)
    thirds: list[list[_Sentence]] = [[], [], []]
    for s in by_worth:
        thirds[min(2, s.index * 3 // n)].append(s)
    return thirds


def _render(chosen: list[_Sentence], total: int) -> str:
    """The chosen sentences in the text's order, a gap marked wherever any
    were left out, before the first and after the last included."""
    parts = ["… "] if chosen[0].index > 0 else []
    for i, s in enumerate(chosen):
        if i:
            parts.append(" " if s.index == chosen[i - 1].index + 1 else GAP)
        parts.append(s.text)
    if chosen[-1].index < total - 1:
        parts.append(" …")
    return "".join(parts)


def _cut(s: _Sentence, budget: int) -> str:
    """One sentence too long for the budget, cut to fit it: at a word where
    there is one to cut at, and marked with "…" where it was cut."""
    before = "… " if s.index > 0 else ""
    room = budget * 3 - len(before.encode("utf-8")) - len(" …".encode("utf-8"))
    if room <= 0:
        return ""
    piece = s.text.encode("utf-8")[:room].decode("utf-8", "ignore")
    if " " in piece:
        piece = piece[: piece.rindex(" ")]
    piece = piece.rstrip()
    return f"{before}{piece} …" if piece else ""
