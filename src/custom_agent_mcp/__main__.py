from __future__ import annotations

import argparse
import multiprocessing

from .server import create_server
from .settings import load_settings

_TRANSPORTS = ("stdio", "streamable-http", "sse")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="custom-agent-mcp",
        description="Run the PyFind MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=_TRANSPORTS,
        default="stdio",
        help=(
            "stdio (default) for clients that spawn this process, or "
            "streamable-http for a shared server several clients can attach to."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for HTTP transports (default: 8765).",
    )
    return parser


def main() -> None:
    multiprocessing.freeze_support()
    args = build_parser().parse_args()
    server = create_server(load_settings())

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()