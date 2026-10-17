"""text_stats: words, sentences and reading time. Needs no network."""

from __future__ import annotations

import re

from .base import Arg, Tool, ToolResult, register


STOPWORDS = set(
    "the a an and or but if then than that this these those of to in for on at by "
    "with from as is are was were be been being it its it's i you he she they we "
    "not no so such can will just do does did have has had about into over after "
    "your our their his her my me him them us".split()
)


async def _text_stats(text: str) -> ToolResult:
    body = (text or "").strip()
    if not body:
        return ToolResult(False, "There is no text to measure.", error="empty")

    words = re.findall(r"[A-Za-z0-9']+", body)
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", body) if s.strip()]
    counts: dict[str, int] = {}
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or len(lw) < 3:
            continue
        counts[lw] = counts.get(lw, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    minutes = len(words) / 200 if words else 0

    return ToolResult(
        True,
        f"{len(words):,} words, {len(sentences):,} sentences, "
        f"about {max(1, round(minutes))} minute(s) to read.",
        card="textstats",
        data={
            "words": len(words),
            "characters": len(body),
            "sentences": len(sentences),
            "paragraphs": len([p for p in body.split("\n\n") if p.strip()]),
            "reading_minutes": round(minutes, 1),
            "avg_sentence_words": round(len(words) / max(1, len(sentences)), 1),
            "top_terms": [{"term": t, "count": c} for t, c in top],
        },
    )


register(Tool(
    name="text_stats",
    label="Text stats",
    glyph="▤",
    description="Word, sentence and reading-time statistics for a block of text.",
    args=(Arg("text", "string", True, "the text to measure", max_len=200),),
    run=_text_stats,
    card="textstats",
    example="/stats <paste some text>",
))
