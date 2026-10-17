"""Fill a database with the conversations the browser tests open.

    AURORA_DB_PATH=/path/to/test.db python web/e2e/seed.py

Run from the repository root. Whatever the database held is removed first, so
every run starts from the same three conversations:

  1. "Every figure a tool can draw": one answer carrying all nine cards, the
     four offline ones computed by the real tools and the four network ones
     from fixed payloads (the tests must not depend on the internet), plus a
     failed call.
  2. A long pasted question and an answer that uses every kind of markdown the
     renderer supports.
  3. A short exchange in a right-to-left script.

The server can be running: SQLite's write-ahead log lets a second process
write, and the pages read conversations per request.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if not os.environ.get("AURORA_DB_PATH"):
    sys.exit("AURORA_DB_PATH is not set; refusing to touch the default database")

from app.store import conversations, db, runs  # noqa: E402
from app.tools import call_tool  # noqa: E402

WEATHER = {
    "place": "Reykjavík",
    "country": "Iceland",
    "condition": "Partly cloudy",
    "icon": "partly",
    "temperature": 7.4,
    "feels_like": 3.9,
    "humidity": 71,
    "precipitation": 0.0,
    "wind": 21.6,
    "days": [
        {"date": "2026-10-13", "max": 8.1, "min": 3.2, "condition": "Partly cloudy", "icon": "partly"},
        {"date": "2026-10-14", "max": 7.0, "min": 2.5, "condition": "Rain", "icon": "rain"},
        {"date": "2026-10-15", "max": 5.6, "min": 0.9, "condition": "Snow", "icon": "snow"},
        {"date": "2026-10-16", "max": 6.3, "min": 1.4, "condition": "Overcast", "icon": "cloud"},
        {"date": "2026-10-17", "max": 9.2, "min": 4.0, "condition": "Clear sky", "icon": "sun"},
    ],
    "hourly": [3.2, 3.0, 2.8, 2.7, 2.9, 3.4, 4.1, 5.0, 5.9, 6.6, 7.1, 7.4,
               7.6, 7.5, 7.2, 6.8, 6.1, 5.5, 5.0, 4.6, 4.3, 4.0, 3.8, 3.6],
    "units": "°C",
}

WIKIPEDIA = {
    "title": "Ada Lovelace",
    "description": "English mathematician and writer (1815–1852)",
    "extract": (
        "Augusta Ada King, Countess of Lovelace was an English mathematician and writer, "
        "chiefly known for her work on Charles Babbage's proposed mechanical general-purpose "
        "computer, the Analytical Engine. She was the first to recognise that the machine had "
        "applications beyond pure calculation."
    ),
    "url": "https://en.wikipedia.org/wiki/Ada_Lovelace",
    "lang": "en",
}

PAGE_TEXT = (
    "llama.cpp runs large language models in plain C and C++ with no dependencies. "
    "It supports 4-bit quantisation, memory-mapped weights and a wide range of CPUs. "
) * 6
PAGE = {
    "url": "https://example.org/notes/llama-cpp",
    "domain": "example.org",
    "title": "Notes on running llama.cpp on a small server",
    "text": PAGE_TEXT,
    "lede": PAGE_TEXT[:400].rsplit(" ", 1)[0] + "…",
    "words": len(PAGE_TEXT.split()),
    "reading_minutes": round(len(PAGE_TEXT.split()) / 200, 1),
    "truncated": False,
}

CURRENCY = {
    "amount": 100.0,
    "from": "EUR",
    "to": "USD",
    "result": 108.42,
    "rate": 1.0842,
    "date": "2026-10-12",
    "source": "European Central Bank via Frankfurter",
}

LONG_QUESTION = "\n".join(
    [
        "I pasted the notes from yesterday's meeting below. Can you pull out the decisions?",
        "",
        "1. The benchmark page stays on the public site; nobody objected.",
        "2. Context is capped at 4096 tokens until memory use has been measured for a week.",
        "3. The weather tool keeps its five-day forecast but drops the hourly table.",
        "4. Anything that needs the network must say so before it runs.",
        "5. We look again at a larger model once the server has more memory.",
        "",
        "There was also a long discussion about naming that did not end in a decision.",
    ]
)

MARKDOWN_ANSWER = """# Decisions

Four things were **decided**, and one was *deferred*.

## What was agreed

1. The benchmark page stays public.
2. Context is capped at `4096` tokens for now.
3. The weather tool keeps its forecast.

- Network tools announce themselves.
- Naming was left open.

> A larger model waits for more memory.

### The cap, as configuration

```python
AURORA_N_CTX = 4096  # tokens
def fits(prompt: int, answer: int) -> bool:
    return prompt + answer <= AURORA_N_CTX
```

| Item | Outcome |
| --- | --- |
| Benchmark page | stays |
| Context | 4096 |

See the [project page](https://example.org/aurora) for the rest.

---

#### Still open

Naming."""


async def offline(name: str, args: dict):
    result = await call_tool(name, args)
    if not result.ok:
        raise SystemExit(f"{name} failed while seeding: {result.error}")
    return result


async def main() -> None:
    db.init()
    for table in ("steps", "tool_calls", "context_turns", "messages", "conversations", "runs"):
        db.write(f"DELETE FROM {table}")  # noqa: SLF001 - a fixture owns the whole file

    # 3. Right-to-left, oldest so it sorts last.
    cid = conversations.create("ما هو النموذج اللغوي؟")
    conversations.add_message(cid, "user", "ما هو النموذج اللغوي؟")
    conversations.add_message(
        cid, "assistant",
        "النموذج اللغوي برنامج يتوقع الكلمة التالية في النص. مثال: `print(1 + 1)`.",
        ttft_ms=412.0, gen_tps=9.8, prompt_tokens=31, completion_tokens=24,
    )

    # 2. A long question, and every kind of markdown.
    cid = conversations.create(LONG_QUESTION)
    conversations.add_message(cid, "user", LONG_QUESTION)
    conversations.add_message(
        cid, "assistant", MARKDOWN_ANSWER,
        ttft_ms=1830.5, gen_tps=8.42, prompt_tokens=212, completion_tokens=148,
    )

    # 1. Every card.
    question = "Show me every figure a tool can draw."
    cid = conversations.create(question)
    conversations.add_message(cid, "user", question)
    calls: list[str] = []

    def keep(tool: str, card: str, data: dict, summary: str, ms: float, args: dict) -> None:
        calls.append(conversations.add_tool_call(
            cid, tool, via="skill", args=args, card=card, data=data, summary=summary, ms=ms,
        ))

    for name, args in (
        ("calculator", {"expression": "(1024 * 3.5) / 7 + 2**10"}),
        ("convert_units", {"value": 26.2, "from_unit": "mi", "to_unit": "km"}),
        ("clock", {"timezone_name": "Asia/Tokyo"}),
        ("text_stats", {"text": "The quick brown fox jumps over the lazy dog. " * 12}),
    ):
        result = await offline(name, args)
        keep(name, result.card, result.data, result.summary, 0.4, args)

    keep("weather", "weather", WEATHER, "Reykjavík, Iceland: Partly cloudy, 7.4°C.", 412.3,
         {"location": "Reykjavik"})
    keep("wikipedia", "wikipedia", WIKIPEDIA, "Ada Lovelace: English mathematician.", 268.9,
         {"query": "Ada Lovelace"})
    keep("read_url", "url", PAGE, "Page “Notes on running llama.cpp”.", 731.0,
         {"url": PAGE["url"]})
    keep("currency", "currency", CURRENCY, "100 EUR = 108.42 USD.", 198.4,
         {"amount": 100, "from_currency": "EUR", "to_currency": "USD"})
    calls.append(conversations.add_tool_call(
        cid, "weather", via="skill", args={"location": "Atlantis"}, card=None, data={},
        summary="No place called “Atlantis” was found.", ms=301.2, ok=False,
    ))

    # How that answer ran, as its steps were kept.
    run_id = "seeded-run"
    for idx, (kind, name, via, ms, tokens) in enumerate((
        ("route", "weather", "model", 310.0, (667, 18, 12)),
        ("tool", "weather", "model", 412.3, (None, None, None)),
        ("decide", None, "model", 400.0, (702, 36, 5)),
        ("answer", None, None, 2900.0, (740, 61, 19)),
    )):
        runs.add_step(
            run_id, cid, idx, kind, name=name, via=via, total_ms=ms,
            prompt_tokens=tokens[0], new_tokens=tokens[1], output_tokens=tokens[2],
            first_token_ms=ms / 4 if tokens[0] else None, queue_ms=0.0 if tokens[0] else None,
        )
    mid = conversations.add_message(
        cid, "assistant",
        "Those are the nine figures: four computed here, four fetched, and one that failed.",
        ttft_ms=96.0, gen_tps=11.7, prompt_tokens=388, completion_tokens=19,
        correction="Not in what was looked up: nine. Weather returned: Reykjavík, Iceland: "
                   "Partly cloudy, 7.4°C.",
    )
    conversations.attach_tool_calls(mid, calls)
    runs.attach_steps(mid, run_id)
    conversations.touch(cid)

    # The list is ordered by last use. Three rows written in the same instant
    # would come back in no particular order, so they are spaced out: the
    # cards a moment ago, the long question an hour ago, the third yesterday.
    now = time.time()
    for title_start, age in (("Show me every", 60), ("I pasted the notes", 3600), ("ما هو", 86400)):
        db.write(  # noqa: SLF001
            "UPDATE conversations SET updated_at = ?, created_at = ? WHERE title LIKE ?",
            (now - age, now - age, title_start + "%"),
        )
    db.close()
    print("seeded", os.environ["AURORA_DB_PATH"])


if __name__ == "__main__":
    asyncio.run(main())
