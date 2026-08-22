"""`openworker-mcp` — stdio MCP server for this OpenWorker install (see server.py)."""

from __future__ import annotations

import sys

from .server import main


def cli() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
