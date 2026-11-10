"""Tests for the TinyFish search fallback."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp import Client

from custom_agent_mcp.exceptions import SearxngError
from custom_agent_mcp.searxng import SearxngClient
from custom_agent_mcp.settings import Settings
from custom_agent_mcp.tinyfish import TinyFishClient, TinyFishError, load_api_key
from custom_agent_mcp.web_server import create_web_server

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


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        tinyfish_fallback=True,
    )


def _payload(results: list[dict]) -> dict:
    return {
        "ok": True,
        "query": "test",
        "instance": "http://searx.test",
        "page": 1,
        "result_count": len(results),
        "number_of_results": len(results),
        "results": results,
        "answers": [],
        "infoboxes": [],
        "suggestions": [],
        "corrections": [],
        "unresponsive_engines": [],
    }


def _result(title: str, url: str) -> dict:
    return {
        "title": title,
        "url": url,
        "snippet": "snippet",
        "engine": "brave",
        "engines": ["brave"],
        "score": 1.0,
        "category": "general",
        "published_date": None,
        "thumbnail": None,
    }


def _call_web_search(settings: Settings, **arguments) -> object:
    server = create_web_server(settings)

    async def call():
        async with Client(server) as client:
            return await client.call_tool("web_search", arguments)

    return asyncio.run(call())


def test_no_fallback_when_searxng_has_results(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    result_item = _result("SearXNG result", "https://searx.example")
    monkeypatch.setattr(
        SearxngClient, "search", lambda self, *a, **k: _payload([result_item])
    )

    def fail(*args, **kwargs):
        raise AssertionError("fallback must not run when SearXNG returned results")

    monkeypatch.setattr(TinyFishClient, "search", fail)

    result = _call_web_search(_settings(tmp_path), query="test")

    assert result.is_error is False
    assert result.structured_content["results"][0]["url"] == "https://searx.example"
    assert result.structured_content["provider"] == "searxng"
    assert "fallback_used" not in result.structured_content


def test_fallback_when_searxng_returns_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(SearxngClient, "search", lambda self, *a, **k: _payload([]))
    monkeypatch.setattr(
        TinyFishClient,
        "search",
        lambda self, *a, **k: {
            **_payload([_result("Tiny result", "https://tiny.example")]),
            "provider": "tinyfish",
            "instance": "https://api.search.tinyfish.ai",
        },
    )

    result = _call_web_search(_settings(tmp_path), query="test")

    assert result.is_error is False
    content = result.structured_content
    assert content["fallback_used"] is True
    assert content["fallback_reason"] == "no_results"
    assert content["results"][0]["url"] == "https://tiny.example"


def test_fallback_when_searxng_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))

    def unreachable(self, *args, **kwargs):
        raise SearxngError("Could not reach http://127.0.0.1:8888")

    monkeypatch.setattr(SearxngClient, "search", unreachable)
    monkeypatch.setattr(
        TinyFishClient,
        "search",
        lambda self, *a, **k: {
            **_payload([_result("Tiny result", "https://tiny.example")]),
            "provider": "tinyfish",
        },
    )

    result = _call_web_search(_settings(tmp_path), query="test")

    assert result.is_error is False
    content = result.structured_content
    assert content["fallback_used"] is True
    assert content["fallback_reason"] == "searxng_error"
    assert "Could not reach" in content["searxng_error"]


def test_no_fallback_with_engine_filter(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(SearxngClient, "search", lambda self, *a, **k: _payload([]))

    def fail(*args, **kwargs):
        raise AssertionError("fallback must not run with an engine filter")

    monkeypatch.setattr(TinyFishClient, "search", fail)

    result = _call_web_search(
        _settings(tmp_path), query="test", engines=["duckduckgo"]
    )

    assert result.is_error is False
    assert result.structured_content["results"] == []
    assert result.structured_content["provider"] == "searxng"


def test_no_fallback_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(SearxngClient, "search", lambda self, *a, **k: _payload([]))

    def fail(*args, **kwargs):
        raise AssertionError("fallback must not run when disabled")

    monkeypatch.setattr(TinyFishClient, "search", fail)

    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        tinyfish_fallback=False,
    )
    result = _call_web_search(settings, query="test")

    assert result.is_error is False
    assert result.structured_content["results"] == []
    assert result.structured_content["provider"] == "searxng"


def test_provider_searxng_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))

    def unreachable(self, *args, **kwargs):
        raise SearxngError("Could not reach http://127.0.0.1:8888")

    monkeypatch.setattr(SearxngClient, "search", unreachable)

    def fail(*args, **kwargs):
        raise AssertionError("provider='searxng' must not call TinyFish")

    monkeypatch.setattr(TinyFishClient, "search", fail)

    result = _call_web_search(_settings(tmp_path), query="test", provider="searxng")

    assert result.is_error is True


def test_provider_tinyfish_bypasses_searxng(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))

    def fail(*args, **kwargs):
        raise AssertionError("provider='tinyfish' must not call SearXNG")

    monkeypatch.setattr(SearxngClient, "search", fail)
    monkeypatch.setattr(
        TinyFishClient,
        "search",
        lambda self, *a, **k: {
            **_payload([_result("Tiny result", "https://tiny.example")]),
            "provider": "tinyfish",
        },
    )

    result = _call_web_search(_settings(tmp_path), query="test", provider="tinyfish")

    assert result.is_error is False
    content = result.structured_content
    assert content["provider"] == "tinyfish"
    assert content["results"][0]["url"] == "https://tiny.example"
    assert "fallback_used" not in content


def test_provider_tinyfish_rejects_filters(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYFISH_API_KEY", "test-key")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))

    result = _call_web_search(
        _settings(tmp_path),
        query="test",
        provider="tinyfish",
        engines=["duckduckgo"],
    )

    assert result.is_error is True


def test_provider_tinyfish_requires_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))

    result = _call_web_search(_settings(tmp_path), query="test", provider="tinyfish")

    assert result.is_error is True


def test_searxng_error_propagates_without_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", str(tmp_path / "missing.env"))

    def unreachable(self, *args, **kwargs):
        raise SearxngError("Could not reach http://127.0.0.1:8888")

    monkeypatch.setattr(SearxngClient, "search", unreachable)

    result = _call_web_search(_settings(tmp_path), query="test")

    assert result.is_error is True