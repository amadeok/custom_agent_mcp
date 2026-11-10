"""Run several PyFind tool calls in one ordered server-side batch.

A batch is a JSON list of command objects::

    [
      {"name": "press_key", "args": ["ctrl+a"]},
      {"name": "sleep", "args": [0.1]},
      {"name": "type_text", "args": ["replacement"]}
    ]

``args`` is a positional argument list, in the same order as the named tool's
parameters. Commands are validated before anything runs, then executed in list
order on the same thread. A built-in ``sleep`` command pauses the queue:
``{"name": "sleep", "args": [0.1]}``.

The point of this tool is focus continuity. Ordinary input calls take focus for
one injection and give it back afterwards, so a selection made by ``press_key``
can be gone by the time a separate ``type_text`` call runs. A queue applies the
same strict focus sequence around the whole list: it blocks input, captures the
current foreground, focuses the target, runs every command, restores the
captured window, and only then releases input.
"""

from __future__ import annotations

import inspect
import math
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from loguru import logger
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ..errors import tool_errors
from ..runtime import DEFAULT_INJECTION_SETTLE, PyFindRuntime
from ..settings import Settings

MAX_COMMANDS = 100
MAX_SLEEP = 30.0

BATCH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

# Commands that inject into a window, or deliberately change focus. A queue
# that contains one of these and names a target gets the strict focus guard.
FOCUS_COMMANDS = frozenset(
    {"click", "drag", "type_text", "press_key", "scroll", "focus_window"}
)


def _sleep(seconds: float) -> dict[str, Any]:
    """Pause the queue. A focused queue keeps input blocked for the pause."""
    time.sleep(seconds)
    return {"ok": True, "seconds": seconds}


def _validate_sleep(seconds: Any) -> None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("sleep seconds must be a number")
    value = float(seconds)
    if not math.isfinite(value):
        raise ValueError("sleep seconds must be finite")
    if value < 0:
        raise ValueError("sleep seconds must be >= 0")
    if value > MAX_SLEEP:
        raise ValueError(f"sleep seconds must be <= {MAX_SLEEP:g}")


class Command(BaseModel):
    """One step in a command queue."""

    name: str
    args: list[Any] = Field(default_factory=list)


# screenshot returns image content, which is not useful inside a blind JSON
# result. run_commands is excluded so a queue cannot recurse. Web tools are
# normally registered on the separate websearch server, but keep them excluded
# defensively: a focused queue would hold the input gate closed while a remote
# request runs.
_EXCLUDED_COMMANDS = frozenset(
    {"screenshot", "run_commands", "web_search", "web_fetch"}
)

CommandHandler = Callable[..., Any]


@dataclass(frozen=True)
class _PlannedCommand:
    name: str
    args: list[Any]
    handler: CommandHandler
    window_title: str | None
    focus_command: bool


def _validate_command(
    index: int,
    command: Command,
    handlers: Mapping[str, CommandHandler],
) -> _PlannedCommand:
    """Validate one command and return its focus target, if it supplies one."""
    name = command.name.strip()
    if name.endswith("()"):
        name = name[:-2].strip()
    if not name:
        raise ValueError(f"commands[{index}]: name must not be empty")

    handler = handlers.get(name)
    if handler is None:
        available = ", ".join(sorted(handlers))
        raise ValueError(
            f"commands[{index}]: unknown function {name!r}. "
            f"Available functions: {available or 'none'}"
        )

    try:
        bound = inspect.signature(handler).bind(*command.args)
    except TypeError as exc:
        raise ValueError(f"commands[{index}] {name!r}: {exc}") from exc

    if name == "sleep":
        try:
            _validate_sleep(bound.arguments.get("seconds"))
        except ValueError as exc:
            raise ValueError(f"commands[{index}] 'sleep': {exc}") from exc

    supplied_title = bound.arguments.get("window_title")
    if not isinstance(supplied_title, str) or not supplied_title.strip():
        supplied_title = None
    return _PlannedCommand(
        name=name,
        args=list(command.args),
        handler=handler,
        window_title=supplied_title,
        focus_command=name in FOCUS_COMMANDS,
    )


def _plan_commands(
    commands: Sequence[Command],
    handlers: Mapping[str, CommandHandler],
) -> list[_PlannedCommand]:
    """Validate the whole queue before the first command runs."""
    if not commands:
        raise ValueError("commands must contain at least one entry")
    if len(commands) > MAX_COMMANDS:
        raise ValueError(f"commands supports at most {MAX_COMMANDS} entries")
    return [
        _validate_command(index, command, handlers)
        for index, command in enumerate(commands)
    ]


def execute_commands(
    runtime: PyFindRuntime,
    handlers: Mapping[str, CommandHandler],
    commands: Sequence[Command],
    *,
    window_title: str | None = None,
    continue_on_error: bool = False,
    settle: float = DEFAULT_INJECTION_SETTLE,
) -> dict[str, Any]:
    """Validate and run a command queue in order."""
    if not 0.0 <= settle <= 2.0:
        raise ValueError("settle must be between 0 and 2 seconds")
    handlers = dict(handlers)
    handlers.setdefault("sleep", _sleep)
    plan = _plan_commands(commands, handlers)

    # A queue that names a window uses the same strict focus sequence as a
    # single input call: block input, capture the previous foreground, focus
    # the target, run the whole queue, restore the previous window, and only
    # then open the gate. If the caller does not name a target, only focus
    # commands that supply their own window_title can choose one.
    focus_target = window_title
    if focus_target is None:
        focus_target = next(
            (
                command.window_title
                for command in plan
                if command.focus_command and command.window_title
            ),
            None,
        )

    focus: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []
    failed = 0
    manager = (
        runtime.focus_sequence(focus_target, settle=settle)
        if focus_target is not None
        else nullcontext(None)
    )
    with manager as focus_payload:
        focus = focus_payload
        for index, command in enumerate(plan):
            try:
                result = command.handler(*command.args)
            except Exception as exc:
                if isinstance(exc, ToolError):
                    logger.debug(
                        "run_commands entry {} ({}) failed: {}",
                        index,
                        command.name,
                        exc,
                    )
                else:
                    logger.exception(
                        "run_commands entry {} ({}) failed", index, command.name
                    )
                failed += 1
                results.append(
                    {
                        "index": index,
                        "function": command.name,
                        "ok": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                if not continue_on_error:
                    break
            else:
                results.append(
                    {
                        "index": index,
                        "function": command.name,
                        "ok": True,
                        "result": result,
                    }
                )

    payload: dict[str, Any] = {
        "ok": failed == 0,
        "command_count": len(plan),
        "completed": len(results) - failed,
        "failed": failed,
        "stopped_early": len(results) < len(plan),
        "continue_on_error": continue_on_error,
        "results": results,
    }
    if focus is not None:
        payload["focus"] = focus
    return payload


def register_batch_tools(
    mcp: MCPServer,
    runtime: PyFindRuntime,
    settings: Settings,
    handlers: Mapping[str, CommandHandler],
) -> None:
    available_handlers = {
        name: handler
        for name, handler in handlers.items()
        if name not in _EXCLUDED_COMMANDS
    }
    # sleep is a queue primitive, not a standalone MCP tool.
    available_handlers.setdefault("sleep", _sleep)
    available = ", ".join(sorted(available_handlers))
    description = (
        "Run a JSON list of PyFind commands in order in one tool call.\n\n"
        'Each entry is {"name": <tool name>, "args": [positional arguments]}. '
        "Arguments follow the named tool's parameter order, for example:\n\n"
        "[\n"
        '  {"name": "press_key", "args": ["ctrl+a"]},\n'
        '  {"name": "sleep", "args": [0.1]},\n'
        '  {"name": "type_text", "args": ["replacement"]}\n'
        "]\n\n"
        "sleep is a built-in queue command; it accepts seconds and pauses in "
        f"place, up to {MAX_SLEEP:g} s. The name sleep() is accepted too. In a "
        "focused queue the input block stays closed during the pause, so keep "
        "sleeps short. Use find_text or find_image with timeout instead when "
        "waiting for UI state.\n\n"
        "Commands run sequentially on the server, so dependent actions such as "
        "Ctrl+A followed by type_text do not need Code Mode or an execute "
        "batch. A focused queue uses the same strict focus rules as a single "
        "input call: input is blocked, the window that currently has focus is "
        "captured, the target is brought to the front, every command runs, and "
        "then the captured window is restored before input is released. A "
        "selection made by one command therefore stays live for the next, and "
        "the human gets their window back afterwards.\n\n"
        "Pass window_title to choose the target explicitly, or let it come "
        "from the first focus command (click, drag, type_text, press_key, "
        "scroll, or focus_window) that supplies one. Prefer the explicit form "
        "for selection-heavy queues. Commands that name a different window can "
        "still take focus for their own injection and restore the queue target "
        "afterwards. The final restore always returns the window that had focus "
        "before the queue, even if a queued command focused another window. "
        "settle delays that final restore so the target can process the last "
        "event.\n\n"
        "The whole queue is validated before anything runs. A missing function "
        "or a bad argument count rejects the queue without side effects. A "
        "command that fails at runtime stops the queue with the results so far "
        "unless continue_on_error is true; inspect ok, failed, stopped_early, "
        "and results. If a queue blocks input, put unblock_user_input last and "
        "use continue_on_error=true so a failure cannot strand the block. A "
        "focused queue already holds the gate for its whole duration, including "
        "any sleep, find_text, or find_image wait, so keep waits short; "
        "releasing it with a queued gate command opens the block early.\n\n"
        "The queue accepts the registered input, gate, find, OCR, and "
        "observation tools, except screenshot and run_commands itself. Web "
        f"tools live in the separate websearch server. Available now: {available}. "
        "Use find_text or find_image with timeout "
        "inside the queue to wait for UI state. A screenshot or a visual "
        "decision ends the queue: inspect, then start the next one."
    )

    @mcp.tool(annotations=BATCH, description=description)
    @tool_errors
    def run_commands(
        commands: list[Command],
        window_title: str | None = None,
        continue_on_error: bool = False,
        settle: float = DEFAULT_INJECTION_SETTLE,
    ) -> dict[str, Any]:
        return execute_commands(
            runtime,
            available_handlers,
            commands,
            window_title=window_title,
            continue_on_error=continue_on_error,
            settle=settle,
        )