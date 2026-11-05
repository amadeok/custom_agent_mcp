"""Drive the PyFind MCP server in-process so a debugger can break inside tools.

The server is created directly with ``create_server()`` and connected to an
in-memory MCP client, so no stdio wire, no client process, and no JSON-RPC
handshake gets in the way.  Breakpoints in tool handlers, the runtime, and the
input gate all hit normally.

Examples
--------
    python scripts/debug_mcp.py
    python scripts/debug_mcp.py list_windows
    python scripts/debug_mcp.py ocr '{"region": {"x": 0, "y": 0, "width": 800, "height": 600}}'
    python scripts/debug_mcp.py screenshot '{"window_title": "Notepad"}'

Settings come from the usual PYFIND_MCP_* environment variables, exactly like
``python -m custom_agent_mcp``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp import Client

from custom_agent_mcp.server import create_server
from custom_agent_mcp.settings import load_settings


def _out(text: str) -> None:
    # server.py redirects builtins.print to stderr, so write to the real stdout.
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


async def main(argv: list[str]) -> int:
    server = create_server(load_settings())

    async with Client(server) as client:
        if not argv:
            result = await client.list_tools()
            for tool in result.tools:
                summary = (tool.description or "").splitlines()[0] if tool.description else ""
                _out(f"{tool.name}: {summary}")
            return 0

        name = argv[0]
        raw_args = argv[1] if len(argv) > 1 else "{}"
        arguments: dict[str, Any] = json.loads(raw_args)

        result = await client.call_tool(name, arguments)
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        _out(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 1 if result.is_error else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
