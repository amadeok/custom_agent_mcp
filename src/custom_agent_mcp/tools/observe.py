from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import TextContent, ToolAnnotations

from ..errors import tool_errors
from ..imaging import save_screenshot
from ..models import Rect
from ..runtime import PyFindRuntime
from ..settings import Settings

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def register_observe_tools(mcp: MCPServer, runtime: PyFindRuntime, settings: Settings) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def list_windows(query: str | None = None, visible_only: bool = True) -> dict[str, Any]:
        """List desktop windows with their titles, handles, and absolute screen bounds."""
        return runtime.list_windows(query=query, visible_only=visible_only)

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def list_monitors() -> dict[str, Any]:
        """List physical monitors and the full virtual desktop bounds."""
        return runtime.list_monitors()

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @tool_errors
    def screenshot(
        window_title: str | None = None,
        monitor: int | None = None,
        region: Rect | None = None,
    ) -> list[TextContent | Image]:
        """Capture the screen, a monitor, a window, or an absolute region.

        Saves a full-resolution image (PNG or WebP) and returns a preview image
        plus metadata containing the full path. Use at most one of window_title,
        monitor, or region.
        """
        rect, target = runtime.resolve_target(
            window_title=window_title,
            monitor=monitor,
            region=region,
        )
        frame, capture_meta = runtime.capture(rect, target)
        saved = save_screenshot(frame, settings)
        payload = {
            "ok": True,
            "target": target,
            "capture": capture_meta,
            **saved,
        }
        return [
            TextContent(type="text", text=json.dumps(payload, indent=2)),
            Image(path=saved["preview_path"]),
        ]

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def ocr(
        window_title: str | None = None,
        monitor: int | None = None,
        region: Rect | None = None,
        lang: str = "en-US",
        preprocess: bool = False,
        include_words: bool = False,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        """Run Windows OCR on the screen, a monitor, a window, or an absolute region.

        Returns the recognized text plus line rectangles in absolute screen
        coordinates. Set include_words=true for per-word boxes.

        If your model has native vision, prefer reading a screenshot with it
        instead of calling this tool: vision preserves layout, icons, and
        styling and usually reads text more reliably. This tool uses Windows
        OCR (not PaddleOCR), so its text and line/word boxes can be imperfect.
        Use it mainly when the model cannot see images, or when plain text
        output is enough and an image would be overkill.
        """
        if not 1 <= max_lines <= 2000:
            raise ValueError("max_lines must be between 1 and 2000")
        rect, target = runtime.resolve_target(
            window_title=window_title,
            monitor=monitor,
            region=region,
        )
        frame, capture_meta = runtime.capture(rect, target)
        result = runtime.ocr(
            frame,
            rect,
            lang=lang,
            preprocess=preprocess,
            include_words=include_words,
            max_lines=max_lines,
        )
        result["target"] = target
        result["captured_at"] = capture_meta.get("captured_at")
        return result