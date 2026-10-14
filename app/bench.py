"""The benchmark suite behind /bench.

It measures the machine the site is actually running on, through the same code
path chat uses — same lock, same sampling, same context. A synthetic harness
that bypassed the queue would report numbers no visitor would ever see.

Every run reads its prompt cold. The model keeps the last prompt it read and
reads only what differs, so a repeat of the same prompt costs almost nothing:
when the repeats were identical, the suite reported 500–9,000 tokens a second
of reading on a server that reads 60–140. Each run's prompt now begins with a
word no other run's begins with.
"""

from __future__ import annotations

import statistics
import time
from typing import AsyncIterator

from . import db
from .llm import engine

# Filler for the benchmark prompts. It describes no particular machine: the
# suite runs wherever the site does.
FILLER = (
    "The model runs on a small server with no graphics card, on a few shared "
    "cores and a few gigabytes of memory. Weights are stored in four bit "
    "blocks, which keeps the file near one gigabyte and keeps the working set "
    "inside the page cache. Every generated token requires the whole set of "
    "weights to be read once, so throughput follows memory bandwidth much more "
    "closely than it follows clock speed. "
)

TASK = (
    "Using the notes above, write a clear paragraph explaining why a four bit "
    "quantized model runs acceptably on a small CPU server."
)

CASES = [
    {"id": "short", "label": "Short prompt", "target": 32, "gen": 128, "reps": 3},
    {"id": "medium", "label": "Medium prompt", "target": 256, "gen": 128, "reps": 3},
    {"id": "long", "label": "Long prompt", "target": 1024, "gen": 128, "reps": 2},
]

SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "repeat_penalty": 1.05,
    "max_tokens": 128,
}

# The first word of each run's prompt, one per run, so that no run starts the
# way an earlier one did and the model has nothing it can reuse.
MARKS = (
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
    "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
)


def build_prompt(target_tokens: int, mark: str = "") -> str:
    """Grow filler until the prompt reaches the target size for this tokenizer.

    Sentence-at-a-time rather than paragraph-at-a-time: a whole paragraph of
    granularity overshoots the 32-token case by 4x, which would make the short
    row of the chart a lie. ``mark`` opens the prompt and counts towards it.
    """
    sentences = [s.strip() + " " for s in FILLER.split(". ") if s.strip()]
    body = f"{mark}: " if mark else ""
    i = 0
    while engine.count_tokens(body + TASK) < target_tokens:
        body += sentences[i % len(sentences)]
        i += 1
        if len(body) > 60_000:  # safety valve, never reached at these targets
            break
    return (body + TASK).strip()


async def _one_run(prompt: str, gen_tokens: int) -> dict | None:
    messages = [{"role": "user", "content": prompt}]
    sampling = dict(SAMPLING, max_tokens=gen_tokens)
    async for event, payload in engine.stream_chat(messages, sampling):
        if event == "error":
            raise RuntimeError(payload.get("message", "generation failed"))
        if event == "done":
            return payload
    return None


def _summarize(case: dict, results: list[dict]) -> dict:
    ttfts = [r["ttft_ms"] for r in results]
    gen = [r["gen_tps"] for r in results]
    prefill = [r["prefill_tps"] for r in results]
    ttfts_sorted = sorted(ttfts)
    p95 = ttfts_sorted[min(len(ttfts_sorted) - 1, int(round(0.95 * (len(ttfts_sorted) - 1))))]
    return {
        "id": case["id"],
        "label": case["label"],
        "prompt_tokens": results[0]["prompt_tokens"],
        "gen_tokens": round(statistics.mean(r["completion_tokens"] for r in results)),
        "reps": len(results),
        "ttft_p50": round(statistics.median(ttfts), 1),
        "ttft_p95": round(p95, 1),
        "prefill_tps": round(statistics.median(prefill), 1),
        "gen_tps": round(statistics.median(gen), 2),
        "gen_tps_min": round(min(gen), 2),
        "gen_tps_max": round(max(gen), 2),
        "rss_mb": max((r.get("rss_mb") or 0) for r in results) or None,
    }


async def run_suite() -> AsyncIterator[tuple[str, dict]]:
    """Yield (event, payload): stage | rep | case | summary | error."""
    started = time.time()

    marks = iter(MARKS)

    yield "stage", {"text": "Warming up (this run is discarded)", "pct": 0}
    try:
        await _one_run(build_prompt(24, next(marks)), 24)
    except Exception as exc:  # noqa: BLE001
        yield "error", {"message": str(exc)}
        return

    total_reps = sum(c["reps"] for c in CASES)
    done_reps = 0
    summaries: list[dict] = []

    for case in CASES:
        results: list[dict] = []
        for rep in range(case["reps"]):
            yield "stage", {
                "text": f"{case['label']} — run {rep + 1} of {case['reps']}",
                "pct": round(100 * done_reps / total_reps),
            }
            try:
                res = await _one_run(build_prompt(case["target"], next(marks)), case["gen"])
            except Exception as exc:  # noqa: BLE001
                yield "error", {"message": str(exc)}
                return
            if res is None:
                continue
            results.append(res)
            done_reps += 1
            yield "rep", {
                "case": case["id"],
                "rep": rep + 1,
                "gen_tps": res["gen_tps"],
                "ttft_ms": res["ttft_ms"],
                "pct": round(100 * done_reps / total_reps),
            }
            db.add_run(
                "bench",
                label=case["id"],
                prompt_tokens=res["prompt_tokens"],
                gen_tokens=res["completion_tokens"],
                ttft_ms=res["ttft_ms"],
                prefill_tps=res["prefill_tps"],
                gen_tps=res["gen_tps"],
                rss_mb=res.get("rss_mb"),
            )

        if results:
            summary = _summarize(case, results)
            summaries.append(summary)
            yield "case", summary

    overall = {
        "cases": summaries,
        "elapsed_s": round(time.time() - started, 1),
        "gen_tps": round(statistics.median([s["gen_tps"] for s in summaries]), 2)
        if summaries
        else None,
        "prefill_tps": round(statistics.median([s["prefill_tps"] for s in summaries]), 1)
        if summaries
        else None,
        "rss_mb": max((s.get("rss_mb") or 0) for s in summaries) or None
        if summaries
        else None,
        "info": engine.info(),
        "at": time.time(),
        # False in mock mode, where db.add_run keeps nothing. The page then
        # draws this summary itself instead of reading back what was stored.
        "stored": not engine.cfg.mock,
    }
    yield "summary", overall


def history(limit: int = 120) -> list[dict]:
    return db.recent_runs("bench", limit)


def latest_summary() -> dict | None:
    """Rebuild the most recent suite's headline numbers from stored runs."""
    runs = db.recent_runs("bench", 60)
    if not runs:
        return None
    newest = runs[0]["created_at"]
    window = [r for r in runs if newest - r["created_at"] < 900]  # one sitting
    by_case: dict[str, list[dict]] = {}
    for r in window:
        by_case.setdefault(r["label"] or "unknown", []).append(r)

    cases = []
    for case in CASES:
        rows = by_case.get(case["id"])
        if not rows:
            continue
        cases.append(
            {
                "id": case["id"],
                "label": case["label"],
                "prompt_tokens": rows[0]["prompt_tokens"],
                "gen_tokens": rows[0]["gen_tokens"],
                "reps": len(rows),
                "ttft_p50": round(statistics.median([r["ttft_ms"] for r in rows]), 1),
                "ttft_p95": round(max(r["ttft_ms"] for r in rows), 1),
                "prefill_tps": round(statistics.median([r["prefill_tps"] for r in rows]), 1),
                "gen_tps": round(statistics.median([r["gen_tps"] for r in rows]), 2),
                "gen_tps_min": round(min(r["gen_tps"] for r in rows), 2),
                "gen_tps_max": round(max(r["gen_tps"] for r in rows), 2),
                "rss_mb": max((r["rss_mb"] or 0) for r in rows) or None,
            }
        )
    if not cases:
        return None
    return {
        "cases": cases,
        "gen_tps": round(statistics.median([c["gen_tps"] for c in cases]), 2),
        "prefill_tps": round(statistics.median([c["prefill_tps"] for c in cases]), 1),
        "rss_mb": max((c.get("rss_mb") or 0) for c in cases) or None,
        "at": newest,
    }


# Order-of-magnitude reference points for Qwen2.5-1.5B-Instruct Q4_K_M under
# llama.cpp. Real figures swing widely with memory bandwidth, AVX support and
# (on shared cloud) noisy neighbours — the page says so out loud.
#
# None of these rows is this server. The page draws this server as a row of
# its own, from what the suite measured, rather than claiming a class for it.
REFERENCE = [
    {"hw": "2 vCPU shared cloud", "threads": 2, "prefill": "25–60", "gen": "6–12"},
    {"hw": "4 vCPU dedicated, AVX2", "threads": 4, "prefill": "60–120", "gen": "12–20"},
    {"hw": "Desktop 8-core, DDR5", "threads": 8, "prefill": "200–400", "gen": "30–55"},
    {"hw": "Apple M2 (Metal)", "threads": "—", "prefill": "500+", "gen": "45–70"},
]


# Qwen2.5-1.5B-Instruct at Q4_K_M, for when there is no file to weigh.
DEFAULT_WEIGHTS_MB = 990
# A 7B model at the same quantisation, weights alone: the comparison the
# memory section draws.
LARGER_WEIGHTS_MB = 4400


def weights_mb() -> tuple[int, bool]:
    """The size of the model file and True, or a typical figure and False when
    the file is not there to be weighed (mock mode on a development machine)."""
    try:
        return round(engine.cfg.resolved_model_path.stat().st_size / (1024 * 1024)), True
    except OSError:
        return DEFAULT_WEIGHTS_MB, False


def memory_budget(n_ctx: int) -> list[dict]:
    """An estimate of what a loaded model takes, part by part.

    Arithmetic, not a measurement: the weights are the file's size, the KV
    cache follows from the model's shape (Qwen2.5-1.5B: 28 layers, 2 KV heads,
    128 head dim, f16 => 28 KiB per token), and the last two are round figures.
    The pages label it as an estimate and print the measured resident size of
    the process beside it."""
    kv_mb = round(28 * 2 * 128 * 2 * 2 * n_ctx / (1024 * 1024))
    weights, on_disk = weights_mb()
    return [
        {
            "name": f"Weights ({engine.cfg.model_quant}, mmap)",
            "mb": weights,
            "note": "read once per token" if on_disk else "typical size; no model file on this server",
            "on_disk": on_disk,
        },
        {"name": f"KV cache @ {n_ctx} ctx (f16)", "mb": kv_mb, "note": "28 KiB per token"},
        {"name": "Compute buffers", "mb": 200, "note": "scales with n_batch"},
        {"name": "Python + FastAPI + uvicorn", "mb": 120, "note": "one worker"},
    ]


def larger_model(budget: list[dict], system: dict, limited: bool = True) -> dict:
    """Where a 7B model at the same quantisation would stand on this machine.

    It needs its own weights plus everything else in the budget, which is at
    least what this model needs. What it has to fit in is the machine's memory
    as measured, or the limit on the service where that is tighter. `fits` is
    None when neither can be read: the page then makes no claim either way.

    ``limited`` false says the model is not in this service: it runs in a
    llama-server with limits of its own, so this service's limit does not
    bound it and only the machine's memory counts.
    """
    need = LARGER_WEIGHTS_MB + sum(b["mb"] for b in budget[1:])
    total = system.get("mem_total_mb")
    measured = (system.get("limit_mb"), system.get("limit_high_mb")) if limited else ()
    limits = [v for v in measured if v]
    limit = min(limits) if limits else None
    rooms = [v for v in (total, limit) if v]
    room = min(rooms) if rooms else None
    return {
        "need_mb": need,
        "room_mb": room,
        "by_limit": limit is not None and (not total or limit < total),
        "fits": None if room is None else need <= room,
        "share": None if room is None else round(100 * need / room),
    }
