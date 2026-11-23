"""Tests for the shared HTTP transport: bind address, auth, and LAN URLs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from custom_agent_mcp.cli import BearerAuthMiddleware, build_parser

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _ensure_script():
    path = SCRIPTS / "ensure_websearch_server.py"
    spec = importlib.util.spec_from_file_location("ensure_websearch_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _guarded_app(token: str) -> Starlette:
    async def endpoint(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", endpoint, methods=["GET", "POST"])])
    app.add_middleware(BearerAuthMiddleware, token=token)
    return app


def test_bearer_middleware_rejects_missing_token():
    with TestClient(_guarded_app("secret")) as client:
        response = client.post("/mcp")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_middleware_rejects_wrong_token():
    with TestClient(_guarded_app("secret")) as client:
        response = client.post("/mcp", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


def test_bearer_middleware_accepts_matching_token():
    with TestClient(_guarded_app("secret")) as client:
        response = client.post("/mcp", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.text == "ok"


def test_parser_defaults_to_localhost_and_env_token(monkeypatch):
    monkeypatch.delenv("MCP_HTTP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("WEBSEARCH_MCP_AUTH_TOKEN", raising=False)
    args = build_parser("prog", "desc").parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.auth_token is None


def test_parser_reads_the_auth_token_from_the_environment(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_AUTH_TOKEN", "from-env")
    args = build_parser("prog", "desc").parse_args(
        ["--transport", "streamable-http", "--host", "0.0.0.0"]
    )

    assert args.transport == "streamable-http"
    assert args.host == "0.0.0.0"
    assert args.auth_token == "from-env"


def test_ensure_script_maps_a_wildcard_bind_to_loopback():
    ensure = _ensure_script()

    assert ensure.connect_host("0.0.0.0") == "127.0.0.1"
    assert ensure.connect_host("::") == "::1"
    assert ensure.connect_host("192.168.1.20") == "192.168.1.20"


def test_ensure_script_advertises_lan_urls_for_a_wildcard_bind(monkeypatch):
    ensure = _ensure_script()
    monkeypatch.setattr(ensure, "lan_ipv4_addresses", lambda: ["192.168.1.20"])

    urls = ensure.advertised_urls("0.0.0.0", 8765)
    assert urls[0] == "http://127.0.0.1:8765/mcp"
    assert "http://192.168.1.20:8765/mcp" in urls

    assert ensure.advertised_urls("127.0.0.1", 8765) == ["http://127.0.0.1:8765/mcp"]
