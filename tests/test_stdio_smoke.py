from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYFIND_MCP_TEMPLATES_DIR": str(tmp_path / "templates"),
            "PYFIND_MCP_OUTPUT_DIR": str(tmp_path / "out"),
            "PYFIND_MCP_ALLOW_INPUT": "false",
            "PYFIND_MCP_ALLOW_INPUT_GATE": "false",
            "PYFIND_MCP_LOG_LEVEL": "ERROR",
            "SEARXNG_MCP_START_CMD": "",
            "DYNAMIC_SEARCH_TOOL_MODE": "combined",
            "DYNAMIC_SEARCH_PROVIDERS": "searxng,tavily,tinyfish,camoufox",
            "DYNAMIC_SEARCH_STRATEGY": "first",
        }
    )
    return env


def _tool_names(tmp_path: Path, args: list[str]) -> list[str]:
    params = StdioServerParameters(
        command=sys.executable,
        args=args,
        env=_env(tmp_path),
        cwd=REPO_ROOT,
    )

    async def run() -> list[str]:
        async with Client(params) as client:
            result = await client.list_tools()
            return [tool.name for tool in result.tools]

    return asyncio.run(run())


def test_stdio_pyfind_server_lists_tools(tmp_path):
    names = _tool_names(tmp_path, ["-m", "custom_agent_mcp"])

    assert "screenshot" in names
    assert "ocr" in names
    assert "find_image" in names
    assert "run_commands" in names
    assert "dynamic_search" not in names
    assert "page_fetch" not in names
    assert "click" not in names
    assert "block_user_input" not in names


def test_stdio_dynamic_search_server_lists_tools(tmp_path):
    names = _tool_names(tmp_path, ["-m", "custom_agent_mcp.dynamic_search_kit"])

    assert "dynamic_search" in names
    assert "page_fetch" in names
    assert "camoufox_open" in names
    assert "screenshot" not in names
    assert "ocr" not in names
    assert "run_commands" not in names
    assert "click" not in names