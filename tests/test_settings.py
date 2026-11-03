from __future__ import annotations

from custom_agent_mcp.settings import load_settings


def test_load_settings_defaults(monkeypatch):
    for name in (
        "PYFIND_MCP_TEMPLATES_DIR",
        "PYFIND_MCP_OUTPUT_DIR",
        "PYFIND_MCP_ALLOW_INPUT",
        "PYFIND_MCP_MAX_WORKERS",
        "PYFIND_MCP_THRESHOLD",
        "PYFIND_MCP_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.allow_input is False
    assert settings.max_workers == 2
    assert settings.threshold == 0.8
    assert settings.log_level == "WARNING"


def test_load_settings_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("PYFIND_MCP_TEMPLATES_DIR", str(tmp_path / "templates"))
    monkeypatch.setenv("PYFIND_MCP_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PYFIND_MCP_ALLOW_INPUT", "true")
    monkeypatch.setenv("PYFIND_MCP_MAX_WORKERS", "4")
    monkeypatch.setenv("PYFIND_MCP_THRESHOLD", "0.65")
    monkeypatch.setenv("PYFIND_MCP_LOG_LEVEL", "debug")

    settings = load_settings()

    assert settings.templates_dir == tmp_path / "templates"
    assert settings.output_dir == tmp_path / "out"
    assert settings.allow_input is True
    assert settings.max_workers == 4
    assert settings.threshold == 0.65
    assert settings.log_level == "DEBUG"