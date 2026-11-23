"""Shared command-line runner for the MCP server variants."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import secrets
from collections.abc import Callable, Sequence
from typing import Any

from mcp.server import MCPServer

from .settings import Settings, load_settings

_TRANSPORTS = ("stdio", "streamable-http", "sse")


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <token>`` on every HTTP request.

    This is a static shared-secret check for a trusted network or VPN. It is not
    OAuth: clients configure the header directly, and the token travels in
    plaintext unless the connection is wrapped in TLS.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value for key, value in scope.get("headers") or []
        }
        supplied = headers.get(b"authorization", b"")
        scheme, _, credential = supplied.partition(b" ")
        expected = self.token.encode("utf-8")
        if scheme.lower() != b"bearer" or not secrets.compare_digest(
            credential.strip(), expected
        ):
            from starlette.responses import JSONResponse

            response = JSONResponse(
                {"error": "unauthorized", "detail": "Bearer token required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _http_app(
    server: MCPServer,
    transport: str,
    host: str,
    auth_token: str | None,
):
    """Build the ASGI app for an HTTP transport, with optional bearer auth."""
    if transport == "streamable-http":
        app = server.streamable_http_app(host=host)
    else:
        app = server.sse_app(host=host)
    if auth_token:
        app.add_middleware(BearerAuthMiddleware, token=auth_token)
    return app


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
        help=(
            "Bind address for HTTP transports (default: 127.0.0.1). Use 0.0.0.0 "
            "to accept connections from other machines on the local network."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for HTTP transports (default: 8765).",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("MCP_HTTP_AUTH_TOKEN")
        or os.getenv("WEBSEARCH_MCP_AUTH_TOKEN"),
        help=(
            "Require this bearer token on HTTP transports. Defaults to "
            "MCP_HTTP_AUTH_TOKEN or WEBSEARCH_MCP_AUTH_TOKEN."
        ),
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
        if args.auth_token:
            raise SystemExit("--auth-token requires an HTTP transport")
        server.run(transport="stdio")
        return

    import uvicorn

    app = _http_app(server, args.transport, args.host, args.auth_token)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=server.settings.log_level.lower(),
    )