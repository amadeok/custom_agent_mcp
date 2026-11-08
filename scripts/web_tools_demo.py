"""Call the PyFind web tools from a plain Python script, without OpenCode.

There are two ways to reach the same code, and this script shows both:

* direct (default) - import ``custom_agent_mcp.searxng`` and call it. No MCP
  handshake, no server process, no extra dependency beyond ``loguru``.
* ``--mcp`` - spawn ``python -m custom_agent_mcp`` over stdio and call the
  ``web_search`` / ``web_fetch`` tools exactly like OpenCode does. Add
  ``--url`` to attach to an already running streamable-HTTP server instead of
  spawning one.

The SearXNG instance comes from ``SEARXNG_MCP_BASE_URL`` (default
``http://127.0.0.1:8888``), the same variable the MCP server reads.

Examples
--------
    python scripts/web_tools_demo.py "searxng json api"
    python scripts/web_tools_demo.py "python 3.13 release notes" --fetch 1
    python scripts/web_tools_demo.py "mcp python sdk" --mcp
    python scripts/web_tools_demo.py "mcp python sdk" --mcp --url http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from custom_agent_mcp.searxng import SearxngClient, fetch_page
from custom_agent_mcp.settings import Settings, load_settings


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
def print_search(payload: dict[str, Any]) -> None:
    print(f"instance: {payload['instance']}")
    print(f"query:    {payload['query']}")
    print(f"results:  {payload['result_count']}")

    for index, item in enumerate(payload["results"], start=1):
        print(f"\n[{index}] {item['title']}")
        print(f"    {item['url']}")
        if item["snippet"]:
            print(f"    {item['snippet']}")
        engines = ", ".join(item["engines"]) or item["engine"] or "?"
        print(f"    engine: {engines}")

    for answer in payload["answers"]:
        print(f"\nanswer: {answer['answer']}")

    if payload["unresponsive_engines"]:
        print("\nunresponsive engines:")
        for name, reason in payload["unresponsive_engines"]:
            print(f"  - {name}: {reason}")


def print_page(payload: dict[str, Any], preview: int) -> None:
    print(f"\nfetched: {payload['final_url']}")
    print(f"status:  {payload['status']}  type: {payload['content_type']}")
    print(f"title:   {payload['title']}")
    print(f"chars:   {payload['char_count']} (truncated={payload['truncated']})")
    text = payload["text"]
    print("\n" + text[:preview])
    if len(text) > preview:
        print(f"\n... {len(text) - preview} more characters")


def pick_url(payload: dict[str, Any], index: int) -> str:
    results = payload["results"]
    if not 1 <= index <= len(results):
        raise SystemExit(
            f"--fetch {index} is out of range; the search returned {len(results)} results"
        )
    return results[index - 1]["url"]


# --------------------------------------------------------------------------- #
# Mode 1: direct import
# --------------------------------------------------------------------------- #
def run_direct(
    settings: Settings,
    query: str,
    max_results: int,
    fetch_index: int,
    preview: int,
) -> int:
    client = SearxngClient(
        settings.searxng_base_url,
        timeout=settings.searxng_timeout,
        user_agent=settings.searxng_user_agent,
    )
    payload = client.search(query, max_results=max_results)
    print_search(payload)

    if fetch_index:
        page = fetch_page(
            pick_url(payload, fetch_index),
            timeout=settings.searxng_timeout,
            max_chars=max(preview, 1),
            user_agent=settings.searxng_user_agent,
        )
        print_page(page, preview)
    return 0


# --------------------------------------------------------------------------- #
# Mode 2: MCP client
# --------------------------------------------------------------------------- #
def tool_payload(result: Any) -> dict[str, Any]:
    """Return the dict a tool produced, from structured or text content."""
    if result.structured_content:
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError("tool returned no structured content")


async def run_mcp(
    settings: Settings,
    query: str,
    max_results: int,
    fetch_index: int,
    preview: int,
    url: str | None,
) -> int:
    from mcp import Client, StdioServerParameters

    if url:
        target: Any = url
    else:
        # Pass the environment through so the child sees SEARXNG_MCP_BASE_URL.
        target = StdioServerParameters(
            command=sys.executable,
            args=["-m", "custom_agent_mcp"],
            env=dict(os.environ),
        )

    async with Client(target) as client:
        result = await client.call_tool(
            "web_search", {"query": query, "max_results": max_results}
        )
        if result.is_error:
            raise SystemExit(f"web_search failed: {tool_payload(result)}")
        payload = tool_payload(result)
        print_search(payload)

        if fetch_index:
            result = await client.call_tool(
                "web_fetch",
                {"url": pick_url(payload, fetch_index), "max_chars": max(preview, 1)},
            )
            if result.is_error:
                raise SystemExit(f"web_fetch failed: {tool_payload(result)}")
            print_page(tool_payload(result), preview)
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web_tools_demo",
        description="Search and fetch through the PyFind web tools.",
    )
    parser.add_argument("query", help="search query")
    parser.add_argument(
        "--max-results", type=int, default=5, help="results to request (default: 5)"
    )
    parser.add_argument(
        "--fetch",
        type=int,
        default=0,
        metavar="N",
        help="also fetch result N (1-based) and print its text",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=1200,
        help="characters of fetched text to print (default: 1200)",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="go through the MCP server instead of importing the library",
    )
    parser.add_argument(
        "--url",
        help="with --mcp, attach to a running streamable-HTTP server",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()

    if args.mcp:
        return asyncio.run(
            run_mcp(
                settings,
                args.query,
                args.max_results,
                args.fetch,
                args.preview,
                args.url,
            )
        )
    return run_direct(
        settings, args.query, args.max_results, args.fetch, args.preview
    )


if __name__ == "__main__":
    raise SystemExit(main())
