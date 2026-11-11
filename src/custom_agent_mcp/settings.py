from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .camoufox_browser import (
    DEFAULT_LOCALE,
    DEFAULT_SEARCH_ENGINE,
    DEFAULT_SEARCH_SESSION,
    DEFAULT_SETTLE_MS,
    DEFAULT_TIMEOUT as DEFAULT_CAMOUFOX_TIMEOUT,
)
from .searxng import (
    DEFAULT_SEARXNG_URL,
    DEFAULT_START_TIMEOUT,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
)
from .tavily import (
    DEFAULT_SEARCH_DEPTH,
    DEFAULT_TAVILY_URL,
    DEFAULT_TIMEOUT as DEFAULT_TAVILY_TIMEOUT,
    SEARCH_DEPTHS,
)
from .tinyfish import DEFAULT_TINYFISH_URL

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
ALL_SEARCH_PROVIDERS = ("searxng", "tavily", "camoufox", "tinyfish")
DEFAULT_SEARCH_PROVIDERS = ("searxng", "tavily", "tinyfish", "camoufox")
TOOL_MODES = ("combined", "separate", "both")
SEARCH_STRATEGIES = ("first", "merge")
FETCH_RENDER_MODES = ("auto", "always", "never")
CAMOUFOX_MODES = ("local", "remote")
CAMOUFOX_SYSTEMS = ("windows", "macos", "linux")


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


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    raw = (os.getenv(name) or "").strip().lower() or default
    if raw not in choices:
        raise ValueError(f"{name} must be one of {list(choices)}, got {raw!r}")
    return raw


def _env_search_providers() -> tuple[str, ...]:
    raw = (os.getenv("DYNAMIC_SEARCH_PROVIDERS") or "").strip()
    values = raw.split(",") if raw else list(DEFAULT_SEARCH_PROVIDERS)
    providers: list[str] = []
    for value in values:
        name = value.strip().lower()
        if not name:
            continue
        if name not in ALL_SEARCH_PROVIDERS:
            raise ValueError(
                f"DYNAMIC_SEARCH_PROVIDERS contains unknown provider {name!r}; "
                f"use {', '.join(ALL_SEARCH_PROVIDERS)}"
            )
        if name in providers:
            raise ValueError(
                f"DYNAMIC_SEARCH_PROVIDERS lists {name!r} more than once"
            )
        providers.append(name)
    if not providers:
        raise ValueError("DYNAMIC_SEARCH_PROVIDERS must contain at least one provider")
    return tuple(providers)


def _default_camoufox_profile_dir() -> Path:
    raw = (os.getenv("CAMOUFOX_MCP_PROFILE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    base = (
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_DATA_HOME")
        or str(Path.home() / ".local" / "share")
    )
    return Path(base) / "dynamic-search-kit" / "camoufox"


def _default_camoufox_geoip() -> bool | str:
    raw = (os.getenv("CAMOUFOX_MCP_GEOIP") or "false").strip()
    lower = raw.lower()
    if lower in {"", "0", "false", "no", "off"}:
        return False
    if lower in {"1", "true", "yes", "on"}:
        return True
    return raw


def _default_camoufox_os() -> str:
    raw = (os.getenv("CAMOUFOX_MCP_OS") or "windows").strip().lower()
    if raw in {"", "auto"}:
        return ""
    if raw not in CAMOUFOX_SYSTEMS:
        raise ValueError(
            f"CAMOUFOX_MCP_OS must be one of {list(CAMOUFOX_SYSTEMS)} or auto, "
            f"got {raw!r}"
        )
    return raw


def _default_camoufox_engine() -> str:
    raw = (os.getenv("CAMOUFOX_MCP_SEARCH_ENGINE") or DEFAULT_SEARCH_ENGINE).strip().lower()
    if raw not in {*("duckduckgo", "bing", "google"), "custom"}:
        raise ValueError(
            "CAMOUFOX_MCP_SEARCH_ENGINE must be duckduckgo, bing, google, or custom"
        )
    if raw == "custom" and not (os.getenv("CAMOUFOX_MCP_SEARCH_URL") or "").strip():
        raise ValueError(
            "CAMOUFOX_MCP_SEARCH_ENGINE=custom requires CAMOUFOX_MCP_SEARCH_URL"
        )
    return raw


@dataclass(frozen=True)
class Settings:
    templates_dir: Path
    output_dir: Path
    allow_input: bool = False
    allow_input_gate: bool = False
    input_gate_hotkey: str = "ctrl+alt+shift+f12"
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
    tool_mode: str = "combined"
    search_providers: tuple[str, ...] = DEFAULT_SEARCH_PROVIDERS
    search_strategy: str = "first"
    fetch_max_chars: int = 20_000
    fetch_render: str = "auto"
    fetch_min_chars: int = 300
    searxng_base_url: str = DEFAULT_SEARXNG_URL
    searxng_timeout: float = DEFAULT_TIMEOUT
    searxng_user_agent: str = DEFAULT_USER_AGENT
    searxng_start_cmd: str = ""
    searxng_start_timeout: float = DEFAULT_START_TIMEOUT
    tavily_base_url: str = DEFAULT_TAVILY_URL
    tavily_timeout: float = DEFAULT_TAVILY_TIMEOUT
    tavily_env_file: str = ""
    tavily_search_depth: str = DEFAULT_SEARCH_DEPTH
    tavily_include_answer: bool = False
    tavily_include_raw_content: bool = False
    camoufox_mode: str = "local"
    camoufox_remote_url: str = ""
    camoufox_headless: bool = True
    camoufox_humanize: bool = True
    camoufox_profile_dir: Path = field(default_factory=_default_camoufox_profile_dir)
    camoufox_locale: str = DEFAULT_LOCALE
    camoufox_os: str = "windows"
    camoufox_proxy: str = ""
    camoufox_geoip: bool | str = False
    camoufox_timeout: float = DEFAULT_CAMOUFOX_TIMEOUT
    camoufox_settle_ms: int = DEFAULT_SETTLE_MS
    camoufox_search_engine: str = DEFAULT_SEARCH_ENGINE
    camoufox_search_url: str = ""
    camoufox_search_session: str = DEFAULT_SEARCH_SESSION
    camoufox_fetch_session: str = "fetch"
    tinyfish_base_url: str = DEFAULT_TINYFISH_URL
    tinyfish_timeout: float = DEFAULT_TIMEOUT
    tinyfish_env_file: str = ""

    def __post_init__(self) -> None:
        if self.tool_mode not in TOOL_MODES:
            raise ValueError(f"tool_mode must be one of {list(TOOL_MODES)}")
        if self.search_strategy not in SEARCH_STRATEGIES:
            raise ValueError(
                f"search_strategy must be one of {list(SEARCH_STRATEGIES)}"
            )
        if self.fetch_render not in FETCH_RENDER_MODES:
            raise ValueError(
                f"fetch_render must be one of {list(FETCH_RENDER_MODES)}"
            )
        if not 1 <= self.fetch_max_chars <= 200_000:
            raise ValueError("fetch_max_chars must be between 1 and 200000")
        if not 0 <= self.fetch_min_chars <= 10_000:
            raise ValueError("fetch_min_chars must be between 0 and 10000")
        if not self.search_providers:
            raise ValueError("search_providers must contain at least one provider")
        for provider in self.search_providers:
            if provider not in ALL_SEARCH_PROVIDERS:
                raise ValueError(f"unknown search provider {provider!r}")
        if self.camoufox_mode not in CAMOUFOX_MODES:
            raise ValueError(f"camoufox_mode must be one of {list(CAMOUFOX_MODES)}")
        if self.camoufox_mode == "remote" and self.camoufox_remote_url:
            scheme = self.camoufox_remote_url.split(":", 1)[0].lower()
            if scheme not in {"ws", "wss"}:
                raise ValueError("camoufox_remote_url must start with ws:// or wss://")
        if (self.camoufox_search_url or "").strip() and "{query}" not in self.camoufox_search_url:
            raise ValueError("camoufox_search_url must contain a {query} placeholder")
        if self.tavily_search_depth not in SEARCH_DEPTHS:
            raise ValueError(
                "tavily_search_depth must be one of: "
                + ", ".join(sorted(SEARCH_DEPTHS))
            )


def _default_input_gate_hotkey() -> str:
    raw = os.getenv("PYFIND_MCP_INPUT_GATE_HOTKEY", "ctrl+alt+shift+f12")
    hotkey = raw.strip().lower()
    if hotkey in {"", "none", "off", "disabled"}:
        return ""
    return hotkey


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
        tool_mode=_env_choice("DYNAMIC_SEARCH_TOOL_MODE", "combined", TOOL_MODES),
        search_providers=_env_search_providers(),
        search_strategy=_env_choice(
            "DYNAMIC_SEARCH_STRATEGY", "first", SEARCH_STRATEGIES
        ),
        fetch_max_chars=_env_int(
            "DYNAMIC_SEARCH_FETCH_MAX_CHARS", 20_000, 1, 200_000
        ),
        fetch_render=_env_choice(
            "DYNAMIC_SEARCH_FETCH_RENDER", "auto", FETCH_RENDER_MODES
        ),
        fetch_min_chars=_env_int(
            "DYNAMIC_SEARCH_FETCH_MIN_CHARS", 300, 0, 10_000
        ),
        searxng_base_url=(
            os.getenv("SEARXNG_MCP_BASE_URL", "").strip() or DEFAULT_SEARXNG_URL
        ),
        searxng_timeout=_env_float("SEARXNG_MCP_TIMEOUT", DEFAULT_TIMEOUT, 1.0, 120.0),
        searxng_user_agent=(
            os.getenv("SEARXNG_MCP_USER_AGENT", "").strip() or DEFAULT_USER_AGENT
        ),
        searxng_start_cmd=os.getenv("SEARXNG_MCP_START_CMD", "").strip(),
        searxng_start_timeout=_env_float(
            "SEARXNG_MCP_START_TIMEOUT", DEFAULT_START_TIMEOUT, 1.0, 300.0
        ),
        tavily_base_url=(
            os.getenv("TAVILY_MCP_BASE_URL", "").strip() or DEFAULT_TAVILY_URL
        ),
        tavily_timeout=_env_float(
            "TAVILY_MCP_TIMEOUT", DEFAULT_TAVILY_TIMEOUT, 1.0, 120.0
        ),
        tavily_env_file=os.getenv("TAVILY_MCP_ENV_FILE", "").strip(),
        tavily_search_depth=_env_choice(
            "TAVILY_MCP_SEARCH_DEPTH", DEFAULT_SEARCH_DEPTH, tuple(sorted(SEARCH_DEPTHS))
        ),
        tavily_include_answer=_env_bool("TAVILY_MCP_INCLUDE_ANSWER", False),
        tavily_include_raw_content=_env_bool(
            "TAVILY_MCP_INCLUDE_RAW_CONTENT", False
        ),
        camoufox_mode=_env_choice("CAMOUFOX_MCP_MODE", "local", CAMOUFOX_MODES),
        camoufox_remote_url=(
            os.getenv("CAMOUFOX_MCP_REMOTE_URL", "").strip()
        ),
        camoufox_headless=_env_bool("CAMOUFOX_MCP_HEADLESS", True),
        camoufox_humanize=_env_bool("CAMOUFOX_MCP_HUMANIZE", True),
        camoufox_profile_dir=_default_camoufox_profile_dir(),
        camoufox_locale=(
            os.getenv("CAMOUFOX_MCP_LOCALE", "").strip() or DEFAULT_LOCALE
        ),
        camoufox_os=_default_camoufox_os(),
        camoufox_proxy=os.getenv("CAMOUFOX_MCP_PROXY", "").strip(),
        camoufox_geoip=_default_camoufox_geoip(),
        camoufox_timeout=_env_float(
            "CAMOUFOX_MCP_TIMEOUT", DEFAULT_CAMOUFOX_TIMEOUT, 5.0, 300.0
        ),
        camoufox_settle_ms=_env_int(
            "CAMOUFOX_MCP_SETTLE_MS", DEFAULT_SETTLE_MS, 0, 10_000
        ),
        camoufox_search_engine=_default_camoufox_engine(),
        camoufox_search_url=(os.getenv("CAMOUFOX_MCP_SEARCH_URL") or "").strip(),
        camoufox_search_session=(
            os.getenv("CAMOUFOX_MCP_SEARCH_SESSION", "").strip()
            or DEFAULT_SEARCH_SESSION
        ),
        camoufox_fetch_session=(
            os.getenv("CAMOUFOX_MCP_FETCH_SESSION", "").strip() or "fetch"
        ),
        tinyfish_base_url=(
            os.getenv("TINYFISH_MCP_BASE_URL", "").strip() or DEFAULT_TINYFISH_URL
        ),
        tinyfish_timeout=_env_float(
            "TINYFISH_MCP_TIMEOUT", DEFAULT_TIMEOUT, 1.0, 120.0
        ),
        tinyfish_env_file=os.getenv("TINYFISH_MCP_ENV_FILE", "").strip(),
    )
