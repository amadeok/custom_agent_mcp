"""Tests for the Tavily search client."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from custom_agent_mcp.exceptions import TavilyError
from custom_agent_mcp.tavily import TavilyClient, load_api_key, normalize_base_url

PAYLOAD = {
    "query": "test",
    "answer": "A generated answer.",
    "results": [
        {
            "title": "Tavily result",
            "url": "https://tavily.example/one",
            "content": "Tavily snippet",
            "score": 0.91,
            "published_date": "2026-11-01",
            "favicon": "https://tavily.example/favicon.ico",
            "raw_content": "Full page content.",
        },
        {
            "title": "Second result",
            "url": "https://tavily.example/two",
            "content": "Second snippet",
            "score": 0.5,
        },
    ],
    "response_time": "0.2",
    "request_id": "req-1",
}


class _Handler(BaseHTTPRequestHandler):
    status = 200
    last_headers: dict[str, str] = {}
    last_body: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        type(self).last_headers = {
            name.lower(): value for name, value in self.headers.items()
        }
        type(self).last_body = json.loads(raw.decode("utf-8"))
        if type(self).status != 200:
            body = json.dumps(
                {"detail": {"error": f"test error {type(self).status}"}}
            ).encode("utf-8")
            self.send_response(type(self).status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(PAYLOAD).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def tavily_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Handler.status = 200
    _Handler.last_headers = {}
    _Handler.last_body = {}
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


def test_normalize_base_url_strips_search():
    assert normalize_base_url("https://api.tavily.com/") == "https://api.tavily.com"
    assert (
        normalize_base_url("https://api.tavily.com/search/")
        == "https://api.tavily.com"
    )
    with pytest.raises(ValueError):
        normalize_base_url("ftp://example.com")


def test_search_maps_results_and_sends_payload(tavily_server):
    client = TavilyClient(
        tavily_server,
        timeout=5,
        api_key="test-key",
        include_answer=True,
    )

    result = client.search(
        "test",
        max_results=1,
        time_range="week",
        safesearch=1,
        language="en-US",
        include_domains=["example.com"],
        exclude_domains=["bad.example"],
    )

    assert result["ok"] is True
    assert result["provider"] == "tavily"
    assert result["result_count"] == 1
    assert result["results"][0]["url"] == "https://tavily.example/one"
    assert result["results"][0]["snippet"] == "Tavily snippet"
    assert result["results"][0]["raw_content"] == "Full page content."
    assert result["answers"] == [{"answer": "A generated answer.", "url": None}]
    assert result["request_id"] == "req-1"

    assert _Handler.last_headers["authorization"] == "Bearer test-key"
    assert _Handler.last_body["query"] == "test"
    assert _Handler.last_body["max_results"] == 1
    assert _Handler.last_body["time_range"] == "week"
    assert _Handler.last_body["safe_search"] is True
    assert _Handler.last_body["language"] == "en-US"
    assert _Handler.last_body["include_domains"] == ["example.com"]
    assert _Handler.last_body["exclude_domains"] == ["bad.example"]


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (401, "TAVILY_API_KEY"),
        (429, "rate limiting"),
        (432, "plan"),
    ],
)
def test_search_maps_http_errors(tavily_server, status, match):
    _Handler.status = status
    try:
        with pytest.raises(TavilyError, match=match):
            TavilyClient(tavily_server, timeout=5, api_key="key").search("test")
    finally:
        _Handler.status = 200


def test_client_requires_a_key():
    client = TavilyClient(timeout=5, api_key="")
    assert client.available is False
    with pytest.raises(TavilyError, match="TAVILY_API_KEY"):
        client.search("test")


def test_pagination_is_rejected(tavily_server):
    with pytest.raises(TavilyError, match="pagination"):
        TavilyClient(tavily_server, timeout=5, api_key="key").search("test", pageno=2)


def test_load_api_key_from_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TAVILY_API_KEY='file-key'\n", encoding="utf-8")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_MCP_ENV_FILE", str(env_file))

    assert load_api_key() == "file-key"

    monkeypatch.setenv("TAVILY_API_KEY", "env-key")
    assert load_api_key() == "env-key"


def test_load_api_key_from_default_cwd_env_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("TAVILY_API_KEY=cwd-key\n", encoding="utf-8")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_MCP_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_api_key() == "cwd-key"


def test_invalid_search_depth_is_rejected():
    with pytest.raises(ValueError, match="search_depth"):
        TavilyClient(api_key="key", search_depth="slowest")


def test_invalid_max_results_is_rejected(tavily_server):
    with pytest.raises(ValueError, match="max_results"):
        TavilyClient(tavily_server, timeout=5, api_key="key").search("x", max_results=21)


def test_missing_key_is_reported_as_unavailable():
    client = TavilyClient(api_key="")
    assert client.available is False
    assert client.search_url.endswith("/search")