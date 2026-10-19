"""A run's ledger: each thing it did, and what that came to.

One function reads it, from the steps a run is making as it goes or from the
rows the steps table kept of them, so the page is told the same about a run
it is watching and about one it is reading back.

What a step cost is split four ways, and the four add up to the total:
waiting for the one inference slot, the model reading its prompt, the model
writing, and tools. A pass through the model is a reply: one that ended in a
call, or the answer. In runs kept before the model called tools itself, a
choice (route, decide, repair, fill) the model made was one too. Anything
else, a tool call or a choice made by a command, costs no model time.
"""

from __future__ import annotations

from typing import Any, Iterable

# The kinds of step that are a choice made by the model, when it made them.
CHOICES = frozenset({"route", "decide", "repair", "fill"})
# The kinds of step that are a reply of the model.
REPLIES = frozenset({"call", "answer"})


def _get(step: Any, name: str) -> Any:
    return step.get(name) if isinstance(step, dict) else getattr(step, name, None)


def is_pass(step: Any) -> bool:
    """Whether a step was a pass through the model. Read from what a stored
    row keeps (its kind and how it was chosen), so it is the same for both."""
    kind = _get(step, "kind")
    return kind in REPLIES or (kind in CHOICES and _get(step, "via") == "model")


def _ms(value: Any) -> float:
    return float(value or 0.0)


def ledger(steps: Iterable[Any]) -> dict:
    """The run in figures: how many steps and passes, the split of what they
    cost, and each step as the page lists it."""
    steps = list(steps)
    passes = [s for s in steps if is_pass(s)]
    total = round(sum(_ms(_get(s, "total_ms")) for s in steps), 1)
    waiting = round(sum(_ms(_get(s, "queue_ms")) for s in passes), 1)
    reading = round(sum(_ms(_get(s, "first_token_ms")) for s in passes), 1)
    tools = round(sum(_ms(_get(s, "total_ms")) for s in steps if not is_pass(s)), 1)
    return {
        "steps": len(steps),
        "passes": len(passes),
        "total_ms": total,
        "waiting_ms": waiting,
        "reading_ms": reading,
        # What is left of the passes once the waiting and the reading are out,
        # from the figures as rounded, so that the four add up to the total.
        "writing_ms": round(max(total - tools - waiting - reading, 0.0), 1),
        "tools_ms": tools,
        "ledger": [
            {
                "kind": _get(s, "kind"),
                "name": _get(s, "name"),
                "via": _get(s, "via"),
                "ok": bool(_get(s, "ok")),
                "error": _get(s, "error"),
                "tokens_in": _get(s, "prompt_tokens"),
                "tokens_read": _get(s, "new_tokens"),
                "tokens_out": _get(s, "output_tokens"),
                "ms": round(_ms(_get(s, "total_ms")), 1),
            }
            for s in steps
        ],
    }
