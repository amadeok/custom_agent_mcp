"""Tests for the camofox-browser daemon backend."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from custom_agent_mcp.camoufox_browser import CamoufoxBrowserManager
from custom_agent_mcp.camofox_server import (
    CamofoxServerManager,
    build_session_key,
    normalize_server_url,
    validate_session_prefix,
)
from custom_agent_mcp.dynamic_search import DynamicSearchService
from custom_agent_mcp.exceptions import CamoufoxError
from custom_agent_mcp.settings import Settings, load_settings


class _FakeDaemon:
    """A tiny camofox-browser stand-in with the endpoints the client uses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.headers: list[dict[str, str]] = []
        self.tabs: dict[str, dict] = {}
        self.url = "https://example.com/"
        self.title = "Example Domain"
        self.text = "Example page text"
        self.results: list[dict] = []
        self.selector_visible = True
        self.fail_health = False
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # noqa: D401 - silence test server
                return

            def _payload(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                return json.loads(raw or b"{}")

            def _json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                parsed = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                daemon.calls.append(("GET", parsed.path, query))
                daemon.headers.append(dict(self.headers))
                if parsed.path == "/health":
                    if daemon.fail_health:
                        return self._json(500, {"error": "unhealthy"})
                    return self._json(
                        200,
                        {
                            "ok": True,
                            "running": True,
                            "engine": "camoufox",
                            "version": "2.5.0",
                            "browserConnected": bool(daemon.tabs),
                        },
                    )
                if parsed.path == "/tabs":
                    return self._json(
                        200,
                        {
                            "running": True,
                            "tabs": [
                                {
                                    "tabId": tab_id,
                                    "targetId": tab_id,
                                    "url": tab["url"],
                                    "title": daemon.title,
                                    "listItemId": tab["session_key"],
                                }
                                for tab_id, tab in daemon.tabs.items()
                                if tab["user_id"] == query.get("userId")
                            ],
                        },
                    )
                return self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                parsed = urlsplit(self.path)
                body = self._payload()
                daemon.calls.append(("POST", parsed.path, body))
                daemon.headers.append(dict(self.headers))
                parts = parsed.path.strip("/").split("/")
                if parsed.path == "/tabs":
                    user_id = body.get("userId")
                    session_key = body.get("sessionKey")
                    if not user_id or not session_key:
                        return self._json(
                            400, {"error": "userId and sessionKey required"}
                        )
                    tab_id = f"tab-{len(daemon.tabs) + 1}"
                    daemon.tabs[tab_id] = {
                        "user_id": user_id,
                        "session_key": session_key,
                        "url": "about:blank",
                    }
                    return self._json(200, {"tabId": tab_id, "url": "about:blank"})
                if len(parts) == 3 and parts[0] == "tabs":
                    tab = daemon.tabs.get(parts[1])
                    if tab is None:
                        return self._json(404, {"error": "Tab not found"})
                    action = parts[2]
                    if action == "navigate":
                        tab["url"] = body.get("url", tab["url"])
                        daemon.url = tab["url"]
                        return self._json(200, {"ok": True, "url": tab["url"]})
                    if action == "evaluate":
                        return self._json(
                            200,
                            {
                                "ok": True,
                                "result": daemon._evaluate(
                                    body.get("expression", "")
                                ),
                                "resultType": "object",
                                "truncated": False,
                            },
                        )
                    if action in {"click", "type", "press", "wait"}:
                        return self._json(200, {"ok": True})
                    return self._json(404, {"error": "not found"})
                return self._json(404, {"error": "not found"})

            def do_DELETE(self) -> None:  # noqa: N802 - http.server API
                parsed = urlsplit(self.path)
                body = self._payload()
                daemon.calls.append(("DELETE", parsed.path, body))
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 2 and parts[0] == "tabs":
                    daemon.tabs.pop(parts[1], None)
                    return self._json(200, {"ok": True})
                if len(parts) == 3 and parts[0] == "tabs" and parts[1] == "group":
                    for tab_id, tab in list(daemon.tabs.items()):
                        if tab["session_key"] == parts[2]:
                            daemon.tabs.pop(tab_id)
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def _evaluate(self, expression: str):
        if "document.querySelectorAll" in expression:
            return self.results
        if "const el = document.querySelector" in expression:
            if "el.focus()" in expression:
                return self.selector_visible
            return self.selector_visible
        if "title: document.title" in expression:
            return {"url": self.url, "title": self.title, "text": self.text}
        if "document.title" in expression:
            return self.title
        return "ok"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def daemon():
    server = _FakeDaemon()
    try:
        yield server
    finally:
        server.close()


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        **overrides,
    )


def _manager(tmp_path, daemon: _FakeDaemon, **overrides) -> CamofoxServerManager:
    overrides.setdefault("camoufox_settle_ms", 0)
    return CamofoxServerManager(
        _settings(
            tmp_path,
            camoufox_mode="camofox",
            camoufox_server_url=daemon.base_url,
            **overrides,
        )
    )


def test_normalize_server_url_and_prefix():
    assert normalize_server_url("http://localhost:9377/") == (
        "http://localhost:9377"
    )
    with pytest.raises(ValueError, match="http"):
        normalize_server_url("localhost:9377")
    with pytest.raises(ValueError, match="query"):
        normalize_server_url("http://localhost:9377/?x=1")
    assert validate_session_prefix("dsk") == "dsk"
    assert validate_session_prefix("") == ""
    with pytest.raises(ValueError):
        validate_session_prefix("bad prefix")
    assert build_session_key("dsk", "work") == "dsk-work"
    assert build_session_key("", "work") == "work"


def test_settings_camofox_mode_reads_camofox_env(monkeypatch):
    for name in (
        "CAMOUFOX_MCP_MODE",
        "CAMOUFOX_MCP_SERVER_URL",
        "CAMOUFOX_MCP_SERVER_API_KEY",
        "CAMOUFOX_MCP_SERVER_USER",
        "CAMOUFOX_MCP_SERVER_SESSION_PREFIX",
        "CAMOFOX_URL",
        "CAMOFOX_API_KEY",
        "CAMOFOX_DEFAULT_USER_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CAMOUFOX_MCP_MODE", "camofox")
    monkeypatch.setenv("CAMOFOX_URL", "http://localhost:9377")
    monkeypatch.setenv("CAMOFOX_API_KEY", "secret")
    monkeypatch.setenv("CAMOFOX_DEFAULT_USER_ID", "shared")

    settings = load_settings()

    assert settings.camoufox_mode == "camofox"
    assert settings.camoufox_server_url == "http://localhost:9377"
    assert settings.camoufox_server_api_key == "secret"
    assert settings.camoufox_server_user == "shared"
    assert settings.camoufox_server_session_prefix == "dsk"


def test_settings_reject_invalid_server_url(tmp_path):
    with pytest.raises(ValueError, match="http"):
        _settings(
            tmp_path,
            camoufox_mode="camofox",
            camoufox_server_url="ftp://localhost:9377",
        )


def test_dynamic_search_service_picks_backend(tmp_path):
    local = DynamicSearchService(_settings(tmp_path))
    remote = DynamicSearchService(
        _settings(
            tmp_path,
            camoufox_mode="camofox",
            camoufox_server_url="http://127.0.0.1:9",
        )
    )

    assert isinstance(local.browser, CamoufoxBrowserManager)
    assert isinstance(remote.browser, CamofoxServerManager)
    local.shutdown()
    remote.shutdown()


def test_open_read_and_close_use_the_daemon(daemon, tmp_path):
    manager = _manager(tmp_path, daemon, camoufox_server_api_key="secret")

    opened = manager.open("https://example.com", session="work")

    assert opened["ok"] is True
    assert opened["url"] == "https://example.com/"
    assert opened["title"] == "Example Domain"
    assert opened["text"] == "Example page text"
    assert daemon.tabs["tab-1"]["session_key"] == "dsk-work"
    assert any(
        method == "POST" and path == "/tabs/tab-1/navigate"
        for method, path, _ in daemon.calls
    )
    assert daemon.headers[0]["Authorization"] == "Bearer secret"

    daemon.text = "Updated text"
    read = manager.read(session="work")
    assert read["text"] == "Updated text"
    assert sum(1 for method, path, _ in daemon.calls if path == "/tabs" and method == "POST") == 1

    closed = manager.close("work")
    assert closed == {"ok": True, "session": "work", "closed": True}
    assert daemon.tabs == {}
    assert any(
        method == "DELETE" and path == "/tabs/group/dsk-work"
        for method, path, _ in daemon.calls
    )
    manager.shutdown()


def test_act_translates_to_daemon_endpoints(daemon, tmp_path):
    manager = _manager(tmp_path, daemon)
    manager.open("https://example.com", session="work")

    click = manager.act("click", session="work", selector="#submit")
    assert click["action"] == "click"
    assert any(
        method == "POST" and path == "/tabs/tab-1/click" and body["selector"] == "#submit"
        for method, path, body in daemon.calls
    )

    manager.act(
        "type",
        session="work",
        selector="#q",
        text="hello",
        press_enter=True,
    )
    type_call = next(
        body
        for method, path, body in daemon.calls
        if method == "POST" and path == "/tabs/tab-1/type"
    )
    assert type_call["text"] == "hello"
    assert any(
        method == "POST" and path == "/tabs/tab-1/press" and body["key"] == "Enter"
        for method, path, body in daemon.calls
    )

    manager.act("press_key", session="work", selector="#q", key="Control+A")
    assert any(
        method == "POST"
        and path == "/tabs/tab-1/evaluate"
        and "el.focus()" in body["expression"]
        for method, path, body in daemon.calls
    )

    evaluated = manager.act(
        "evaluate", session="work", expression="() => document.title"
    )
    assert evaluated["result"] == "Example Domain"

    waited = manager.act("wait", session="work", selector="#q", timeout_ms=1000)
    assert waited["ok"] is True

    manager.shutdown()


def test_search_uses_the_daemon(daemon, tmp_path):
    daemon.results = [
        {"title": "Python", "url": "https://python.org/", "snippet": "docs"},
        {"title": "Docs", "url": "https://docs.python.org/", "snippet": "manual"},
    ]
    manager = _manager(tmp_path, daemon)

    result = manager.search("python", max_results=2, session="search")

    assert result["provider"] == "camoufox"
    assert result["session"] == "search"
    assert result["result_count"] == 2
    assert [item["url"] for item in result["results"]] == [
        "https://python.org/",
        "https://docs.python.org/",
    ]
    assert "q=python" in result["search_url"]
    assert daemon.tabs["tab-1"]["session_key"] == "dsk-search"

    manager.shutdown()


def test_status_discovers_daemon_tab_groups(daemon, tmp_path):
    daemon.tabs["node-tab"] = {
        "user_id": "default",
        "session_key": "random-node-uuid",
        "url": "https://node.example/",
    }
    daemon.tabs["dsk-tab"] = {
        "user_id": "default",
        "session_key": "dsk-work",
        "url": "https://work.example/",
    }
    manager = _manager(tmp_path, daemon)

    status = manager.status()

    assert status["mode"] == "camofox"
    assert status["server_url"] == daemon.base_url
    assert status["daemon"]["ok"] is True
    sessions = {item["session"]: item for item in status["sessions"]}
    assert "work" in sessions
    assert sessions["work"]["url"] == "https://work.example/"
    assert sessions["work"]["discovered"] is True
    assert "random-node-uuid" not in sessions

    manager.shutdown()


def test_close_all_only_closes_prefixed_groups(daemon, tmp_path):
    daemon.tabs["node-tab"] = {
        "user_id": "default",
        "session_key": "random-node-uuid",
        "url": "https://node.example/",
    }
    daemon.tabs["dsk-tab"] = {
        "user_id": "default",
        "session_key": "dsk-work",
        "url": "https://work.example/",
    }
    manager = _manager(tmp_path, daemon)

    result = manager.close_all()

    assert result["ok"] is True
    assert list(daemon.tabs) == ["node-tab"]
    manager.shutdown()


def test_adopts_an_existing_tab_from_a_previous_run(daemon, tmp_path):
    daemon.tabs["existing"] = {
        "user_id": "default",
        "session_key": "dsk-work",
        "url": "https://kept.example/",
    }
    daemon.url = "https://kept.example/"
    daemon.title = "Kept"
    manager = _manager(tmp_path, daemon)

    result = manager.read(session="work")

    assert result["url"] == "https://kept.example/"
    assert result["title"] == "Kept"
    assert "existing" in daemon.tabs
    assert not any(
        method == "POST" and path == "/tabs"
        for method, path, _ in daemon.calls
    )
    manager.shutdown()


def test_unreachable_daemon_reports_a_clear_error(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    manager = CamofoxServerManager(
        _settings(
            tmp_path,
            camoufox_mode="camofox",
            camoufox_server_url=f"http://127.0.0.1:{port}",
        )
    )

    with pytest.raises(CamoufoxError, match="Could not reach camofox-browser"):
        manager.open("https://example.com")

    status = manager.status()
    assert status["ok"] is True
    assert status["daemon"]["ok"] is False
    assert "Could not reach" in status["daemon"]["error"]
    manager.shutdown()