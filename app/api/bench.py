"""The benchmark: the suite run as an event stream, and what the page shows
before and after a run."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..config import settings
from ..ops import bench, telemetry
from . import deps
from .sse import SSE_HEADERS, sse

router = APIRouter()


def bench_static() -> dict:
    """What the benchmark page shows before any run: the suite, the reference
    machines, the memory estimate and how a larger model would stand here."""
    budget = bench.memory_budget(settings.n_ctx)
    return {
        "cases": bench.CASES,
        "reference": bench.REFERENCE,
        "budget": budget,
        "larger": bench.larger_model(
            budget, telemetry.snapshot(), limited=deps.engine.runtime != "llama-server"
        ),
        "latest": bench.latest_summary(),
    }


@router.post("/api/bench/run")
async def api_bench_run():
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, data in bench.run_suite():
                yield sse(event, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/api/bench/results")
async def api_bench_results():
    return {**bench_static(), "history": bench.history(), "info": deps.engine.info()}
