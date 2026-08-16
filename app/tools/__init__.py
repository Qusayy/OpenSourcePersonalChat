"""The tool belt.

Importing this module registers every tool. Nothing else needs to know which
file a tool lives in.
"""

from .base import REGISTRY, Tool, ToolResult, call_tool, tools_to_gbnf
from . import offline, web, reader  # noqa: F401  (import registers the tools)

__all__ = ["REGISTRY", "Tool", "ToolResult", "call_tool", "tools_to_gbnf"]
