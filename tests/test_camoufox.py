from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from custom_agent_mcp.camoufox_browser import (
    CamoufoxBrowserManager,
    build_search_url,
    parse_proxy,
    validate_session_name,
)
from custom_agent_mcp.camoufox_remote import build_parser, launch_kwargs
from custom_agent_mcp.exceptions import CamoufoxError
from custom_agent_mcp.settings import Settings


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        camoufox_profile_dir=tmp_path / "profiles",
        **overrides,
    )


def test_build_duckduckgo_search_url_adds_filters():
    url = build_search_url(
        engine="duckduckgo",
        template="",
        query="hello world",
        language="it-IT",
        time_range="week",
        safesearch=2,
    )
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)

    assert parsed.netloc == "html.duckduckgo.com"
    assert params["q"] == ["hello world"]
    assert params["df"] == ["w"]
    assert params["kp"] == ["1"]
    assert params["kl"] == ["it-it"]


def test_build_custom_search_url_only_substitutes_query():
    url = build_search_url(
        engine="custom",
        template="https://search.local/find?q={query}&site=docs",
        query="mcp server",
        language="it-IT",
        time_range="week",
        safesearch=2,
    )

    assert url == "https://search.local/find?q=mcp+server&site=docs"


def test_build_search_url_requires_placeholder():
    with pytest.raises(ValueError, match="placeholder"):
        build_search_url(
            engine="custom",
            template="https://search.local/find",
            query="q",
        )


def test_parse_proxy_url_and_json():
    assert parse_proxy("") is None
    assert parse_proxy("proxy.local:8080") == {"server": "http://proxy.local:8080"}
    assert parse_proxy("http://user:p%40ss@proxy.local:8080") == {
        "server": "http://proxy.local:8080",
        "username": "user",
        "password": "p@ss",
    }
    assert parse_proxy('{"server": "socks5://proxy:1080", "username": "u"}') == {
        "server": "socks5://proxy:1080",
        "username": "u",
    }


@pytest.mark.parametrize(
    "value",
    ["not a proxy", "ftp://proxy:21", "http://proxy-only"],
)
def test_parse_proxy_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_proxy(value)


def test_validate_session_name():
    assert validate_session_name("default") == "default"
    assert validate_session_name("agent-1.work") == "agent-1.work"
    for value in ("", "../escape", "a/b", "a b", "x" * 41):
        with pytest.raises(ValueError):
            validate_session_name(value)


def test_status_starts_no_browser(tmp_path):
    manager = CamoufoxBrowserManager(_settings(tmp_path))

    result = manager.status()

    assert result["ok"] is True
    assert result["mode"] == "local"
    assert result["session_count"] == 0
    assert result["sessions"] == []
    manager.shutdown()


def test_local_mode_reports_missing_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "custom_agent_mcp.camoufox_browser.importlib.util.find_spec",
        lambda name: None,
    )
    manager = CamoufoxBrowserManager(_settings(tmp_path))

    available, reason = manager.availability()
    assert available is False
    assert "Camoufox is not installed" in reason

    with pytest.raises(CamoufoxError, match="Camoufox is not installed"):
        manager.search("test")
    manager.shutdown()


def test_remote_mode_requires_endpoint(tmp_path):
    manager = CamoufoxBrowserManager(
        _settings(tmp_path, camoufox_mode="remote", camoufox_remote_url="")
    )

    available, reason = manager.availability()
    assert available is False
    assert "CAMOUFOX_MCP_REMOTE_URL" in reason
    manager.shutdown()


def test_remote_host_parser_builds_launch_kwargs():
    args = build_parser().parse_args(
        [
            "--host",
            "0.0.0.0",
            "--port",
            "9333",
            "--ws-path",
            "dsk",
            "--headed",
            "--no-humanize",
            "--proxy",
            "http://u:p@proxy.local:8080",
            "--geoip",
            "203.0.113.10",
        ]
    )

    kwargs = launch_kwargs(args)

    assert kwargs["headless"] is False
    assert kwargs["humanize"] is False
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9333
    assert kwargs["ws_path"] == "dsk"
    assert kwargs["proxy"] == {
        "server": "http://proxy.local:8080",
        "username": "u",
        "password": "p",
    }
    assert kwargs["geoip"] == "203.0.113.10"