"""An MCP server that exposes THIS OpenWorker install to an agent on another machine.

See server.py for the design; cli.py is the `openworker-mcp` entry point.
"""

from .server import build_server

__all__ = ["build_server"]
