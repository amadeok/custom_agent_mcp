from __future__ import annotations

import asyncio

import pytest
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
    assert "move_mouse" not in names
    assert "block_user_input" not in names
    assert "unblock_user_input" not in names
    assert "begin_input_task" not in names
    assert "end_input_task" not in names


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
    assert "move_mouse" in names
    assert "move_mouse_by" in names
    assert "drag" in names
    assert "cursor_position" in names
    assert "focus_window" in names
    assert "block_user_input" not in names
    assert "unblock_user_input" not in names
    assert "begin_input_task" not in names
    assert "end_input_task" not in names


def test_gate_tools_registered_when_enabled(tmp_path):
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        allow_input=True,
        allow_input_gate=True,
    )

    names = _tool_names(settings)

    assert "block_user_input" in names
    assert "unblock_user_input" in names
    assert "begin_input_task" in names
    assert "end_input_task" in names
    assert "input_block_status" in names


def test_gate_requires_input_tools(tmp_path):
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        allow_input=False,
        allow_input_gate=True,
    )

    with pytest.raises(ValueError, match="ALLOW_INPUT"):
        create_server(settings)