"""The figures a pass reports when it ends: what it read, what it wrote, and
how long each took."""

from __future__ import annotations


def blank(**known) -> dict:
    """The figures of a pass that read no prompt and wrote nothing, with what
    is known of it given. Every figure not given is None."""
    return {
        "cancelled": False,
        "ttft_ms": None,
        "queue_wait_ms": None,
        "prompt_tokens": None,
        "read_tokens": None,
        "cached_tokens": None,
        "completion_tokens": 0,
        "prefill_tps": None,
        "gen_tps": None,
        "total_ms": None,
        "rss_mb": None,
        **known,
    }
