"""Tests for the Dynamic Search Kit MCP server surface."""

from __future__ import annotations

import asyncio

from mcp import Client

from custom_agent_mcp.dynamic_search_kit import (
    INSTRUCTIONS,
    SERVER_NAME,
    create_dynamic_search_server,
)
from custom_agent_mcp.settings import Settings


def _tools(settings: Settings) -> dict:
    server = create_dynamic_search_server(settings)

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


def test_server_name_is_dynamic_search_kit(tmp_path):
    server = create_dynamic_search_server(_settings(tmp_path))

    assert server.name == SERVER_NAME == "dynamic_search_kit"


def test_combined_mode_registers_one_search_tool(tmp_path):
    names = list(_tools(_settings(tmp_path)))

    assert "dynamic_search" in names
    assert "page_fetch" in names
    assert "fetch_url" in names
    assert "searxng_search" not in names
    assert "tavily_search" not in names
    assert "camoufox_search" not in names
    assert "camoufox_open" in names
    assert "camoufox_read" in names
    assert "camoufox_act" in names
    assert "camoufox_session" in names
    assert "start_searxng" not in names
    assert "screenshot" not in names
    assert "ocr" not in names
    assert "run_commands" not in names


def test_separate_mode_registers_provider_tools(tmp_path):
    names = list(_tools(_settings(tmp_path, tool_mode="separate")))

    assert "dynamic_search" not in names
    assert "searxng_search" in names
    assert "tavily_search" in names
    assert "tinyfish_search" in names
    assert "camoufox_search" in names
    assert "page_fetch" in names
    assert "fetch_url" in names
    assert "camoufox_open" in names


def test_both_mode_registers_combined_and_separate_tools(tmp_path):
    names = list(_tools(_settings(tmp_path, tool_mode="both")))

    assert "dynamic_search" in names
    assert "searxng_search" in names
    assert "tavily_search" in names
    assert "tinyfish_search" in names
    assert "camoufox_search" in names
    assert "page_fetch" in names


def test_provider_subset_controls_registered_tools(tmp_path):
    names = list(
        _tools(
            _settings(
                tmp_path,
                tool_mode="separate",
                search_providers=("searxng",),
            )
        )
    )

    assert "searxng_search" in names
    assert "tavily_search" not in names
    assert "tinyfish_search" not in names
    assert "camoufox_search" not in names
    assert "camoufox_open" not in names
    assert "page_fetch" in names


def test_fetch_url_registered_when_camoufox_enabled(tmp_path):
    names = list(_tools(_settings(tmp_path)))

    assert "fetch_url" in names
    assert "page_fetch" in names
    assert "camoufox_open" in names


def test_fetch_url_not_registered_without_camoufox(tmp_path):
    names = list(
        _tools(
            _settings(
                tmp_path,
                search_providers=("searxng",),
            )
        )
    )

    assert "fetch_url" not in names
    assert "page_fetch" in names


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


def test_search_tools_are_open_world_reads(tmp_path):
    tools = _tools(_settings(tmp_path, tool_mode="both"))

    for name in (
        "dynamic_search",
        "searxng_search",
        "tavily_search",
        "tinyfish_search",
        "camoufox_search",
        "page_fetch",
        "camoufox_read",
    ):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.open_world_hint is True

    for name in ("camoufox_open", "camoufox_act", "camoufox_session"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is not True
        assert annotations.open_world_hint is True


def test_combined_tool_schema_exposes_chain_controls(tmp_path):
    tools = _tools(_settings(tmp_path))
    properties = tools["dynamic_search"].input_schema["properties"]

    assert "query" in properties
    assert "providers" in properties
    assert "strategy" in properties
    assert "instance_url" in properties
    assert "categories" in properties
    assert "engines" in properties


def test_fetch_url_schema_exposes_render_controls(tmp_path):
    tools = _tools(_settings(tmp_path))
    properties = tools["fetch_url"].input_schema["properties"]

    assert "url" in properties
    assert "max_chars" in properties
    assert "render" in properties
    assert "session" in properties
    annotations = tools["fetch_url"].annotations
    assert annotations is not None
    assert annotations.open_world_hint is True
    assert annotations.read_only_hint is not True


def test_page_fetch_schema(tmp_path):
    tools = _tools(_settings(tmp_path))
    properties = tools["page_fetch"].input_schema["properties"]

    assert "url" in properties
    assert "max_chars" in properties


def test_camoufox_tools_expose_session_controls(tmp_path):
    tools = _tools(_settings(tmp_path))

    assert "session" in tools["camoufox_open"].input_schema["properties"]
    assert "session" in tools["camoufox_read"].input_schema["properties"]
    assert "action" in tools["camoufox_act"].input_schema["properties"]
    assert "action" in tools["camoufox_session"].input_schema["properties"]


def test_instructions_describe_the_new_surface():
    instructions = INSTRUCTIONS.lower()

    assert "dynamic_search" in instructions
    assert "page_fetch" in instructions
    assert "searxng_search" in instructions
    assert "tavily_search" in instructions
    assert "camoufox_search" in instructions
    assert "camoufox_open" in instructions
    assert "camoufox_session" in instructions
    assert "start_searxng" in instructions
    assert "screenshot" not in instructions
    assert "run_commands" not in instructions