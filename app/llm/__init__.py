"""The model: the engine that runs it (``core``), in this process or through
a llama-server sidecar (``server``).

``engine`` is the one instance the application shares.
"""

from ..chatml import render_chatml
from .core import Engine, Stop, Stops, engine

__all__ = ["Engine", "Stop", "Stops", "engine", "render_chatml"]
