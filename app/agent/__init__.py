"""The agent: one message, from the question to the answer.

``run`` drives a message through its stages (see ``loop``); the rest of the
package is what those stages are made of: the routes that cost no model pass
(``route``), the skills (``skills``), the prompt a run grows (``transcript``)
and the window it is fitted to (``context``), and the check on the answer
(``verify``).
"""

from .context import RUN_ROOM
from .loop import ALREADY_LOOKED_UP, LEAST_SHARE, OUT_OF_LOOKUPS, Step, Trace, run, what_happened

__all__ = [
    "ALREADY_LOOKED_UP", "LEAST_SHARE", "OUT_OF_LOOKUPS", "RUN_ROOM",
    "Step", "Trace", "run", "what_happened",
]
