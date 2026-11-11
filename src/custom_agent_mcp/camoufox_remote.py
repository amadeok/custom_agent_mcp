"""Launch a Camoufox Playwright websocket server for remote browser sessions.

Camoufox's ``launch_server`` cannot serve a persistent browser context (the
Playwright server protocol only exposes a launched browser), so this host keeps
one browser process alive for as long as the host runs. That is enough for
persistent MCP sessions across tool calls; use local mode when the profile must
survive a host restart.

Run it on the machine that should own the browser:

    camoufox-remote-host --port 9222 --ws-path dynamic-search

Then point the MCP server at it:

    CAMOUFOX_MCP_MODE=remote
    CAMOUFOX_MCP_REMOTE_URL=ws://127.0.0.1:9222/dynamic-search
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .camoufox_browser import parse_proxy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camoufox-remote-host",
        description=(
            "Run a Camoufox Playwright websocket server. The browser stays alive "
            "until this process exits."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Bind address (default: 127.0.0.1). Use 0.0.0.0 to accept remote "
            "clients; protect the endpoint with a firewall or tunnel."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9222,
        help="Websocket port (default: 9222; 0 picks a free port).",
    )
    parser.add_argument(
        "--ws-path",
        default="dynamic-search",
        help="Websocket URL path (default: dynamic-search).",
    )
    headless = parser.add_mutually_exclusive_group()
    headless.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=True,
        help="Run the browser headless (default).",
    )
    headless.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Show the browser window.",
    )
    parser.add_argument(
        "--humanize",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Humanize cursor movement (default: enabled).",
    )
    parser.add_argument(
        "--locale",
        default="en-US",
        help="Browser locale (default: en-US).",
    )
    parser.add_argument(
        "--os",
        dest="os_name",
        choices=("windows", "macos", "linux"),
        default="windows",
        help="Fingerprint target OS (default: windows).",
    )
    parser.add_argument(
        "--proxy",
        default="",
        help="Proxy URL or JSON object, for example http://user:pass@host:8080.",
    )
    parser.add_argument(
        "--geoip",
        nargs="?",
        const=True,
        default=False,
        help="Match geolocation/locale to the proxy exit IP, or pass an IP.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Camoufox debug output.",
    )
    return parser


def launch_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": args.host,
        "headless": bool(args.headless),
        "humanize": bool(args.humanize),
        "locale": args.locale,
        "os": args.os_name,
        "port": args.port,
        "ws_path": args.ws_path,
    }
    if args.proxy:
        kwargs["proxy"] = parse_proxy(args.proxy)
    if args.geoip:
        kwargs["geoip"] = args.geoip
    if args.debug:
        kwargs["debug"] = True
    return kwargs


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        from camoufox.server import launch_server
    except ImportError as exc:
        raise SystemExit(
            "Camoufox is not installed. Run `pip install -e \".[browser]\"` and "
            "`python -m camoufox fetch` first."
        ) from exc

    try:
        launch_server(**launch_kwargs(args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()