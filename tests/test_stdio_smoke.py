from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def test_stdio_server_lists_tools(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            "PYFIND_MCP_TEMPLATES_DIR": str(tmp_path / "templates"),
            "PYFIND_MCP_OUTPUT_DIR": str(tmp_path / "out"),
            "PYFIND_MCP_ALLOW_INPUT": "false",
            "PYFIND_MCP_ALLOW_INPUT_GATE": "false",
            "PYFIND_MCP_LOG_LEVEL": "ERROR",
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "custom_agent_mcp"],
        env=env,
        cwd=repo_root,
    )

    async def run() -> list[str]:
        async with Client(params) as client:
            result = await client.list_tools()
            return [tool.name for tool in result.tools]

    names = asyncio.run(run())

    assert "screenshot" in names
    assert "ocr" in names
    assert "find_image" in names
    assert "click" not in names
    assert "block_user_input" not in names