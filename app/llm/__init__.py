"""The model: the engine that runs it (``core``), in this process or through
a llama-server sidecar (``server``).

``engine`` is the one instance the application shares.
"""

from .core import Completion, Engine, Stop, engine, render_chatml

__all__ = ["Completion", "Engine", "Stop", "engine", "render_chatml"]
