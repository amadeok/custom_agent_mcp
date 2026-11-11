from __future__ import annotations

from typing import Any, Callable, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..dynamic_search import DynamicSearchService, SearchRequest
from ..errors import tool_errors
from ..searxng import DEFAULT_PAGE_CHARS, MAX_PAGE_CHARS, start_instance
from ..settings import Settings

# Web tools touch the network, so they are open-world reads.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Browser actions change remote state and are not safe to retry blindly.
BROWSER_ACTION = ToolAnnotations(open_world_hint=True)
# Starting an instance changes machine state, but repeating it is harmless.
START_LOCAL = ToolAnnotations(idempotent_hint=True, open_world_hint=True)

TimeRange = Literal["day", "week", "month", "year"]
Strategy = Literal["first", "merge"]
FetchRender = Literal["auto", "always", "never"]
BrowserAction = Literal["click", "type", "press_key", "evaluate", "wait"]
SessionAction = Literal["status", "close", "close_all"]


def register_web_tools(
    mcp: MCPServer,
    settings: Settings,
    service: DynamicSearchService | None = None,
) -> dict[str, Callable[..., Any]]:
    service = service or DynamicSearchService(settings)
    handlers: dict[str, Callable[..., Any]] = {}
    providers = set(settings.search_providers)
    mode = settings.tool_mode

    # ------------------------------------------------------------------ #
    # Combined search
    # ------------------------------------------------------------------ #
    if mode in {"combined", "both"}:

        @mcp.tool(annotations=READ_ONLY)
        @tool_errors
        def dynamic_search(
            query: str,
            max_results: int = 10,
            categories: list[str] | None = None,
            engines: list[str] | None = None,
            language: str = "auto",
            pageno: int = 1,
            time_range: TimeRange | None = None,
            safesearch: int = 0,
            providers: list[str] | None = None,
            strategy: Strategy | None = None,
            instance_url: str | None = None,
        ) -> dict[str, Any]:
            """Search the web through the configured provider chain.

            The default chain and order come from DYNAMIC_SEARCH_PROVIDERS
            (default: searxng,tavily,tinyfish,camoufox). Pass providers to override the
            subset and order for one call, for example ["camoufox", "searxng"].
            strategy="first" (default) stops at the first provider with results;
            strategy="merge" queries every provider and merges unique URLs.

            SearXNG supports categories, engines, and instance_url. Tavily and
            Camoufox do not, so those providers are skipped when those filters
            are present. Tavily and Camoufox also do not paginate. The result
            includes attempts, provider_chain, selected_provider, and
            fallback_used so you can see what ran and why.

            Use page_fetch for static pages, or camoufox_open for pages that
            need JavaScript, cookies, or a persistent login.
            """
            request = SearchRequest(
                query=query,
                max_results=max_results,
                categories=tuple(categories) if categories else None,
                engines=tuple(engines) if engines else None,
                language=language,
                pageno=pageno,
                time_range=time_range,
                safesearch=safesearch,
                instance_url=instance_url,
            )
            return service.search_combined(
                request,
                providers=providers,
                strategy=strategy,
            )

        handlers["dynamic_search"] = dynamic_search

    # ------------------------------------------------------------------ #
    # Separate provider tools
    # ------------------------------------------------------------------ #
    if mode in {"separate", "both"}:
        if "searxng" in providers:

            @mcp.tool(annotations=READ_ONLY)
            @tool_errors
            def searxng_search(
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
                """Search only the configured SearXNG instance.

                Returns titles, URLs, snippets, engines, answers, suggestions,
                and instance metadata. The instance must expose its JSON API.
                Pass instance_url to override SEARXNG_MCP_BASE_URL for one call.
                """
                request = SearchRequest(
                    query=query,
                    max_results=max_results,
                    categories=tuple(categories) if categories else None,
                    engines=tuple(engines) if engines else None,
                    language=language,
                    pageno=pageno,
                    time_range=time_range,
                    safesearch=safesearch,
                    instance_url=instance_url,
                )
                return service.search_provider("searxng", request)

            handlers["searxng_search"] = searxng_search

        if "tavily" in providers:

            @mcp.tool(annotations=READ_ONLY)
            @tool_errors
            def tavily_search(
                query: str,
                max_results: int = 10,
                language: str = "auto",
                time_range: TimeRange | None = None,
                safesearch: int = 0,
                include_domains: list[str] | None = None,
                exclude_domains: list[str] | None = None,
            ) -> dict[str, Any]:
                """Search only Tavily.

                Tavily needs TAVILY_API_KEY in the environment or .env. It
                supports up to 20 results, time_range, language, safe search,
                and domain include/exclude filters. It does not paginate.
                """
                request = SearchRequest(
                    query=query,
                    max_results=max_results,
                    language=language,
                    time_range=time_range,
                    safesearch=safesearch,
                    include_domains=tuple(include_domains) if include_domains else None,
                    exclude_domains=tuple(exclude_domains) if exclude_domains else None,
                )
                return service.search_provider("tavily", request)

            handlers["tavily_search"] = tavily_search

        if "camoufox" in providers:

            @mcp.tool(annotations=READ_ONLY)
            @tool_errors
            def camoufox_search(
                query: str,
                max_results: int = 10,
                language: str = "auto",
                time_range: TimeRange | None = None,
                safesearch: int = 0,
                session: str | None = None,
            ) -> dict[str, Any]:
                """Search through the persistent Camoufox browser.

                The browser navigates to CAMOUFOX_MCP_SEARCH_ENGINE (default
                duckduckgo) or CAMOUFOX_MCP_SEARCH_URL and extracts result
                links. Use this when a normal HTTP search is blocked or when the
                search page needs JavaScript. The session stays alive between
                calls; use camoufox_session to inspect or close it.
                """
                request = SearchRequest(
                    query=query,
                    max_results=max_results,
                    language=language,
                    time_range=time_range,
                    safesearch=safesearch,
                )
                return service.search_provider(
                    "camoufox", request, camoufox_session=session
                )  # None uses CAMOUFOX_MCP_SEARCH_SESSION

            handlers["camoufox_search"] = camoufox_search

        if "tinyfish" in providers:

            @mcp.tool(annotations=READ_ONLY)
            @tool_errors
            def tinyfish_search(
                query: str,
                max_results: int = 10,
                language: str = "auto",
                pageno: int = 1,
                time_range: TimeRange | None = None,
                safesearch: int = 0,
            ) -> dict[str, Any]:
                """Search only the TinyFish API.

                TinyFish needs TINYFISH_API_KEY and supports pages 1-11. It is
                a normal provider: include it in DYNAMIC_SEARCH_PROVIDERS or
                DYNAMIC_SEARCH_TOOL_MODE=separate/both.
                """
                request = SearchRequest(
                    query=query,
                    max_results=max_results,
                    language=language,
                    pageno=pageno,
                    time_range=time_range,
                    safesearch=safesearch,
                )
                return service.search_provider("tinyfish", request)

            handlers["tinyfish_search"] = tinyfish_search

    # ------------------------------------------------------------------ #
    # Static page fetch
    # ------------------------------------------------------------------ #
    @mcp.tool(annotations=READ_ONLY)
    @tool_errors
    def page_fetch(
        url: str,
        max_chars: int = DEFAULT_PAGE_CHARS,
    ) -> dict[str, Any]:
        """Download an HTTP(S) page and return its readable text.

        HTML is converted to plain text with inline Markdown links; scripts,
        styles, and tags are removed. JSON is pretty-printed, and plain text or
        XML is returned as-is. Long pages are truncated to max_chars (default
        20000, at most 200000). PDFs and other binary content are rejected.

        This tool does not execute JavaScript, keep cookies, or authenticate.
        Use fetch_url for static-first fetching with a Camoufox fallback, or
        camoufox_open for an interactive persistent session.
        """
        if max_chars > MAX_PAGE_CHARS:
            raise ValueError(f"max_chars must be at most {MAX_PAGE_CHARS}")
        return service.fetch_page(url, max_chars=max_chars)

    handlers["page_fetch"] = page_fetch

    # ------------------------------------------------------------------ #
    # Persistent Camoufox browser tools
    # ------------------------------------------------------------------ #
    if "camoufox" in providers:

        @mcp.tool(annotations=BROWSER_ACTION)
        @tool_errors
        def fetch_url(
            url: str,
            max_chars: int | None = None,
            render: FetchRender | None = None,
            session: str | None = None,
        ) -> dict[str, Any]:
            """Fetch one URL the model selected, static first and rendered if needed.

            Use the search tool first, inspect the returned titles, URLs, and
            snippets, then call fetch_url for each page worth reading. In
            render="auto" mode this tries plain HTTP first and launches Camoufox
            only when the page looks like a JavaScript shell, a challenge, or
            too short to be useful. render="always" forces Camoufox;
            render="never" keeps it static.

            The response reports fetch_method ("static" or "camoufox"),
            render_reason, text, char_count, and static/render errors. The
            default session is CAMOUFOX_MCP_FETCH_SESSION, separate from the
            manual browsing session.
            """
            return service.fetch_url(
                url,
                max_chars=max_chars,
                render=render,
                session=session,
            )

        handlers["fetch_url"] = fetch_url

        @mcp.tool(annotations=BROWSER_ACTION)
        @tool_errors
        def camoufox_open(
            url: str,
            session: str = "default",
            wait_until: Literal[
                "commit", "domcontentloaded", "load", "networkidle"
            ] = "domcontentloaded",
            settle_ms: int | None = None,
            max_chars: int = DEFAULT_PAGE_CHARS,
        ) -> dict[str, Any]:
            """Open a URL in a persistent Camoufox session and return its text.

            The session keeps cookies, local storage, and the current page
            across tool calls. Local sessions use a profile on disk; remote
            sessions connect to CAMOUFOX_MCP_REMOTE_URL. Use camoufox_act to
            click or type, then camoufox_read to inspect the result.
            """
            return service.browser.open(
                url,
                session=session,
                wait_until=wait_until,
                settle_ms=settle_ms,
                max_chars=max_chars,
            )

        handlers["camoufox_open"] = camoufox_open

        @mcp.tool(annotations=READ_ONLY)
        @tool_errors
        def camoufox_read(
            session: str = "default",
            max_chars: int = DEFAULT_PAGE_CHARS,
        ) -> dict[str, Any]:
            """Return the current page text, title, and URL for a session."""
            return service.browser.read(session=session, max_chars=max_chars)

        handlers["camoufox_read"] = camoufox_read

        @mcp.tool(annotations=BROWSER_ACTION)
        @tool_errors
        def camoufox_act(
            action: BrowserAction,
            session: str = "default",
            selector: str | None = None,
            text: str | None = None,
            key: str | None = None,
            expression: str | None = None,
            timeout_ms: int | None = None,
            press_enter: bool = False,
            settle_ms: int | None = None,
            max_chars: int = DEFAULT_PAGE_CHARS,
        ) -> dict[str, Any]:
            """Interact with the current page in a persistent Camoufox session.

            action="click" needs selector. action="type" needs selector and
            text; set press_enter=true to submit. action="press_key" needs key
            and optionally focuses selector first. action="evaluate" runs a
            JavaScript expression and returns its JSON value. action="wait"
            waits for selector, or for timeout_ms when no selector is given.
            The result includes a fresh page snapshot.
            """
            return service.browser.act(
                action,
                session=session,
                selector=selector,
                text=text,
                key=key,
                expression=expression,
                timeout_ms=timeout_ms,
                press_enter=press_enter,
                settle_ms=settle_ms,
                max_chars=max_chars,
            )

        handlers["camoufox_act"] = camoufox_act

        @mcp.tool(annotations=BROWSER_ACTION)
        @tool_errors
        def camoufox_session(
            action: SessionAction = "status",
            session: str | None = None,
        ) -> dict[str, Any]:
            """Inspect or close persistent Camoufox sessions.

            action="status" lists active sessions and their current URLs.
            action="close" closes one session (session is required).
            action="close_all" closes every session. Closing a local session
            keeps its profile on disk, so cookies survive the next launch.
            """
            if action == "status":
                return service.browser.status(session)
            if action == "close":
                if not session:
                    raise ValueError("close requires a session name")
                return service.browser.close(session)
            if action == "close_all":
                return service.browser.close_all()
            raise ValueError("action must be status, close, or close_all")

        handlers["camoufox_session"] = camoufox_session

    # ------------------------------------------------------------------ #
    # Optional local SearXNG launcher
    # ------------------------------------------------------------------ #
    if settings.searxng_start_cmd:

        @mcp.tool(annotations=START_LOCAL)
        @tool_errors
        def start_searxng() -> dict[str, Any]:
            """Start the local SearXNG instance if it is not already running.

            Runs the launcher configured in SEARXNG_MCP_START_CMD and waits for
            the instance to accept connections. Safe to call repeatedly: when
            the instance is already up it returns immediately with
            already_running=true. Call dynamic_search or searxng_search
            afterwards.
            """
            return start_instance(
                settings.searxng_base_url,
                settings.searxng_start_cmd,
                timeout=settings.searxng_start_timeout,
            )

        handlers["start_searxng"] = start_searxng

    return handlers