from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..errors import tool_errors
from ..runtime import PyFindRuntime
from ..settings import Settings

BLOCK = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)

RELEASE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

STATUS = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def register_gate_tools(mcp: MCPServer, runtime: PyFindRuntime, settings: Settings) -> None:
    @mcp.tool(annotations=BLOCK)
    @tool_errors
    def block_user_input(
        keyboard: bool = True,
        mouse: bool = True,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        """Block physical keyboard and mouse input system-wide.

        The block stays active until unblock_user_input is called, the physical
        emergency hotkey is pressed (Ctrl+Alt+Shift+F12 by default), or the MCP
        server exits. Pass ttl to release automatically after that many seconds;
        the configured maximum is 600 seconds by default. Events injected by
        this server still reach the desktop while the gate is active.
        """
        return runtime.block_user_input(keyboard=keyboard, mouse=mouse, ttl=ttl)

    @mcp.tool(annotations=RELEASE)
    @tool_errors
    def unblock_user_input(keyboard: bool = True, mouse: bool = True) -> dict[str, Any]:
        """Release the physical input block for the selected devices."""
        return runtime.unblock_user_input(keyboard=keyboard, mouse=mouse)

    @mcp.tool(annotations=STATUS)
    @tool_errors
    def input_block_status() -> dict[str, Any]:
        """Report whether physical input is blocked and when it will release."""
        return runtime.input_block_status()