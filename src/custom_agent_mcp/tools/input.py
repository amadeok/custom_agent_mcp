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

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

CLICK_BUTTON = Literal[
    "left", "right", "middle", "x1", "x2", "back", "forward", "move"
]
DRAG_BUTTON = Literal["left", "right", "middle", "x1", "x2", "back", "forward"]


def register_input_tools(mcp: MCPServer, runtime: PyFindRuntime, settings: Settings) -> None:
    @mcp.tool(annotations=WRITE)
    @tool_errors
    def click(
        x: int,
        y: int,
        button: CLICK_BUTTON = "left",
        clicks: int = 1,
        interval: float = 0.0,
        modifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Click at absolute screen coordinates, or move the pointer with button="move".

        Events are injected with loge2's passthrough tag, so the click still
        reaches the desktop while block_user_input is active. modifiers are held
        for the click, for example ["ctrl"] for a ctrl-click.
        """
        return runtime.click(
            x,
            y,
            button=button,
            clicks=clicks,
            interval=interval,
            modifiers=modifiers,
        )

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def move_mouse(x: int, y: int) -> dict[str, Any]:
        """Move the pointer to absolute screen coordinates without clicking."""
        return runtime.move_mouse(x, y)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def move_mouse_by(dx: int, dy: int) -> dict[str, Any]:
        """Move the pointer by an exact pixel offset from its current position."""
        return runtime.move_mouse_by(dx, dy)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def drag(
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        button: DRAG_BUTTON = "left",
        duration: float = 0.2,
        modifiers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Press a mouse button at one point, move to another, and release it.

        The button is released in a finally block, so a failed drag cannot leave
        the desktop with a held mouse button.
        """
        return runtime.drag(
            from_x,
            from_y,
            to_x,
            to_y,
            button=button,
            duration=duration,
            modifiers=modifiers,
        )

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def type_text(text: str, interval: float = 0.0) -> dict[str, Any]:
        """Type arbitrary Unicode text into the focused window.

        Uses loge2's KEYEVENTF_UNICODE injection, so it is independent of the
        active keyboard layout and still works while input is blocked.
        """
        return runtime.type_text(text, interval=interval)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def press_key(key: str, hold: float = 0.02) -> dict[str, Any]:
        """Press a key or hotkey, for example "enter", "tab", or "ctrl+shift+s"."""
        return runtime.press_key(key, hold=hold)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def scroll(
        clicks: int,
        x: int | None = None,
        y: int | None = None,
        horizontal: bool = False,
    ) -> dict[str, Any]:
        """Scroll the mouse wheel.

        Positive scrolls up (right when horizontal=True); negative scrolls down
        (left when horizontal=True).
        """
        return runtime.scroll(clicks, x=x, y=y, horizontal=horizontal)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def cursor_position() -> dict[str, Any]:
        """Return the current pointer position in absolute screen coordinates."""
        return runtime.cursor_position()
