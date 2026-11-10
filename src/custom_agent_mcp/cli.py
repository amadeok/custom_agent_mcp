"""Shared command-line runner for the MCP server variants."""

from __future__ import annotations

import argparse
import multiprocessing
from collections.abc import Callable, Sequence
from typing import Any

from mcp.server import MCPServer

from .settings import Settings, load_settings

_TRANSPORTS = ("stdio", "streamable-http", "sse")


def build_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
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


def run(
    create: Callable[[Settings], MCPServer],
    *,
    prog: str,
    description: str,
    argv: Sequence[str] | None = None,
) -> None:
    multiprocessing.freeze_support()
    args: Any = build_parser(prog, description).parse_args(argv)
    server = create(load_settings())

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=args.transport, host=args.host, port=args.port)