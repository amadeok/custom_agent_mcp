from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_INPUT_GATE_MODES = {"injection", "task"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _default_templates_dir() -> Path:
    raw = os.getenv("PYFIND_MCP_TEMPLATES_DIR")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path.cwd() / "templates"


def _default_output_dir() -> Path:
    raw = os.getenv("PYFIND_MCP_OUTPUT_DIR")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path(tempfile.gettempdir()) / "opencode" / "pyfind-mcp"


@dataclass(frozen=True)
class Settings:
    templates_dir: Path
    output_dir: Path
    allow_input: bool = False
    allow_input_gate: bool = False
    input_gate_hotkey: str = "ctrl+alt+shift+f12"
    input_gate_mode: str = "injection"
    input_gate_max_ttl: float = 600.0
    beep: bool = True
    image_format: str = "png"
    webp_quality: int = 90
    webp_lossless: bool = False
    max_workers: int = 2
    threshold: float = 0.8
    default_timeout: float = 10.0
    max_timeout: float = 120.0
    preview_max_width: int = 1600
    max_output_files: int = 100
    log_level: str = "WARNING"


def _default_input_gate_hotkey() -> str:
    raw = os.getenv("PYFIND_MCP_INPUT_GATE_HOTKEY", "ctrl+alt+shift+f12")
    hotkey = raw.strip().lower()
    if hotkey in {"", "none", "off", "disabled"}:
        return ""
    return hotkey


def _default_input_gate_mode() -> str:
    raw = os.getenv("PYFIND_MCP_INPUT_GATE_MODE", "injection").strip().lower()
    if raw not in _INPUT_GATE_MODES:
        raise ValueError(
            "PYFIND_MCP_INPUT_GATE_MODE must be 'injection' or 'task', "
            f"got {raw!r}"
        )
    return raw


def _default_image_format() -> str:
    raw = os.getenv("PYFIND_MCP_IMAGE_FORMAT", "png").strip().lower() or "png"
    if raw not in {"png", "webp"}:
        raise ValueError(
            f"PYFIND_MCP_IMAGE_FORMAT must be 'png' or 'webp', got {raw!r}"
        )
    return raw


def load_settings() -> Settings:
    level = os.getenv("PYFIND_MCP_LOG_LEVEL", "WARNING").strip().upper() or "WARNING"
    if level not in _LOG_LEVELS:
        raise ValueError(f"PYFIND_MCP_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {level!r}")

    return Settings(
        templates_dir=_default_templates_dir(),
        output_dir=_default_output_dir(),
        allow_input=_env_bool("PYFIND_MCP_ALLOW_INPUT", False),
        allow_input_gate=_env_bool("PYFIND_MCP_ALLOW_INPUT_GATE", False),
        input_gate_hotkey=_default_input_gate_hotkey(),
        input_gate_mode=_default_input_gate_mode(),
        input_gate_max_ttl=_env_float("PYFIND_MCP_INPUT_GATE_MAX_TTL", 600.0, 1.0, 3600.0),
        beep=_env_bool("PYFIND_MCP_BEEP", True),
        image_format=_default_image_format(),
        webp_quality=_env_int("PYFIND_MCP_WEBP_QUALITY", 90, 1, 100),
        webp_lossless=_env_bool("PYFIND_MCP_WEBP_LOSSLESS", False),
        max_workers=_env_int("PYFIND_MCP_MAX_WORKERS", 2, 1, 32),
        threshold=_env_float("PYFIND_MCP_THRESHOLD", 0.8, 0.0, 1.0),
        default_timeout=_env_float("PYFIND_MCP_DEFAULT_TIMEOUT", 10.0, 0.0, 3600.0),
        max_timeout=_env_float("PYFIND_MCP_MAX_TIMEOUT", 120.0, 1.0, 3600.0),
        preview_max_width=_env_int("PYFIND_MCP_PREVIEW_MAX_WIDTH", 1600, 100, 10000),
        max_output_files=_env_int("PYFIND_MCP_MAX_OUTPUT_FILES", 100, 1, 10000),
        log_level=level,
    )