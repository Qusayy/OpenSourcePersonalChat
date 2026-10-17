"""Server-sent events: how a frame is written, and the headers that keep a
proxy from holding frames back."""

from __future__ import annotations

import json
from typing import Any

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx must also carry `proxy_buffering off;` — without both, tokens
    # arrive in one lump at the end and the live metrics are meaningless.
    "X-Accel-Buffering": "no",
}


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
