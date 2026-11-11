"""Dynamic Search Kit MCP server.

The server combines SearXNG, Tavily, TinyFish, and persistent Camoufox browser
sessions. What it exposes is controlled by DYNAMIC_SEARCH_TOOL_MODE:

* ``combined`` - one ``dynamic_search`` tool that walks the provider chain.
* ``separate`` - one tool per configured provider.
* ``both`` - the combined tool and the separate tools.

The Camoufox session tools are exposed whenever ``camoufox`` is in
DYNAMIC_SEARCH_PROVIDERS, because they are not search-provider calls: they keep
a browser and login state alive across MCP requests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from mcp.server import MCPServer

from . import __version__
from .cli import run
from .dynamic_search import DynamicSearchService
from .logging_config import configure_logging
from .settings import Settings, load_settings
from .tools.web import register_web_tools

SERVER_NAME = "dynamic_search_kit"
SERVER_TITLE = "Dynamic Search Kit"

INSTRUCTIONS = """\
This server provides web search and persistent browser sessions. It does not
control the desktop; use the PyFind server for screen capture, OCR, templates,
and GUI input.

The server can expose its providers in two ways, controlled by
DYNAMIC_SEARCH_TOOL_MODE. In combined mode it registers dynamic_search, which
walks the configured provider chain and either stops at the first useful result
or merges providers. In separate mode it registers one tool per configured
provider: searxng_search, tavily_search, camoufox_search, and tinyfish_search
when enabled. In both mode all of those tools exist.

dynamic_search accepts a providers list to override the configured subset and
order for one call, and strategy="first" or strategy="merge". The defaults come
from DYNAMIC_SEARCH_PROVIDERS and DYNAMIC_SEARCH_STRATEGY. Its attempts,
provider_chain, and selected_provider fields show what ran and why a provider
was skipped.

The intended workflow is search first, then fetch only the pages the model
chooses. Search tools return titles, URLs, and snippets. fetch_url takes one
selected URL, tries static HTTP first, and launches Camoufox only when the page
looks like a JavaScript shell, a challenge page, or otherwise needs rendering.
render="always" forces Camoufox and render="never" keeps the fetch static. The
fetch session is separate from the browsing session.

page_fetch downloads static HTML, JSON, XML, or text and does not execute
JavaScript. Use it for ordinary pages and APIs. Use fetch_url when a page may
need rendering, or camoufox_open when a page needs JavaScript, cookies,
authentication, or a persistent session. Camoufox
sessions stay alive across tool calls: camoufox_open navigates, camoufox_read
returns the current page, camoufox_act clicks, types, presses keys, evaluates
JavaScript, or waits, and camoufox_session lists or closes sessions. Local
sessions keep their profile on disk; remote sessions connect to a Playwright
websocket endpoint.

start_searxng is registered only when SEARXNG_MCP_START_CMD points at a
launcher. Never put secrets or private data in a search query.
"""


def create_dynamic_search_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    service = DynamicSearchService(settings)

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        try:
            yield {}
        finally:
            service.shutdown()

    server = MCPServer(
        SERVER_NAME,
        title=SERVER_TITLE,
        version=__version__,
        description=(
            "SearXNG, Tavily, TinyFish, and persistent Camoufox web search, with "
            "a configurable combined search tool and readable page fetching."
        ),
        instructions=INSTRUCTIONS,
        log_level=settings.log_level,
        lifespan=lifespan,
    )

    register_web_tools(server, settings, service)

    # Keep a reference for tests and diagnostics; tools already close over it.
    server._dynamic_search_service = service  # type: ignore[attr-defined]
    return server


# Backwards-compatible name used by the old web_server module and tests.
create_web_server = create_dynamic_search_server
WEB_INSTRUCTIONS = INSTRUCTIONS


def main() -> None:
    run(
        create_dynamic_search_server,
        prog="dynamic-search-kit",
        description="Run the Dynamic Search Kit MCP server.",
    )


if __name__ == "__main__":
    main()