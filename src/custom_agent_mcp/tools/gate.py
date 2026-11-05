from __future__ import annotations

from typing import Any, Callable

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

TASK_START = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def register_gate_tools(
    mcp: MCPServer, runtime: PyFindRuntime, settings: Settings
) -> dict[str, Callable[..., Any]]:
    @mcp.tool(annotations=TASK_START)
    @tool_errors
    def begin_input_task() -> dict[str, Any]:
        """Start an injected-input task and play the ascending start cue.

        Physical input stays available between calls: each input tool blocks
        keyboard and mouse only for the duration of its own injection and
        plays a short blip. The emergency hotkey is installed at task start.
        To hold input blocked continuously, use block_user_input with a
        bounded ttl instead.

        Queue commands that do not need visual feedback in one run_commands
        call, for example ctrl+a immediately followed by type_text to replace a
        selection. A focused run_commands call follows the strict focus rules:
        it blocks input, captures the current foreground, focuses the target,
        runs the queue, restores the captured window, and only then releases
        input. Focus is not restored between queued commands, so the selection
        stays live. Call end_input_task after the last action, even if the task
        failed.
        """
        return runtime.begin_input_task()

    @mcp.tool(annotations=RELEASE)
    @tool_errors
    def end_input_task() -> dict[str, Any]:
        """End the current task, release any remaining block, and play the end cue.

        Call this when the injected-input task finishes, even if it failed.
        """
        return runtime.end_input_task()

    @mcp.tool(annotations=BLOCK)
    @tool_errors
    def block_user_input(
        keyboard: bool = True,
        mouse: bool = True,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        """Manual override: hold physical input blocked system-wide.

        Not part of the normal injected-input task flow. Prefer begin_input_task
        and end_input_task, which block only while each injection is sent. This
        tool keeps the block active until unblock_user_input is called, the
        physical emergency hotkey is pressed (Ctrl+Alt+Shift+F12 by default),
        the MCP server exits, or the ttl expires. Pass a bounded ttl. The
        configured ttl maximum is 600 seconds by default. A start cue plays when
        the block is installed and an end cue when it is fully released (disable
        cues with PYFIND_MCP_BEEP=false). Tagged events injected by this server
        still reach the desktop while the gate is active.
        """
        return runtime.block_user_input(keyboard=keyboard, mouse=mouse, ttl=ttl)

    @mcp.tool(annotations=RELEASE)
    @tool_errors
    def unblock_user_input(keyboard: bool = True, mouse: bool = True) -> dict[str, Any]:
        """Release a manual block created by block_user_input.

        For a normal injected-input task, call end_input_task instead. A
        descending cue plays when the gate is fully released.
        """
        return runtime.unblock_user_input(keyboard=keyboard, mouse=mouse)

    @mcp.tool(annotations=STATUS)
    @tool_errors
    def input_block_status() -> dict[str, Any]:
        """Report task state, whether input is blocked, and when it will release."""
        return runtime.input_block_status()

    return {
        "begin_input_task": begin_input_task,
        "end_input_task": end_input_task,
        "block_user_input": block_user_input,
        "unblock_user_input": unblock_user_input,
        "input_block_status": input_block_status,
    }