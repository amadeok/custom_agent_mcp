from __future__ import annotations

from typing import Any, Callable, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..errors import tool_errors
from ..exceptions import SearxngError
from ..searxng import (
    DEFAULT_PAGE_CHARS,
    MAX_PAGE_CHARS,
    MAX_SEARCH_RESULTS,
    SearxngClient,
    fetch_page,
    start_instance,
)
from ..settings import Settings

# Web tools touch the network, so they are open-world reads.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Starting an instance changes machine state, but repeating it is harmless.
START_LOCAL = ToolAnnotations(idempotent_hint=True, open_world_hint=True)

TimeRange = Literal["day", "week", "month", "year"]


def register_web_tools(
    mcp: MCPServer, settings: Settings
) -> dict[str, Callable[..., Any]]:
    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def web_search(
        query: str,
        max_results: int = 10,
        categories: list[str] | None = None,
        engines: list[str] | None = None,
        language: str = "auto",
        pageno: int = 1,
        time_range: TimeRange | None = None,
        safesearch: int = 0,
        instance_url: str | None = None,
    ) -> dict[str, Any]:
        """Search the web through a SearXNG instance.

        Returns titles, URLs, snippets, engines, answers, suggestions, and
        instance metadata. The default instance is SEARXNG_MCP_BASE_URL
        (http://127.0.0.1:8888); pass instance_url to override it for one call.
        Use web_fetch when a snippet is not enough.

        categories and engines are lists of SearXNG category or engine names.
        time_range limits results to day, week, month, or year. safesearch is
        0 (off), 1 (moderate), or 2 (strict).
        """
        if len(query or "") > 2000:
            raise ValueError("query must be at most 2000 characters")
        base_url = (
            instance_url.strip()
            if instance_url and instance_url.strip()
            else settings.searxng_base_url
        )
        client = SearxngClient(
            base_url,
            timeout=settings.searxng_timeout,
            user_agent=settings.searxng_user_agent,
        )
        try:
            return client.search(
                query,
                max_results=max_results,
                categories=categories,
                engines=engines,
                language=language,
                pageno=pageno,
                time_range=time_range,
                safesearch=safesearch,
            )
        except SearxngError as exc:
            if settings.searxng_start_cmd and "Could not reach" in str(exc):
                raise SearxngError(
                    f"{exc} Call start_searxng to launch the local instance."
                ) from exc
            raise

    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def web_fetch(
        url: str,
        max_chars: int = DEFAULT_PAGE_CHARS,
    ) -> dict[str, Any]:
        """Download an HTTP(S) page and return its readable text.

        HTML is converted to plain text with inline Markdown links; scripts,
        styles, and tags are removed. JSON is pretty-printed, and plain text
        or XML is returned as-is. Long pages are truncated to max_chars
        (default 20000, at most 200000), and the result reports char_count and
        truncated. PDFs and other binary content are rejected. Use this after
        web_search when the result snippet is not enough.
        """
        if max_chars > MAX_PAGE_CHARS:
            raise ValueError(f"max_chars must be at most {MAX_PAGE_CHARS}")
        return fetch_page(
            url,
            timeout=settings.searxng_timeout,
            max_chars=max_chars,
            user_agent=settings.searxng_user_agent,
        )

    handlers: dict[str, Callable[..., Any]] = {
        "web_search": web_search,
        "web_fetch": web_fetch,
    }

    if settings.searxng_start_cmd:

        @mcp.tool(annotations=START_LOCAL)
        @tool_errors
        def start_searxng() -> dict[str, Any]:
            """Start the local SearXNG instance if it is not already running.

            Runs the launcher configured in SEARXNG_MCP_START_CMD and waits for
            the instance to accept connections. Safe to call repeatedly: when
            the instance is already up it returns immediately with
            already_running=true. Call web_search afterwards.
            """
            return start_instance(
                settings.searxng_base_url,
                settings.searxng_start_cmd,
                timeout=settings.searxng_start_timeout,
            )

        handlers["start_searxng"] = start_searxng

    return handlers
