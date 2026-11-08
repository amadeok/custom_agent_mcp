from __future__ import annotations

import builtins
import sys

from loguru import logger
from mcp.server import MCPServer

from . import __version__
from .runtime import PyFindRuntime
from .settings import Settings, load_settings
from .tools.batch import register_batch_tools
from .tools.find import register_find_tools
from .tools.gate import register_gate_tools
from .tools.input import register_input_tools
from .tools.observe import register_observe_tools
from .tools.web import register_web_tools

# PyFind (and some of its dependencies) use print() for diagnostics. stdout is
# the MCP stdio wire, so send every print to stderr before anything else runs.
_REAL_PRINT = builtins.print


def _stderr_print(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    _REAL_PRINT(*args, **kwargs)


builtins.print = _stderr_print

INSTRUCTIONS = """\
This server combines local Windows screen automation (PyFind) with web
research through SearXNG.

Start with list_windows or list_monitors to learn the desktop layout, then use
screenshot or ocr to inspect the current screen. Prefer find_text and
find_image over guessing coordinates.

All rectangles are absolute virtual-screen pixels. Secondary monitors may have
negative x or y values. A rectangle is {x, y, width, height}. Use at most one of
window_title, monitor, or region in a call.

SearXNG web tools are always available. web_search queries the configured
instance (SEARXNG_MCP_BASE_URL, default http://127.0.0.1:8080) and returns
titles, URLs, snippets, answers, and engine metadata. web_fetch downloads an
HTTP(S) page and returns readable text with inline Markdown links, truncated
to max_chars. Use web_search for current or externally verifiable information,
then web_fetch for the sources that matter. Do not put web tools in
run_commands: they are network calls, and a focused queue would hold the input
gate closed while the request runs. Never put secrets or private data in a
search query.
Start_searxng is registered only when SEARXNG_MCP_START_CMD points at a
launcher; use it when web_search reports that the instance is unreachable.

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
task-start cue plays at begin and the descending task-end cue at end. Physical
input stays available between calls: each input tool blocks both keyboard and
mouse only for the duration of its own injection and plays a short blip. To
hold input blocked continuously, use block_user_input with a bounded ttl.

Queue dependent input in ONE run_commands call. The tool takes a JSON list of
{"name": ..., "args": [...]} entries and runs them in order on the server, so
it does not need Code Mode or an execute batch. Arguments are positional, in
the named tool's parameter order. For example, replace a selection with
commands [{"name": "press_key", "args": ["ctrl+a"]}, {"name": "sleep",
"args": [0.1]}, {"name": "type_text", "args": ["replacement"]}]. The built-in
sleep command pauses the queue; in a focused queue it holds the input block,
so keep it short. A focused queue follows the strict focus sequence:
it blocks input, captures the window that currently has focus, brings the
target to the front, runs every command, restores the captured window, and
only then releases input. Queued input calls therefore skip their own focus
take/restore and a selection is not deactivated between commands; the human
gets their previous window back after the queue. Pass window_title to choose
the target, or let it come from the first focus command that supplies one.
settle delays the final restore. A focused queue holds input for its whole
duration, so keep waits short. The whole queue is validated before anything
runs, so a typo has no side effects. A failed command stops the queue unless
continue_on_error=true; inspect ok, failed, stopped_early, and results. If a
queue blocks input, put unblock_user_input last and continue_on_error=true so
a failure cannot strand the block.

Do not split a dependent sequence across separate tool calls: focus is
restored after every standalone injection, so a selection, caret, or transient
menu can be lost. If a step needs visual inspection, end the queue, inspect
the result, and start the next one. Inside a queue, use find_text or find_image
with timeout to wait for UI state instead of sleeping. Never run input tools
concurrently; their order and focus are stateful.

Input is not held between calls, so the foreground window can change while you
observe or think. Always pass the window_title you inspected to press_key and
type_text: if that window lost focus, the server blocks keyboard and mouse,
forces it to the foreground, sends the keys, and then gives focus back to the
window that had it before. If the target cannot be focused the call fails and
nothing is sent; if the previous window cannot be focused again the call also
fails, so focus is never silently left on the wrong window. click, drag, and
scroll accept window_title too: they focus the target for the injection, which
prevents another window covering the mouse target, and then restore the previous
focus. Use focus_window when you deliberately want to leave a window in front.

type_text sends one character per SendInput with a short pause (0.03 s by
default) because Chrome and other Chromium targets drop characters from a single
large batch. When press_key or type_text had to take focus, it also keeps the
gate closed for settle seconds (0.2 s by default) before restoring focus, so the
target can process the last event. Raise settle for slow or heavily loaded apps.
For a browser address bar, ctrl+l followed by type_text is usually enough; a
ctrl+a press before typing is a cheap fallback if the URL is appended instead of
replaced.

click, drag, and scroll return the pointer to where it was before the call, so
the human's cursor is not left somewhere random. This happens inside the same
gate and before focus is restored. Pass restore_cursor=false only when the next
step needs the pointer to remain at the target. move_mouse, move_mouse_by, and
click(button="move") are explicit moves and never restore.

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
        description=(
            "Windows screen capture, OCR, template matching, SearXNG web search, "
            "and optional GUI input blocking/injection."
        ),
        instructions=INSTRUCTIONS,
        log_level=settings.log_level,
    )

    handlers = {}
    handlers.update(register_observe_tools(server, runtime, settings))
    handlers.update(register_find_tools(server, runtime, settings))
    handlers.update(register_web_tools(server, settings))
    if settings.allow_input:
        handlers.update(register_input_tools(server, runtime, settings))
        if settings.allow_input_gate:
            handlers.update(register_gate_tools(server, runtime, settings))
    register_batch_tools(server, runtime, settings, handlers)

    # Keep a reference for tests and diagnostics; tools already close over it.
    server._pyfind_runtime = runtime  # type: ignore[attr-defined]
    return server