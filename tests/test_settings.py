from __future__ import annotations

from custom_agent_mcp.settings import load_settings


def test_load_settings_defaults(monkeypatch):
    for name in (
        "PYFIND_MCP_TEMPLATES_DIR",
        "PYFIND_MCP_OUTPUT_DIR",
        "PYFIND_MCP_ALLOW_INPUT",
        "PYFIND_MCP_ALLOW_INPUT_GATE",
        "PYFIND_MCP_INPUT_GATE_HOTKEY",
        "PYFIND_MCP_INPUT_GATE_MAX_TTL",
        "PYFIND_MCP_BEEP",
        "PYFIND_MCP_IMAGE_FORMAT",
        "PYFIND_MCP_WEBP_QUALITY",
        "PYFIND_MCP_WEBP_LOSSLESS",
        "PYFIND_MCP_MAX_WORKERS",
        "PYFIND_MCP_THRESHOLD",
        "PYFIND_MCP_LOG_LEVEL",
        "DYNAMIC_SEARCH_TOOL_MODE",
        "DYNAMIC_SEARCH_PROVIDERS",
        "DYNAMIC_SEARCH_STRATEGY",
        "DYNAMIC_SEARCH_FETCH_MAX_CHARS",
        "DYNAMIC_SEARCH_FETCH_RENDER",
        "DYNAMIC_SEARCH_FETCH_MIN_CHARS",
        "SEARXNG_MCP_BASE_URL",
        "SEARXNG_MCP_TIMEOUT",
        "SEARXNG_MCP_USER_AGENT",
        "SEARXNG_MCP_START_CMD",
        "SEARXNG_MCP_START_TIMEOUT",
        "TAVILY_API_KEY",
        "TAVILY_MCP_BASE_URL",
        "TAVILY_MCP_TIMEOUT",
        "TAVILY_MCP_ENV_FILE",
        "TAVILY_MCP_SEARCH_DEPTH",
        "TAVILY_MCP_INCLUDE_ANSWER",
        "TAVILY_MCP_INCLUDE_RAW_CONTENT",
        "CAMOUFOX_MCP_MODE",
        "CAMOUFOX_MCP_REMOTE_URL",
        "CAMOUFOX_MCP_HEADLESS",
        "CAMOUFOX_MCP_HUMANIZE",
        "CAMOUFOX_MCP_PROFILE_DIR",
        "CAMOUFOX_MCP_LOCALE",
        "CAMOUFOX_MCP_OS",
        "CAMOUFOX_MCP_PROXY",
        "CAMOUFOX_MCP_GEOIP",
        "CAMOUFOX_MCP_TIMEOUT",
        "CAMOUFOX_MCP_SETTLE_MS",
        "CAMOUFOX_MCP_SEARCH_ENGINE",
        "CAMOUFOX_MCP_SEARCH_URL",
        "CAMOUFOX_MCP_SEARCH_SESSION",
        "CAMOUFOX_MCP_FETCH_SESSION",
        "TINYFISH_API_KEY",
        "TINYFISH_MCP_BASE_URL",
        "TINYFISH_MCP_TIMEOUT",
        "TINYFISH_MCP_ENV_FILE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.allow_input is False
    assert settings.allow_input_gate is False
    assert settings.input_gate_hotkey == "ctrl+alt+shift+f12"
    assert not hasattr(settings, "input_gate_mode")
    assert settings.input_gate_max_ttl == 600.0
    assert settings.beep is True
    assert settings.image_format == "png"
    assert settings.webp_quality == 90
    assert settings.webp_lossless is False
    assert settings.max_workers == 2
    assert settings.threshold == 0.8
    assert settings.log_level == "WARNING"
    assert settings.tool_mode == "combined"
    assert settings.search_providers == (
        "searxng",
        "tavily",
        "tinyfish",
        "camoufox",
    )
    assert settings.search_strategy == "first"
    assert settings.fetch_max_chars == 20_000
    assert settings.fetch_render == "auto"
    assert settings.fetch_min_chars == 300
    assert settings.searxng_base_url == "http://127.0.0.1:8888"
    assert settings.searxng_timeout == 15.0
    assert settings.searxng_user_agent.startswith("custom-agent-mcp/")
    assert settings.searxng_start_cmd == ""
    assert settings.searxng_start_timeout == 90.0
    assert settings.tavily_base_url == "https://api.tavily.com"
    assert settings.tavily_timeout == 15.0
    assert settings.tavily_env_file == ""
    assert settings.tavily_search_depth == "basic"
    assert settings.tavily_include_answer is False
    assert settings.tavily_include_raw_content is False
    assert settings.camoufox_mode == "local"
    assert settings.camoufox_remote_url == ""
    assert settings.camoufox_headless is True
    assert settings.camoufox_humanize is True
    assert settings.camoufox_profile_dir.name == "camoufox"
    assert settings.camoufox_locale == "en-US"
    assert settings.camoufox_os == "windows"
    assert settings.camoufox_proxy == ""
    assert settings.camoufox_geoip is False
    assert settings.camoufox_timeout == 45.0
    assert settings.camoufox_settle_ms == 750
    assert settings.camoufox_search_engine == "duckduckgo"
    assert settings.camoufox_search_url == ""
    assert settings.camoufox_search_session == "search"
    assert settings.camoufox_fetch_session == "fetch"
    assert settings.tinyfish_base_url == "https://api.search.tinyfish.ai"
    assert settings.tinyfish_timeout == 15.0
    assert settings.tinyfish_env_file == ""


def test_load_settings_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("PYFIND_MCP_TEMPLATES_DIR", str(tmp_path / "templates"))
    monkeypatch.setenv("PYFIND_MCP_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PYFIND_MCP_ALLOW_INPUT", "true")
    monkeypatch.setenv("PYFIND_MCP_ALLOW_INPUT_GATE", "true")
    monkeypatch.setenv("PYFIND_MCP_INPUT_GATE_HOTKEY", "none")
    monkeypatch.setenv("PYFIND_MCP_INPUT_GATE_MAX_TTL", "120")
    monkeypatch.setenv("PYFIND_MCP_BEEP", "false")
    monkeypatch.setenv("PYFIND_MCP_IMAGE_FORMAT", "webp")
    monkeypatch.setenv("PYFIND_MCP_WEBP_QUALITY", "80")
    monkeypatch.setenv("PYFIND_MCP_WEBP_LOSSLESS", "true")
    monkeypatch.setenv("PYFIND_MCP_MAX_WORKERS", "4")
    monkeypatch.setenv("PYFIND_MCP_THRESHOLD", "0.65")
    monkeypatch.setenv("PYFIND_MCP_LOG_LEVEL", "debug")
    monkeypatch.setenv("DYNAMIC_SEARCH_TOOL_MODE", "separate")
    monkeypatch.setenv("DYNAMIC_SEARCH_PROVIDERS", "camoufox,searxng")
    monkeypatch.setenv("DYNAMIC_SEARCH_STRATEGY", "merge")
    monkeypatch.setenv("DYNAMIC_SEARCH_FETCH_MAX_CHARS", "9000")
    monkeypatch.setenv("DYNAMIC_SEARCH_FETCH_RENDER", "always")
    monkeypatch.setenv("DYNAMIC_SEARCH_FETCH_MIN_CHARS", "450")
    monkeypatch.setenv("SEARXNG_MCP_BASE_URL", "http://search.local:8888/searxng")
    monkeypatch.setenv("SEARXNG_MCP_TIMEOUT", "30")
    monkeypatch.setenv("SEARXNG_MCP_USER_AGENT", "test-agent/1.0")
    monkeypatch.setenv("TAVILY_MCP_BASE_URL", "http://tavily.local")
    monkeypatch.setenv("TAVILY_MCP_TIMEOUT", "20")
    monkeypatch.setenv("TAVILY_MCP_ENV_FILE", "custom.env")
    monkeypatch.setenv("TAVILY_MCP_SEARCH_DEPTH", "advanced")
    monkeypatch.setenv("TAVILY_MCP_INCLUDE_ANSWER", "true")
    monkeypatch.setenv("TAVILY_MCP_INCLUDE_RAW_CONTENT", "true")
    monkeypatch.setenv("CAMOUFOX_MCP_MODE", "remote")
    monkeypatch.setenv("CAMOUFOX_MCP_REMOTE_URL", "ws://browser.local:9222/dsk")
    monkeypatch.setenv("CAMOUFOX_MCP_HEADLESS", "false")
    monkeypatch.setenv("CAMOUFOX_MCP_HUMANIZE", "false")
    monkeypatch.setenv("CAMOUFOX_MCP_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("CAMOUFOX_MCP_LOCALE", "it-IT")
    monkeypatch.setenv("CAMOUFOX_MCP_OS", "macos")
    monkeypatch.setenv("CAMOUFOX_MCP_PROXY", "http://user:pass@proxy.local:8080")
    monkeypatch.setenv("CAMOUFOX_MCP_GEOIP", "true")
    monkeypatch.setenv("CAMOUFOX_MCP_TIMEOUT", "60")
    monkeypatch.setenv("CAMOUFOX_MCP_SETTLE_MS", "100")
    monkeypatch.setenv("CAMOUFOX_MCP_SEARCH_ENGINE", "custom")
    monkeypatch.setenv("CAMOUFOX_MCP_SEARCH_URL", "https://search.local/?q={query}")
    monkeypatch.setenv("CAMOUFOX_MCP_SEARCH_SESSION", "agent")
    monkeypatch.setenv("CAMOUFOX_MCP_FETCH_SESSION", "reader")
    monkeypatch.setenv("TINYFISH_MCP_BASE_URL", "http://tinyfish.local")
    monkeypatch.setenv("TINYFISH_MCP_TIMEOUT", "20")
    monkeypatch.setenv("TINYFISH_MCP_ENV_FILE", "custom.env")

    settings = load_settings()

    assert settings.templates_dir == tmp_path / "templates"
    assert settings.output_dir == tmp_path / "out"
    assert settings.allow_input is True
    assert settings.allow_input_gate is True
    assert settings.input_gate_hotkey == ""
    assert settings.input_gate_max_ttl == 120.0
    assert settings.beep is False
    assert settings.image_format == "webp"
    assert settings.webp_quality == 80
    assert settings.webp_lossless is True
    assert settings.max_workers == 4
    assert settings.threshold == 0.65
    assert settings.log_level == "DEBUG"
    assert settings.tool_mode == "separate"
    assert settings.search_providers == ("camoufox", "searxng")
    assert settings.search_strategy == "merge"
    assert settings.fetch_max_chars == 9000
    assert settings.fetch_render == "always"
    assert settings.fetch_min_chars == 450
    assert settings.searxng_base_url == "http://search.local:8888/searxng"
    assert settings.searxng_timeout == 30.0
    assert settings.searxng_user_agent == "test-agent/1.0"
    assert settings.tavily_base_url == "http://tavily.local"
    assert settings.tavily_timeout == 20.0
    assert settings.tavily_env_file == "custom.env"
    assert settings.tavily_search_depth == "advanced"
    assert settings.tavily_include_answer is True
    assert settings.tavily_include_raw_content is True
    assert settings.camoufox_mode == "remote"
    assert settings.camoufox_remote_url == "ws://browser.local:9222/dsk"
    assert settings.camoufox_headless is False
    assert settings.camoufox_humanize is False
    assert settings.camoufox_profile_dir == tmp_path / "profiles"
    assert settings.camoufox_locale == "it-IT"
    assert settings.camoufox_os == "macos"
    assert settings.camoufox_proxy == "http://user:pass@proxy.local:8080"
    assert settings.camoufox_geoip is True
    assert settings.camoufox_timeout == 60.0
    assert settings.camoufox_settle_ms == 100
    assert settings.camoufox_search_engine == "custom"
    assert settings.camoufox_search_url == "https://search.local/?q={query}"
    assert settings.camoufox_search_session == "agent"
    assert settings.camoufox_fetch_session == "reader"
    assert settings.tinyfish_base_url == "http://tinyfish.local"
    assert settings.tinyfish_timeout == 20.0
    assert settings.tinyfish_env_file == "custom.env"


def test_removed_gate_mode_env_is_ignored(monkeypatch):
    monkeypatch.setenv("PYFIND_MCP_INPUT_GATE_MODE", "always")

    settings = load_settings()

    assert not hasattr(settings, "input_gate_mode")


def test_invalid_provider_chain_is_rejected(monkeypatch):
    monkeypatch.setenv("DYNAMIC_SEARCH_PROVIDERS", "searxng,unknown")

    try:
        load_settings()
    except ValueError as exc:
        assert "unknown provider" in str(exc)
    else:
        raise AssertionError("invalid provider chain was accepted")


def test_duplicate_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("DYNAMIC_SEARCH_PROVIDERS", "searxng,searxng")

    try:
        load_settings()
    except ValueError as exc:
        assert "more than once" in str(exc)
    else:
        raise AssertionError("duplicate provider was accepted")