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

FOCUS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

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
        window_title: str | None = None,
    ) -> dict[str, Any]:
        """Click at absolute screen coordinates, or move the pointer with button="move".

        Events are injected with loge2's passthrough tag, so the click still
        reaches the desktop while block_user_input is active. modifiers are held
        for the click, for example ["ctrl"] for a ctrl-click. Keyboard and mouse
        are blocked for the injection. Pass window_title to bring that window to
        the front first; the click fails instead of hitting a window that covers
        the target, and the previously focused window gets focus back afterwards.
        """
        return runtime.click(
            x,
            y,
            button=button,
            clicks=clicks,
            interval=interval,
            modifiers=modifiers,
            window_title=window_title,
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
        window_title: str | None = None,
    ) -> dict[str, Any]:
        """Press a mouse button at one point, move to another, and release it.

        The button is released in a finally block, so a failed drag cannot leave
        the desktop with a held mouse button. Keyboard and mouse are blocked for
        the whole drag. Pass window_title to bring that window to the front
        first; the previously focused window gets focus back afterwards.
        """
        return runtime.drag(
            from_x,
            from_y,
            to_x,
            to_y,
            button=button,
            duration=duration,
            modifiers=modifiers,
            window_title=window_title,
        )

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def type_text(
        text: str,
        interval: float = 0.0,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        """Type arbitrary Unicode text into the focused window.

        Uses loge2's KEYEVENTF_UNICODE injection, so it is independent of the
        active keyboard layout and still works while input is blocked. Keyboard
        and mouse are blocked for the injection. This tool only changes focus to
        reach window_title and always gives the previously focused window its
        focus back afterwards. Always pass the window_title you inspected: if
        that window is not in front, the server blocks input and focuses it
        before typing. If focus cannot be acquired, no text is sent; if focus
        cannot be restored afterwards, the call fails. Both failures are
        reported as errors instead of silently typing into the wrong window.
        """
        return runtime.type_text(
            text, interval=interval, window_title=window_title
        )

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def press_key(
        key: str,
        hold: float = 0.02,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        """Press a key or hotkey, for example "enter", "tab", or "ctrl+shift+s".

        Named keys (enter, tab, ctrl...) go to the window with keyboard focus,
        which may have changed while you were thinking. Keyboard and mouse are
        blocked for the injection. Always pass the window_title you inspected:
        if that window lost focus, the server blocks input and forces it back
        to the foreground before pressing, then restores the window that had
        focus before. If either focus change fails, the call raises an error: a
        lost key, or focus left on the wrong window, is never silent.
        """
        return runtime.press_key(key, hold=hold, window_title=window_title)

    @mcp.tool(annotations=WRITE)
    @tool_errors
    def scroll(
        clicks: int,
        x: int | None = None,
        y: int | None = None,
        horizontal: bool = False,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        """Scroll the mouse wheel.

        Positive scrolls up (right when horizontal=True); negative scrolls down
        (left when horizontal=True). Keyboard and mouse are blocked for the
        injection. Pass window_title to bring that window to the front first;
        the previously focused window gets focus back afterwards.
        """
        return runtime.scroll(
            clicks, x=x, y=y, horizontal=horizontal, window_title=window_title
        )

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def cursor_position() -> dict[str, Any]:
        """Return the current pointer position in absolute screen coordinates."""
        return runtime.cursor_position()

    @mcp.tool(annotations=FOCUS)
    @tool_errors
    def focus_window(window_title: str, timeout: float = 0.5) -> dict[str, Any]:
        """Bring a window to the foreground before interacting with it.

        Use this when a window is behind another window or is not the active
        window. The result reports was_focused (it already had focus) and fails
        if Windows refuses the focus request.
        """
        return runtime.focus_window(window_title, timeout=timeout)