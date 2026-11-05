from __future__ import annotations

from typing import Any, Callable, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..errors import tool_errors
from ..models import Rect
from ..runtime import PyFindRuntime
from ..settings import Settings

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
MatchMode = Literal["in", "=", "startswith", "endswith"]


def register_find_tools(
    mcp: MCPServer, runtime: PyFindRuntime, settings: Settings
) -> dict[str, Callable[..., Any]]:
    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def find_text(
        texts: list[str],
        window_title: str | None = None,
        monitor: int | None = None,
        region: Rect | None = None,
        match_mode: MatchMode = "in",
        timeout: float = 0.0,
        disappear: bool = False,
        all_matches: bool = True,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Locate text on screen using PyFind's Windows OCR matching.

        match_mode: "in" (substring), "=" (exact), "startswith", "endswith".
        With timeout > 0, waits for the text to appear. Set disappear=true to
        wait for it to disappear instead. Returns absolute screen rectangles.
        """
        if len(texts) > 50:
            raise ValueError("texts supports at most 50 strings per call")
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        rect, target = runtime.resolve_target(
            window_title=window_title,
            monitor=monitor,
            region=region,
        )
        result = runtime.find_text(
            texts,
            rect,
            match_mode=match_mode,
            timeout=timeout,
            disappear=disappear,
            all_matches=all_matches,
            max_results=max_results,
        )
        result["target"] = target
        return result

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def find_image(
        templates: list[str],
        window_title: str | None = None,
        monitor: int | None = None,
        region: Rect | None = None,
        threshold: float = 0.8,
        timeout: float = 0.0,
        disappear: bool = False,
        all_matches: bool = True,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Locate PNG or WebP templates on screen using PyFind's template matching.

        Templates may be names from the templates directory (with or without
        .png/.webp) or absolute PNG/WebP paths. With timeout > 0, waits for a
        match. Set disappear=true to wait for the matches to disappear.
        """
        if len(templates) > 50:
            raise ValueError("templates supports at most 50 entries per call")
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        rect, target = runtime.resolve_target(
            window_title=window_title,
            monitor=monitor,
            region=region,
        )
        result = runtime.find_images(
            templates,
            rect,
            threshold=threshold,
            timeout=timeout,
            disappear=disappear,
            all_matches=all_matches,
            max_results=max_results,
        )
        result["target"] = target
        return result

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def list_templates() -> dict[str, Any]:
        """List PNG and WebP templates in the configured templates directory."""
        return runtime.list_templates()

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def reload_templates() -> dict[str, Any]:
        """Clear the template cache and reload PNG and WebP templates from disk."""
        return runtime.reload_templates()

    return {
        "find_text": find_text,
        "find_image": find_image,
        "list_templates": list_templates,
        "reload_templates": reload_templates,
    }