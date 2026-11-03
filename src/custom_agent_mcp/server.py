from __future__ import annotations

import builtins
import sys

from loguru import logger
from mcp.server import MCPServer

from . import __version__
from .runtime import PyFindRuntime
from .settings import Settings, load_settings
from .tools.find import register_find_tools
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

screenshot writes a full-resolution PNG and returns a downscaled preview plus
the full path. Templates for find_image are PNG names from the configured
templates directory, or absolute PNG paths.

MSS captures only visible desktop content. Minimized or fully occluded windows
cannot be read. Input tools (click, type_text, press_key, scroll) are available
only when PYFIND_MCP_ALLOW_INPUT=true.
"""


def create_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or load_settings()

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, backtrace=False, diagnose=False)

    runtime = PyFindRuntime(settings)
    server = MCPServer(
        "pyfind",
        title="PyFind",
        version=__version__,
        description="Windows screen capture, OCR, template matching, and optional GUI input.",
        instructions=INSTRUCTIONS,
        log_level=settings.log_level,
    )

    register_observe_tools(server, runtime, settings)
    register_find_tools(server, runtime, settings)
    if settings.allow_input:
        register_input_tools(server, runtime, settings)

    # Keep a reference for tests and diagnostics; tools already close over it.
    server._pyfind_runtime = runtime  # type: ignore[attr-defined]
    return server