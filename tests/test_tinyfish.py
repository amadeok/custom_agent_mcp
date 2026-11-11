"""Tests for the TinyFish search client and its provider registration."""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from custom_agent_mcp.dynamic_search import DynamicSearchService, SearchRequest
from custom_agent_mcp.settings import Settings
from custom_agent_mcp.tinyfish import TinyFishClient, TinyFishError, load_api_key

PAYLOAD = {
    "query": "test",
    "results": [
        {
            "position": 1,
            "site_name": "example.com",
            "title": "Tiny result",
            "snippet": "Tiny snippet",
            "url": "https://tiny.example/one",
            "date": "2026-11-01",
        },
        {
            "position": 2,
            "site_name": "example.org",
            "title": "Second",
            "snippet": "Second snippet",
            "url": "https://tiny.example/two",
        },
    ],
    "total_results": 2,
    "page": 0,
}


class _Handler(BaseHTTPRequestHandler):
    status = 200
    last_headers: dict[str, str] = {}
    last_query: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        type(self).last_headers = {
            name.lower(): value for name, value in self.headers.items()
        }
        type(self).last_query = urllib.parse.parse_qs(parsed.query)

        if type(self).status != 200:
            body = b'{"error": {"code": "TEST"}}'
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
def tiny_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Handler.status = 200
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


def test_client_maps_results_and_sends_key(tiny_server):
    client = TinyFishClient(tiny_server, timeout=5, api_key="test-key")

    result = client.search(
        "test", max_results=1, pageno=2, time_range="week", language="en-US"
    )

    assert result["ok"] is True
    assert result["provider"] == "tinyfish"
    assert result["page"] == 2
    assert result["number_of_results"] == 2
    assert result["result_count"] == 1
    first = result["results"][0]
    assert first["title"] == "Tiny result"
    assert first["url"] == "https://tiny.example/one"
    assert first["snippet"] == "Tiny snippet"
    assert first["engine"] == "tinyfish"
    assert first["published_date"] == "2026-11-01"

    assert _Handler.last_headers["x-api-key"] == "test-key"
    assert _Handler.last_query["page"] == ["1"]
    assert _Handler.last_query["recency_minutes"] == ["10080"]
    assert _Handler.last_query["language"] == ["en"]


@pytest.mark.parametrize(
    ("status", "match"),
    [
        (401, "TINYFISH_API_KEY"),
        (402, "allowance"),
        (429, "rate limit"),
    ],
)
def test_client_maps_http_errors(tiny_server, status, match):
    _Handler.status = status
    try:
        client = TinyFishClient(tiny_server, timeout=5, api_key="test-key")
        with pytest.raises(TinyFishError, match=match):
            client.search("test")
    finally:
        _Handler.status = 200


def test_client_requires_a_key():
    client = TinyFishClient(timeout=5, api_key="")
    assert client.available is False
    with pytest.raises(TinyFishError, match="TINYFISH_API_KEY"):
        client.search("test")


def test_load_api_key_from_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TINYFISH_API_KEY='file-key'\n", encoding="utf-8")
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(env_file))

    assert load_api_key() == "file-key"

    monkeypatch.setenv("TINYFISH_API_KEY", "env-key")
    assert load_api_key() == "env-key"


def test_load_api_key_from_default_cwd_env_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("TINYFISH_API_KEY=cwd-key\n", encoding="utf-8")
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.delenv("TINYFISH_MCP_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_api_key() == "cwd-key"


def test_legacy_tinyfish_provider_can_be_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        search_providers=("tinyfish",),
        tool_mode="separate",
    )
    service = DynamicSearchService(settings)

    monkeypatch.setattr(
        service,
        "search_provider",
        lambda provider, request, **kwargs: {
            "ok": True,
            "provider": "tinyfish",
            "result_count": 1,
            "results": [{"url": "https://tiny.example"}],
            "attempts": [],
        },
    )

    result = service.search_combined(SearchRequest(query="test"))

    assert result["provider"] == "tinyfish"
    assert result["provider_chain"] == ["tinyfish"]