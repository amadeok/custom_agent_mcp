"""Logging setup shared by the PyFind and web search servers."""

from __future__ import annotations

import sys

from loguru import logger


def configure_logging(level: str) -> None:
    """Send logs to stderr so the MCP stdio wire stays clean."""
    logger.remove()
    logger.add(sys.stderr, level=level, backtrace=False, diagnose=False)