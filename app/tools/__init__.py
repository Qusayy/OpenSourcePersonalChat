"""The tool belt.

Importing this module registers every tool. Nothing else needs to know which
file a tool lives in.

The order of the imports below is the order of the model's menu, and so part
of the system text a stored turn is replayed by: change it on purpose.
(write_file registers edit_file beside it.)
"""

from .base import REGISTRY, Tool, ToolResult, call_tool
from . import calculator, clock, units, text_stats  # noqa: F401  (import registers the tools)
from . import wikipedia, weather, currency, web_search, news  # noqa: F401
from . import read_url, open_result, run_python, read_file, write_file, run_file  # noqa: F401
from . import notes  # noqa: F401,E402  (last: notes come after every lookup on the menu)

__all__ = ["REGISTRY", "Tool", "ToolResult", "call_tool"]
