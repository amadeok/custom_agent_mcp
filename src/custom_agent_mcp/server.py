from __future__ import annotations

import builtins
import sys

from loguru import logger
from mcp.server import MCPServer

from . import __version__
from .runtime import PyFindRuntime
from .settings import Settings, load_settings
from .tools.find import register_find_tools
from .tools.gate import register_gate_tools
from .tools.input import register_input_tools
from .tools.observe import register_observe_tools

# PyFind (and some of its dependencies) use print() for diagnostics. stdout is
# the MCP stdio wire, so send every print to stderr before anything else runs.
_REAL_PRINT = builtins.print


def _stderr_print(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    _REAL_PRINT(*args, **kwargs)


builtins.print = _stderr_print

INSTRUCTIONS = """\
PyFind is a Windows screen-reading and GUI-automation server.

Start with list_windows or list_monitors to learn the desktop layout, then use
screenshot or ocr to inspect the current screen. Prefer find_text and
find_image over guessing coordinates.

All rectangles are absolute virtual-screen pixels. Secondary monitors may have
negative x or y values. A rectangle is {x, y, width, height}. Use at most one of
window_title, monitor, or region in a call.

screenshot writes a full-resolution image (PNG by default, or WebP when
configured) and returns a downscaled preview plus the full path. Templates for
find_image are PNG or WebP names from the configured templates directory, or
absolute paths.

If your model has native vision, prefer reading a screenshot with your own
vision over the ocr tool: vision keeps layout, icons, and styling, and usually
reads text more reliably. This server uses Windows OCR (not PaddleOCR), so its
text and line/word boxes can be imperfect. Use ocr mainly when the model cannot
see images, or when plain text output is enough and an image would be overkill.

MSS captures only visible desktop content. Minimized or fully occluded windows
cannot be read. Input tools are available only when PYFIND_MCP_ALLOW_INPUT=true.
They inject through loge2's tagged SendInput path, so they still work while
block_user_input is active.

Blocking tools are enabled only when PYFIND_MCP_ALLOW_INPUT_GATE=true as well.
For an injected-input task, call begin_input_task before the first input action
and end_input_task after the last, even if the task failed. The ascending
task-start cue plays at begin and the descending task-end cue at end.

begin_input_task takes mode="injection" (default) or mode="task". In injection
mode physical input is not blocked while you observe, wait, or think: each input
tool closes the gate only for the duration of its own injection and plays a
short blip. In task mode keyboard and mouse stay blocked from task start until
end_input_task, the emergency hotkey, or shutdown; choose it when a human must
not interfere for the whole task. Tagged input events still reach the desktop
while the gate is closed.

Input is not held between calls, so the foreground window can change while you
observe or think. Always pass the window_title you inspected to press_key and
type_text: these tools never change focus on their own, so if that window lost
focus the server blocks input, forces it back to the foreground, then sends the
keys. If focus cannot be restored the call fails and nothing is sent. click,
drag, and scroll accept window_title too, which prevents another window covering
the mouse target. Use focus_window when you only need to raise a window.

block_user_input is a manual override that holds input blocked across steps; do
not use it for normal tasks. If you do use it, pass a bounded ttl, confirm the
state with input_block_status, and call unblock_user_input when the task
finishes. A block without a ttl otherwise persists until the physical emergency
hotkey is pressed (Ctrl+Alt+Shift+F12 unless configured otherwise) or the MCP
server exits, so never leave a task without releasing it.
"""


def create_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or load_settings()
    if settings.allow_input_gate and not settings.allow_input:
        raise ValueError(
            "PYFIND_MCP_ALLOW_INPUT_GATE=true requires PYFIND_MCP_ALLOW_INPUT=true."
        )

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, backtrace=False, diagnose=False)

    runtime = PyFindRuntime(settings)
    server = MCPServer(
        "pyfind",
        title="PyFind",
        version=__version__,
        description="Windows screen capture, OCR, template matching, and optional GUI input blocking/injection.",
        instructions=INSTRUCTIONS,
        log_level=settings.log_level,
    )

    register_observe_tools(server, runtime, settings)
    register_find_tools(server, runtime, settings)
    if settings.allow_input:
        register_input_tools(server, runtime, settings)
        if settings.allow_input_gate:
            register_gate_tools(server, runtime, settings)

    # Keep a reference for tests and diagnostics; tools already close over it.
    server._pyfind_runtime = runtime  # type: ignore[attr-defined]
    return server