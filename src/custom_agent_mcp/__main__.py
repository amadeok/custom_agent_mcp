from __future__ import annotations

from .cli import run
from .server import create_server


def main() -> None:
    run(
        create_server,
        prog="custom-agent-mcp",
        description="Run the PyFind MCP server.",
    )


if __name__ == "__main__":
    main()