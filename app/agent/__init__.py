"""The agent: one message, from the question to the answer.

``run`` drives a message through its replies (see ``loop``); the rest of the
package is what those are made of: the call format the model uses
(``calls``), one reply written in phases (``reply``), the commands that call
a tool without the model (``route``), the prompt a run grows
(``transcript``) and the window it is fitted to (``context``), and the check
on the answer (``verify``).
"""

from .context import RUN_ROOM
from .loop import ALREADY_MADE, LEAST_SHARE, run, what_happened
from .trace import Step, Trace

__all__ = ["ALREADY_MADE", "LEAST_SHARE", "RUN_ROOM", "Step", "Trace", "run", "what_happened"]
