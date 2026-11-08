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
from ..tinyfish import TinyFishClient, TinyFishError

# Web tools touch the network, so they are open-world reads.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Starting an instance changes machine state, but repeating it is harmless.
START_LOCAL = ToolAnnotations(idempotent_hint=True, open_world_hint=True)

TimeRange = Literal["day", "week", "month", "year"]
Provider = Literal["auto", "searxng", "tinyfish"]


def _searxng_error_message(message: str, settings: Settings) -> str:
    if settings.searxng_start_cmd and "Could not reach" in message:
        return f"{message} Call start_searxng to launch the local instance."
    return message


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
        provider: Provider = "auto",
    ) -> dict[str, Any]:
        """Search the web through SearXNG, TinyFish, or both.

        Returns titles, URLs, snippets, engines, answers, suggestions, and
        instance metadata. The default instance is SEARXNG_MCP_BASE_URL
        (http://127.0.0.1:8888); pass instance_url to override it for one call.
        Use web_fetch when a snippet is not enough.

        provider selects the search method: "auto" (default) queries SearXNG and
        retries through TinyFish when SearXNG is unreachable, errors, or returns
        no results; "searxng" uses only SearXNG; "tinyfish" uses only TinyFish.
        TinyFish needs TINYFISH_API_KEY in the environment or .env (free tier).
        A fallback result reports fallback_used=true and fallback_reason.
        Fallback is skipped when categories, engines, or an explicit
        instance_url is given, and when TINYFISH_MCP_FALLBACK=false.

        categories and engines are lists of SearXNG category or engine names and
        are not supported by provider="tinyfish". time_range limits results to
        day, week, month, or year. safesearch is 0 (off), 1 (moderate), or 2
        (strict); TinyFish has no safesearch filter, so it is ignored there.
        """
        if len(query or "") > 2000:
            raise ValueError("query must be at most 2000 characters")
        if provider not in ("auto", "searxng", "tinyfish"):
            raise ValueError("provider must be 'auto', 'searxng', or 'tinyfish'")
        explicit_instance = bool(instance_url and instance_url.strip())

        if provider == "tinyfish":
            if categories or engines:
                raise ValueError(
                    "provider='tinyfish' does not support categories or engines"
                )
            if explicit_instance:
                raise ValueError("instance_url is only supported by SearXNG")
            return TinyFishClient(
                settings.tinyfish_base_url,
                timeout=settings.tinyfish_timeout,
                env_file=settings.tinyfish_env_file or None,
            ).search(
                query,
                max_results=max_results,
                pageno=pageno,
                time_range=time_range,
                safesearch=safesearch,
                language=language,
            )

        base_url = (
            instance_url.strip() if explicit_instance else settings.searxng_base_url
        )
        client = SearxngClient(
            base_url,
            timeout=settings.searxng_timeout,
            user_agent=settings.searxng_user_agent,
        )
        fallback: TinyFishClient | None = None
        allow_fallback = False
        if provider == "auto":
            fallback = TinyFishClient(
                settings.tinyfish_base_url,
                timeout=settings.tinyfish_timeout,
                env_file=settings.tinyfish_env_file or None,
            )
            allow_fallback = (
                settings.tinyfish_fallback
                and fallback.available
                and not explicit_instance
                and not categories
                and not engines
            )

        searxng_error: str | None = None
        result: dict[str, Any] | None = None
        try:
            result = client.search(
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
            if not allow_fallback:
                raise SearxngError(
                    _searxng_error_message(str(exc), settings)
                ) from exc
            searxng_error = _searxng_error_message(str(exc), settings)

        if result is not None:
            if result["results"] or not allow_fallback:
                result.setdefault("provider", "searxng")
                return result
            fallback_reason = "no_results"
        else:
            fallback_reason = "searxng_error"

        assert fallback is not None
        try:
            fallback_result = fallback.search(
                query,
                max_results=max_results,
                pageno=pageno,
                time_range=time_range,
                safesearch=safesearch,
                language=language,
            )
        except TinyFishError as exc:
            if result is None:
                raise SearxngError(
                    f"{searxng_error} TinyFish fallback failed: {exc}"
                ) from exc
            # SearXNG answered but found nothing and the fallback failed; return
            # the empty SearXNG result with the fallback failure attached.
            result.setdefault("provider", "searxng")
            result["fallback_attempted"] = True
            result["fallback_error"] = str(exc)
            return result

        fallback_result["fallback_used"] = True
        fallback_result["fallback_reason"] = fallback_reason
        fallback_result["searxng_instance"] = settings.searxng_base_url
        if searxng_error is not None:
            fallback_result["searxng_error"] = searxng_error
        return fallback_result

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
