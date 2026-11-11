"""Compatibility shim for the old ``custom_agent_mcp.web_server`` entry point.

New configurations should use :mod:`custom_agent_mcp.dynamic_search_kit` or the
``dynamic-search-kit`` console script. This module keeps existing MCP entries
working while they migrate.
"""

from __future__ import annotations

from .dynamic_search_kit import (
    INSTRUCTIONS,
    SERVER_NAME,
    SERVER_TITLE,
    WEB_INSTRUCTIONS,
    create_dynamic_search_server,
    create_web_server,
    main,
)

__all__ = [
    "INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_TITLE",
    "WEB_INSTRUCTIONS",
    "create_dynamic_search_server",
    "create_web_server",
    "main",
]


if __name__ == "__main__":
    main()