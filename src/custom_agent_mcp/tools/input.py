from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..errors import tool_errors
from ..runtime import PyFindRuntime
from ..settings import Settings

WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


def register_input_tools(mcp: MCPServer, runtime: PyFindRuntime, settings: Settings) -> None:
    @mcp.tool(annotations=WRITE)
    @tool_errors
    def click(
        x: int,
        y: int,
        button: Literal["left", "right", "middle", "move"] = "left",
        clicks: int = 1,
        interval: float = 0.0,
    ) -> dict[str, Any]:
        """Click at absolute screen coordinates, or move the pointer with button="move"."""
        return runtime.click(x, y, button=button, clicks=clicks, interval=interval)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def type_text(text: str, interval: float = 0.0) -> dict[str, Any]:
        """Type text into the currently focused window using PyAutoGUI."""
        return runtime.type_text(text, interval=interval)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def press_key(key: str) -> dict[str, Any]:
        """Press a key or hotkey, for example "enter", "tab", or "ctrl+shift+s"."""
        return runtime.press_key(key)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def scroll(clicks: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        """Scroll the mouse wheel. Positive scrolls up, negative scrolls down."""
        return runtime.scroll(clicks, x=x, y=y)