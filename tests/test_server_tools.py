from __future__ import annotations

import asyncio

import pytest
from mcp import Client

from custom_agent_mcp.server import INSTRUCTIONS, create_server
from custom_agent_mcp.settings import Settings


def _tools(settings: Settings) -> dict:
    server = create_server(settings)

    async def run() -> dict:
        async with Client(server) as client:
            result = await client.list_tools()
            return {tool.name: tool for tool in result.tools}

    return asyncio.run(run())


def _tool_names(settings: Settings) -> list[str]:
    return list(_tools(settings))


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
    assert "web_search" in names
    assert "web_fetch" in names
    assert "click" not in names
    assert "type_text" not in names
    assert "move_mouse" not in names
    assert "block_user_input" not in names
    assert "unblock_user_input" not in names
    assert "begin_input_task" not in names
    assert "end_input_task" not in names


def test_web_tools_are_open_world_reads(tmp_path):
    tools = _tools(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
        )
    )

    for name in ("web_search", "web_fetch"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.open_world_hint is True

    assert "query" in tools["web_search"].input_schema["properties"]
    assert "instance_url" in tools["web_search"].input_schema["properties"]
    assert "url" in tools["web_fetch"].input_schema["properties"]
    assert "max_chars" in tools["web_fetch"].input_schema["properties"]


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


def test_begin_input_task_schema_has_no_mode(tmp_path):
    tools = _tools(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            allow_input=True,
            allow_input_gate=True,
        )
    )

    schema = tools["begin_input_task"].input_schema
    assert schema.get("properties", {}) == {}
    assert "mode" not in schema.get("properties", {})


def test_begin_input_task_description_requires_batching(tmp_path):
    tools = _tools(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            allow_input=True,
            allow_input_gate=True,
        )
    )

    description = tools["begin_input_task"].description.lower()

    assert "run_commands" in description
    assert "ctrl+a" in description
    assert "selection" in description
    assert "restores the captured window" in description
    assert 'mode="task"' not in description


def test_instructions_require_batching_dependent_input():
    instructions = INSTRUCTIONS.lower()

    assert "run_commands" in instructions
    assert "ctrl+a" in instructions
    assert "type_text" in instructions
    assert "selection" in instructions
    assert "restores the captured window" in instructions
    assert "settle" in instructions
    assert "sleep" in instructions
    assert "separate tool calls" in instructions
    assert "window_title" in instructions
    assert 'mode="task"' not in instructions
    assert "task_mode" not in instructions


def test_input_tool_descriptions_mention_batching(tmp_path):
    tools = _tools(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            allow_input=True,
        )
    )

    for name in ("press_key", "type_text"):
        description = tools[name].description.lower()
        assert "run_commands" in description
        assert "selection" in description


def test_run_commands_is_always_registered(tmp_path):
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
    )

    assert "run_commands" in _tool_names(settings)


def test_run_commands_schema_takes_named_args(tmp_path):
    tools = _tools(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            allow_input=True,
        )
    )

    schema = tools["run_commands"].input_schema
    properties = schema["properties"]
    assert "commands" in properties
    assert "window_title" in properties
    assert "continue_on_error" in properties
    assert "settle" in properties

    command_schema = schema["$defs"]["Command"]
    assert command_schema["required"] == ["name"]
    assert "args" in command_schema["properties"]

    description = tools["run_commands"].description.lower()
    assert "sleep" in description
    assert "built-in" in description


def test_find_tools_expose_the_in_batch_wait_primitives(tmp_path):
    tools = _tools(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
        )
    )

    for name in ("find_text", "find_image"):
        properties = tools[name].input_schema["properties"]
        assert "timeout" in properties
        assert "disappear" in properties