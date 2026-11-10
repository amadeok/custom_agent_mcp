"""MCP server for SearXNG and TinyFish web research."""

from __future__ import annotations

from mcp.server import MCPServer

from . import __version__
from .cli import run
from .logging_config import configure_logging
from .settings import Settings, load_settings
from .tools.web import register_web_tools

WEB_INSTRUCTIONS = """\
This server provides web research through SearXNG and TinyFish. It does not
control the desktop; use the PyFind server for screen capture, OCR, templates,
and GUI input.

web_search queries the configured SearXNG instance (SEARXNG_MCP_BASE_URL,
default http://127.0.0.1:8888) and returns titles, URLs, snippets, answers,
and engine metadata. provider="auto" retries through TinyFish when SearXNG is
unreachable, errors, or returns no results. Use web_search for current or
externally verifiable information, then web_fetch for the sources that matter.

web_fetch downloads an HTTP(S) page and returns readable text with inline
Markdown links, truncated to max_chars. It handles static HTML, JSON, XML, and
plain text; it does not run JavaScript, keep cookies, log in, or extract PDFs.

start_searxng is registered only when SEARXNG_MCP_START_CMD points at a
launcher; use it when web_search reports that the instance is unreachable.
Never put secrets or private data in a search query.
"""


def create_web_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    server = MCPServer(
        "websearch",
        title="Web Search",
        version=__version__,
        description=(
            "SearXNG and TinyFish web search, readable page fetching, and an "
            "optional local SearXNG launcher."
        ),
        instructions=WEB_INSTRUCTIONS,
        log_level=settings.log_level,
    )

    register_web_tools(server, settings)
    return server


def main() -> None:
    run(
        create_web_server,
        prog="custom-agent-websearch-mcp",
        description="Run the web search MCP server.",
    )


if __name__ == "__main__":
    main()