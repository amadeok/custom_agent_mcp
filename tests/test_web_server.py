"""Tests for the dedicated web search MCP server."""

from __future__ import annotations

import asyncio

from mcp import Client

from custom_agent_mcp.settings import Settings
from custom_agent_mcp.web_server import WEB_INSTRUCTIONS, create_web_server


def _tools(settings: Settings) -> dict:
    server = create_web_server(settings)

    async def run() -> dict:
        async with Client(server) as client:
            result = await client.list_tools()
            return {tool.name: tool for tool in result.tools}

    return asyncio.run(run())


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        **overrides,
    )


def test_web_tools_registered(tmp_path):
    names = list(_tools(_settings(tmp_path)))

    assert "web_search" in names
    assert "web_fetch" in names
    assert "start_searxng" not in names
    assert "screenshot" not in names
    assert "ocr" not in names
    assert "run_commands" not in names
    assert "click" not in names


def test_start_searxng_registered_when_configured(tmp_path):
    names = list(
        _tools(
            _settings(
                tmp_path,
                searxng_start_cmd="C:\\Users\\amade\\Apps\\searxng-windows\\start-searxng.cmd",
            )
        )
    )

    assert "start_searxng" in names


def test_web_tools_are_open_world_reads(tmp_path):
    tools = _tools(_settings(tmp_path))

    for name in ("web_search", "web_fetch"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.open_world_hint is True

    assert "query" in tools["web_search"].input_schema["properties"]
    assert "instance_url" in tools["web_search"].input_schema["properties"]
    assert "url" in tools["web_fetch"].input_schema["properties"]
    assert "max_chars" in tools["web_fetch"].input_schema["properties"]


def test_web_instructions_describe_only_web_tools():
    instructions = WEB_INSTRUCTIONS.lower()

    assert "web_search" in instructions
    assert "web_fetch" in instructions
    assert "start_searxng" in instructions
    assert "screenshot" not in instructions
    assert "run_commands" not in instructions