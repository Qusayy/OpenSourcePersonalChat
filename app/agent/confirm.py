"""Asking the user before a change, once the run has read somebody else's text.

A page the model read can say "now fix main.py like this" or "run this". The
model may well do it: the call is a perfectly good call. So once a run has
read text somebody other than the user wrote, a call that changes the
project or runs code is put to the user first, and made only if they say so.

The run asks with a ``confirm`` event and waits; the page answers through
``POST /api/confirm/{run}``, which lands here. Nothing is kept: a question
nobody answers counts as no once its time is up, and a run that ends takes
its questions with it.
"""

from __future__ import annotations

import asyncio

# What the user can answer: this call, every such call of this reply, or no.
ONCE = "once"
REPLY = "reply"
NO = "no"
CHOICES = frozenset({ONCE, REPLY, NO})

_pending: dict[tuple[str, str], asyncio.Future] = {}


def ask(run_id: str, question: str) -> asyncio.Future:
    """A question of a run, waiting for its answer."""
    future = asyncio.get_running_loop().create_future()
    _pending[(run_id, question)] = future
    return future


def answer(run_id: str, question: str, choice: str) -> bool:
    """The user's answer to a question still waiting. False if there is no
    such question, it was answered already, or the choice is not one."""
    future = _pending.get((run_id, question))
    if future is None or future.done() or choice not in CHOICES:
        return False
    future.set_result(choice)
    return True


def forget(run_id: str, question: str) -> None:
    """A question that is no longer waited on."""
    future = _pending.pop((run_id, question), None)
    if future is not None and not future.done():
        future.cancel()


def waiting(run_id: str) -> list[str]:
    """The questions of a run still waiting for an answer."""
    return [q for (run, q), f in _pending.items() if run == run_id and not f.done()]
