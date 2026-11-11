"""Tavily Search API client.

The client uses only the standard library. Tavily needs an API key, which is
read from ``TAVILY_API_KEY`` or from a local ``.env`` file on each call so a key
added after the MCP server started is picked up without a restart.

Docs: https://docs.tavily.com/documentation/api-reference/endpoint/search
"""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from .exceptions import TavilyError

DEFAULT_TAVILY_URL = "https://api.tavily.com"
DEFAULT_TIMEOUT = 15.0
DEFAULT_SEARCH_DEPTH = "basic"
MAX_TIMEOUT = 120.0
MAX_SEARCH_RESULTS = 20
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
SEARCH_DEPTHS = frozenset({"basic", "advanced", "fast", "ultra-fast"})
TIME_RANGES = frozenset({"day", "week", "month", "year"})
_ENV_FILE_NAMES = ("TAVILY_MCP_ENV_FILE",)
_DEFAULT_ENV_FILE = ".env"


def read_env_file(path: Path) -> dict[str, str]:
    """Read simple ``KEY=value`` pairs from an env file."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        name, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[name.strip()] = value
    return values


def load_api_key(env_file: str | Path | None = None) -> str:
    """Return the Tavily key from the environment or a local env file."""
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if key:
        return key

    configured = env_file or next(
        (os.getenv(name) for name in _ENV_FILE_NAMES if os.getenv(name)), ""
    )
    path = (
        Path(configured).expanduser()
        if configured
        else Path.cwd() / _DEFAULT_ENV_FILE
    )
    values = read_env_file(path)
    return (values.get("TAVILY_API_KEY") or "").strip()


def normalize_base_url(raw: str) -> str:
    """Return a clean Tavily base URL without a trailing ``/search``."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("Tavily URL must not be empty")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Tavily URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("Tavily URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError("Tavily URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/search"):
        path = path[: -len("/search")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number")
    value = float(timeout)
    if not 0 < value <= MAX_TIMEOUT:
        raise ValueError(
            f"timeout must be greater than 0 and at most {MAX_TIMEOUT:g} seconds"
        )
    return value


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(4096)
    except Exception:
        return ""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 400:
        text = text[:400] + "..."
    return text


def _api_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict):
        message = detail.get("error") or detail.get("message")
        return str(message) if message else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                message = item.get("msg") or item.get("message")
                if message:
                    parts.append(str(message))
            elif item:
                parts.append(str(item))
        return "; ".join(parts) or None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("error")
        return str(message) if message else None
    if error:
        return str(error)
    return None


def _normalize_result(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or "").strip()
    if not url:
        return None
    normalized = {
        "title": str(item.get("title") or "").strip(),
        "url": url,
        "snippet": str(item.get("content") or "").strip(),
        "engine": "tavily",
        "engines": ["tavily"],
        "score": item.get("score"),
        "category": item.get("topic"),
        "published_date": item.get("published_date"),
        "thumbnail": item.get("favicon"),
    }
    raw_content = item.get("raw_content")
    if isinstance(raw_content, str) and raw_content.strip():
        normalized["raw_content"] = raw_content
    return normalized


class TavilyClient:
    """Small Tavily Search API client (standard library only)."""

    def __init__(
        self,
        base_url: str = DEFAULT_TAVILY_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str | None = None,
        env_file: str | Path | None = None,
        search_depth: str = DEFAULT_SEARCH_DEPTH,
        include_answer: bool = False,
        include_raw_content: bool = False,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = _validate_timeout(timeout)
        self.api_key = (
            api_key if api_key is not None else load_api_key(env_file)
        ).strip()
        depth = (search_depth or DEFAULT_SEARCH_DEPTH).strip().lower()
        if depth not in SEARCH_DEPTHS:
            raise ValueError(
                "search_depth must be one of: " + ", ".join(sorted(SEARCH_DEPTHS))
            )
        self.search_depth = depth
        self.include_answer = bool(include_answer)
        self.include_raw_content = bool(include_raw_content)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def search_url(self) -> str:
        return urllib.parse.urljoin(self.base_url + "/", "search")

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        pageno: int = 1,
        time_range: str | None = None,
        safesearch: int = 0,
        language: str = "auto",
        include_domains: Sequence[str] | None = None,
        exclude_domains: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Query Tavily Search and return SearXNG-shaped results."""
        if not self.api_key:
            raise TavilyError(
                "Tavily is not configured; set TAVILY_API_KEY or add it to .env."
            )

        text_query = (query or "").strip()
        if not text_query:
            raise ValueError("query must not be empty")
        if len(text_query) > 2000:
            raise ValueError("query must be at most 2000 characters")
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise ValueError("max_results must be an integer")
        if not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(
                f"max_results must be between 1 and {MAX_SEARCH_RESULTS}"
            )
        if pageno != 1:
            raise TavilyError("Tavily does not support pagination; use pageno=1.")
        if time_range is not None and time_range not in TIME_RANGES:
            raise ValueError(
                "time_range must be one of: " + ", ".join(sorted(TIME_RANGES))
            )
        if safesearch not in {0, 1, 2}:
            raise ValueError("safesearch must be 0, 1, or 2")

        payload: dict[str, Any] = {
            "query": text_query,
            "search_depth": self.search_depth,
            "max_results": max_results,
            "include_answer": self.include_answer,
            "include_raw_content": self.include_raw_content,
            "safe_search": bool(safesearch),
        }
        if time_range:
            payload["time_range"] = time_range
        language = (language or "auto").strip()
        if language and language.lower() != "auto":
            payload["language"] = language
        include_domains = _clean_domains(include_domains, "include_domains")
        exclude_domains = _clean_domains(exclude_domains, "exclude_domains")
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.search_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_DOWNLOAD_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = _error_body(exc)
            message = f"Tavily HTTP {exc.code} {exc.reason} for {self.search_url}"
            if detail:
                message += f": {detail}"
            if exc.code == 401:
                message += " Check TAVILY_API_KEY."
            elif exc.code == 429:
                message += " Tavily is rate limiting requests; wait and retry."
            elif exc.code in {432, 433, 434}:
                message += " The Tavily plan or credit limit blocked the request."
            raise TavilyError(message) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            raise TavilyError(
                f"Could not reach Tavily at {self.search_url}: {reason}"
            ) from exc

        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise TavilyError("Tavily response exceeded 8 MiB.")

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TavilyError(f"Tavily returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise TavilyError("Tavily returned an unexpected JSON payload.")
        api_error = _api_error(data)
        if api_error:
            raise TavilyError(f"Tavily error: {api_error}")

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raw_results = []
        results = []
        for item in raw_results[:max_results]:
            normalized = _normalize_result(item)
            if normalized is not None:
                results.append(normalized)

        answer = data.get("answer")
        answers = (
            [{"answer": str(answer).strip(), "url": None}]
            if isinstance(answer, str) and answer.strip()
            else []
        )

        payload_out = {
            "ok": True,
            "query": text_query,
            "provider": "tavily",
            "instance": self.base_url,
            "page": 1,
            "result_count": len(results),
            "number_of_results": None,
            "results": results,
            "answers": answers,
            "infoboxes": [],
            "suggestions": [],
            "corrections": [],
            "unresponsive_engines": [],
            "response_time": data.get("response_time"),
        }
        if isinstance(data.get("request_id"), str):
            payload_out["request_id"] = data["request_id"]
        return payload_out


def _clean_domains(
    values: Sequence[str] | None, name: str
) -> list[str] | None:
    if values is None:
        return None
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{name} must not contain empty strings")
        if len(text) > 253:
            raise ValueError(f"{name} entries must be at most 253 characters")
        cleaned.append(text)
    if len(cleaned) > 20:
        raise ValueError(f"{name} supports at most 20 entries")
    return cleaned or None