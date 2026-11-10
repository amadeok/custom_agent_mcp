from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import Client

from custom_agent_mcp.exceptions import SearxngError
from custom_agent_mcp.searxng import (
    SearxngClient,
    fetch_page,
    normalize_base_url,
    normalize_page_url,
    start_instance,
)
from custom_agent_mcp.settings import Settings
from custom_agent_mcp.web_server import create_web_server

SEARCH_PAYLOAD = {
    "query": "searx test",
    "number_of_results": 2,
    "results": [
        {
            "title": "First result",
            "url": "https://example.com/one",
            "content": "First snippet",
            "engine": "duckduckgo",
            "engines": ["duckduckgo"],
            "score": 1.25,
            "category": "general",
            "publishedDate": "2024-01-01",
        },
        {
            "title": "Second result",
            "url": "https://example.com/two",
            "content": "Second snippet",
            "engines": ["brave", "google"],
        },
    ],
    "answers": [{"answer": "42", "url": "https://example.com/answer"}],
    "infoboxes": [{"infobox": "Example", "content": "An example"}],
    "suggestions": ["searx testing"],
    "corrections": ["searxng test"],
    "unresponsive_engines": [["google", "timeout"]],
}

HTML_PAGE = b"""<!doctype html>
<html>
<head>
  <title>Test Page</title>
  <meta name="description" content="A test page.">
  <style>body { color: red; }</style>
</head>
<body>
  <script>alert('nope')</script>
  <h1>Heading</h1>
  <p>Hello <a href="/relative">world</a>.</p>
  <p>Second paragraph.</p>
  <table><tr><td>Left</td><td>Right</td></tr></table>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    search_status = 200
    search_invalid_json = False
    last_search_query: dict[str, list[str]] | None = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/search":
            type(self).last_search_query = parse_qs(parsed.query)
            if type(self).search_status != 200:
                self.send_error(type(self).search_status, "Forbidden")
                return
            if type(self).search_invalid_json:
                self._send(b"<html><body>JSON is disabled</body></html>", "text/html")
                return
            self._send(
                json.dumps(SEARCH_PAYLOAD).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        elif parsed.path == "/page":
            self._send(HTML_PAGE, "text/html; charset=utf-8")
        elif parsed.path == "/plain":
            self._send(b"Plain text body", "text/plain; charset=utf-8")
        elif parsed.path == "/json":
            self._send(b'{"b": 2, "a": 1}', "application/json")
        elif parsed.path == "/binary":
            self._send(b"\x00\x01\x02", "application/octet-stream")
        elif parsed.path == "/large":
            self._send(
                ("<html><body>" + "word " * 1000 + "</body></html>").encode("utf-8"),
                "text/html; charset=utf-8",
            )
        elif parsed.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
        else:
            self.send_error(404, "Not Found")


@pytest.fixture()
def web_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Handler.search_status = 200
    _Handler.search_invalid_json = False
    _Handler.last_search_query = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_normalize_base_url_strips_search_and_slashes():
    assert normalize_base_url("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    assert normalize_base_url("http://127.0.0.1:8888/search/") == "http://127.0.0.1:8888"
    assert (
        normalize_base_url("https://search.example/searxng/")
        == "https://search.example/searxng"
    )
    assert (
        normalize_base_url("https://search.example/searxng/search")
        == "https://search.example/searxng"
    )


@pytest.mark.parametrize(
    "value",
    ["", "127.0.0.1:8888", "ftp://example.com", "http:///missing-host"],
)
def test_normalize_base_url_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        normalize_base_url(value)


def test_normalize_page_url_strips_fragment_and_requires_http():
    assert normalize_page_url("https://example.com/page#part") == "https://example.com/page"
    with pytest.raises(ValueError):
        normalize_page_url("file:///etc/passwd")


def test_search_normalizes_results_and_query_parameters(web_server):
    client = SearxngClient(web_server, timeout=5)

    payload = client.search(
        "searx test",
        max_results=1,
        categories=["general", "news"],
        engines=["duckduckgo", "brave"],
        language="en-US",
        pageno=2,
        time_range="week",
        safesearch=1,
    )

    assert payload["ok"] is True
    assert payload["query"] == "searx test"
    assert payload["instance"] == web_server
    assert payload["page"] == 2
    assert payload["number_of_results"] == 2
    assert payload["result_count"] == 1
    first = payload["results"][0]
    assert first["title"] == "First result"
    assert first["url"] == "https://example.com/one"
    assert first["snippet"] == "First snippet"
    assert first["engine"] == "duckduckgo"
    assert first["published_date"] == "2024-01-01"
    assert payload["answers"] == [
        {"answer": "42", "url": "https://example.com/answer"}
    ]
    assert payload["infoboxes"][0]["infobox"] == "Example"
    assert payload["suggestions"] == ["searx testing"]
    assert payload["corrections"] == ["searxng test"]
    assert payload["unresponsive_engines"] == [["google", "timeout"]]

    params = _Handler.last_search_query
    assert params is not None
    assert params["q"] == ["searx test"]
    assert params["format"] == ["json"]
    assert params["categories"] == ["general,news"]
    assert params["engines"] == ["duckduckgo,brave"]
    assert params["language"] == ["en-US"]
    assert params["pageno"] == ["2"]
    assert params["time_range"] == ["week"]
    assert params["safesearch"] == ["1"]


def test_search_uses_engines_list_for_missing_engine(web_server):
    payload = SearxngClient(web_server, timeout=5).search("searx test")
    second = payload["results"][1]
    assert second["engine"] == "brave, google"
    assert second["engines"] == ["brave", "google"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"query": "x", "max_results": 0},
        {"query": "x", "max_results": 51},
        {"query": "x", "pageno": 0},
        {"query": "x", "safesearch": 3},
        {"query": "x", "time_range": "decade"},
        {"query": "x", "categories": [""]},
        {"query": "x", "engines": ["a", "b"] * 11},
    ],
)
def test_search_rejects_invalid_parameters(web_server, kwargs):
    with pytest.raises(ValueError):
        SearxngClient(web_server, timeout=5).search(**kwargs)


def test_search_forbidden_mentions_json_setting(web_server):
    _Handler.search_status = 403
    try:
        with pytest.raises(SearxngError, match="JSON"):
            SearxngClient(web_server, timeout=5).search("searx test")
    finally:
        _Handler.search_status = 200


def test_search_html_response_mentions_json(web_server):
    _Handler.search_invalid_json = True
    try:
        with pytest.raises(SearxngError, match="invalid JSON"):
            SearxngClient(web_server, timeout=5).search("searx test")
    finally:
        _Handler.search_invalid_json = False


def test_search_unreachable_instance_raises():
    with pytest.raises(SearxngError, match="Could not reach"):
        SearxngClient("http://127.0.0.1:1", timeout=1).search("x")


def test_start_instance_requires_a_command():
    with pytest.raises(ValueError, match="SEARXNG_MCP_START_CMD"):
        start_instance("http://127.0.0.1:1", "  ")


def test_start_instance_skips_the_launcher_when_already_listening():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        result = start_instance(
            f"http://127.0.0.1:{port}",
            "this-command-must-not-run",
        )
    finally:
        listener.close()

    assert result["already_running"] is True
    assert result["started"] is False
    assert result["returncode"] is None


def test_start_instance_reports_a_launcher_that_starts_nothing():
    with pytest.raises(SearxngError, match="not accepting connections"):
        start_instance(
            "http://127.0.0.1:1",
            "exit 0",
            timeout=10,
            wait=0.5,
        )


def test_fetch_page_extracts_readable_html(web_server):
    result = fetch_page(f"{web_server}/page", timeout=5)

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["content_type"] == "text/html"
    assert result["charset"] == "utf-8"
    assert result["title"] == "Test Page"
    assert result["description"] == "A test page."
    assert result["truncated"] is False
    assert "# Heading" in result["text"]
    assert f"[world]({web_server}/relative)" in result["text"]
    assert "Second paragraph." in result["text"]
    assert "| Left | Right" in result["text"]
    assert "alert('nope')" not in result["text"]
    assert "color: red" not in result["text"]


def test_fetch_page_truncates_text(web_server):
    result = fetch_page(f"{web_server}/large", timeout=5, max_chars=100)

    assert result["truncated"] is True
    assert result["char_count"] > 100
    assert len(result["text"]) == 100
    assert result["download_truncated"] is False


def test_fetch_page_plain_text(web_server):
    result = fetch_page(f"{web_server}/plain", timeout=5)

    assert result["content_type"] == "text/plain"
    assert result["title"] is None
    assert result["text"] == "Plain text body"


def test_fetch_page_pretty_prints_json(web_server):
    result = fetch_page(f"{web_server}/json", timeout=5)

    assert result["content_type"] == "application/json"
    assert result["text"] == '{\n  "b": 2,\n  "a": 1\n}'


def test_fetch_page_rejects_binary_content(web_server):
    with pytest.raises(SearxngError, match="Unsupported content type"):
        fetch_page(f"{web_server}/binary", timeout=5)


def test_fetch_page_follows_redirects(web_server):
    result = fetch_page(f"{web_server}/redirect", timeout=5)

    assert result["final_url"] == f"{web_server}/page"
    assert result["title"] == "Test Page"


def test_fetch_page_http_error(web_server):
    with pytest.raises(SearxngError, match="HTTP 404"):
        fetch_page(f"{web_server}/missing", timeout=5)


@pytest.mark.parametrize("max_chars", [0, -1, 200_001])
def test_fetch_page_rejects_invalid_max_chars(web_server, max_chars):
    with pytest.raises(ValueError):
        fetch_page(f"{web_server}/plain", timeout=5, max_chars=max_chars)


def test_web_tools_work_through_the_mcp_client(web_server, tmp_path):
    server = create_web_server(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            searxng_base_url=web_server,
            searxng_timeout=5,
        )
    )

    async def call() -> tuple[object, object]:
        async with Client(server) as client:
            search = await client.call_tool(
                "web_search",
                {"query": "searx test", "max_results": 1},
            )
            fetch = await client.call_tool(
                "web_fetch",
                {"url": f"{web_server}/page", "max_chars": 500},
            )
            return search, fetch

    search, fetch = asyncio.run(call())

    assert search.is_error is False
    assert search.structured_content["results"][0]["title"] == "First result"
    assert fetch.is_error is False
    assert fetch.structured_content["title"] == "Test Page"
    assert "Heading" in fetch.structured_content["text"]
