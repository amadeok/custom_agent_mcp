from __future__ import annotations

import asyncio

from mcp import Client

from custom_agent_mcp.server import create_server
from custom_agent_mcp.settings import Settings


def _tool_names(settings: Settings) -> list[str]:
    server = create_server(settings)

    async def run() -> list[str]:
        async with Client(server) as client:
            result = await client.list_tools()
            return [tool.name for tool in result.tools]

    return asyncio.run(run())


def test_observation_tools_registered(tmp_path):
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        allow_input=False,
    )

    names = _tool_names(settings)

    assert "list_windows" in names
    assert "list_monitors" in names
    assert "screenshot" in names
    assert "ocr" in names
    assert "find_text" in names
    assert "find_image" in names
    assert "list_templates" in names
    assert "reload_templates" in names
    assert "click" not in names
    assert "type_text" not in names


def test_input_tools_registered_when_enabled(tmp_path):
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        allow_input=True,
    )

    names = _tool_names(settings)

    assert "click" in names
    assert "type_text" in names
    assert "press_key" in names
    assert "scroll" in names