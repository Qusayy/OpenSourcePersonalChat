"""What the routes share, held where a test can replace it.

A route reads ``deps.engine`` when it runs, not when it is imported, so an
engine a test puts here is the one every route uses.
"""

from __future__ import annotations

from ..llm import engine

__all__ = ["engine"]
