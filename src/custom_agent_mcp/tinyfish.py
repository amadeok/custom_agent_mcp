"""TinyFish Search API client.

TinyFish Search is free up to 12,000 requests per day (30 requests/minute) and
returns ranked web results as JSON. Dynamic Search Kit treats it as a normal
provider: include ``tinyfish`` in DYNAMIC_SEARCH_PROVIDERS and position it
anywhere in the chain.

Docs: https://docs.tinyfish.ai/search-api/reference
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
from typing import Any

DEFAULT_TINYFISH_URL = "https://api.search.tinyfish.ai"
DEFAULT_TIMEOUT = 15.0
MAX_TIMEOUT = 120.0
MAX_SEARCH_RESULTS = 50
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_API_PAGE = 10  # TinyFish pages are 0-based and capped at 10.

TIME_RANGES = frozenset({"day", "week", "month", "year"})
_TIME_RANGE_MINUTES = {
    "day": 24 * 60,
    "week": 7 * 24 * 60,
    "month": 30 * 24 * 60,
    "year": 365 * 24 * 60,
}
_ENV_FILE_NAMES = ("TINYFISH_MCP_ENV_FILE",)
_DEFAULT_ENV_FILE = ".env"


class TinyFishError(RuntimeError):
    """A TinyFish request or response failed."""


def _read_env_file(path: Path) -> dict[str, str]:
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
    """Return the TinyFish key from the environment or a local env file."""
    key = (os.getenv("TINYFISH_API_KEY") or "").strip()
    if key:
        return key

    configured = env_file or next(
        (os.getenv(name) for name in _ENV_FILE_NAMES if os.getenv(name)), ""
    )
    path = Path(configured).expanduser() if configured else Path.cwd() / _DEFAULT_ENV_FILE
    values = _read_env_file(path)
    return (values.get("TINYFISH_API_KEY") or "").strip()


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number")
    value = float(timeout)
    if not 0 < value <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be greater than 0 and at most {MAX_TIMEOUT:g} seconds")
    return value


def _normalize_base_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("TinyFish URL must not be empty")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("TinyFish URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("TinyFish URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError("TinyFish URL must not contain a query or fragment")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(2048)
    except Exception:
        return ""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 300:
        text = text[:300] + "..."
    return text


def _result_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or "").strip()
    if not url:
        return None
    return {
        "title": str(item.get("title") or "").strip(),
        "url": url,
        "snippet": str(item.get("snippet") or "").strip(),
        "engine": "tinyfish",
        "engines": ["tinyfish"],
        "score": item.get("position"),
        "category": None,
        "published_date": item.get("date"),
        "thumbnail": None,
    }


class TinyFishClient:
    """Minimal TinyFish Search API client (standard library only)."""

    def __init__(
        self,
        base_url: str = DEFAULT_TINYFISH_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str | None = None,
        env_file: str | Path | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.timeout = _validate_timeout(timeout)
        self.api_key = (
            api_key if api_key is not None else load_api_key(env_file)
        ).strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        pageno: int = 1,
        time_range: str | None = None,
        safesearch: int = 0,
        language: str = "auto",
    ) -> dict[str, Any]:
        """Query the TinyFish Search API and return SearXNG-shaped results."""
        if not self.api_key:
            raise TinyFishError(
                "TinyFish is not configured; set TINYFISH_API_KEY or add it to .env."
            )

        text_query = (query or "").strip()
        if not text_query:
            raise ValueError("query must not be empty")
        if len(text_query) > 2000:
            raise ValueError("query must be at most 2000 characters")
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise ValueError("max_results must be an integer")
        if not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        if isinstance(pageno, bool) or not isinstance(pageno, int):
            raise ValueError("pageno must be an integer")
        if not 1 <= pageno <= 20:
            raise ValueError("pageno must be between 1 and 20")
        if time_range is not None and time_range not in TIME_RANGES:
            raise ValueError(
                "time_range must be one of: " + ", ".join(sorted(TIME_RANGES))
            )
        if safesearch not in {0, 1, 2}:
            raise ValueError("safesearch must be 0, 1, or 2")

        page = pageno - 1
        if page > MAX_API_PAGE:
            raise TinyFishError(
                f"TinyFish supports pages 1-{MAX_API_PAGE + 1}, not {pageno}."
            )

        params: dict[str, object] = {"query": text_query, "page": page}
        if time_range in _TIME_RANGE_MINUTES:
            params["recency_minutes"] = _TIME_RANGE_MINUTES[time_range]
        if language and language.strip().lower() != "auto":
            primary = language.strip().split("-")[0].lower()
            if primary:
                params["language"] = primary
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            url,
            headers={
                "X-API-Key": self.api_key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_DOWNLOAD_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = _error_body(exc)
            message = f"TinyFish HTTP {exc.code} {exc.reason} for {self.base_url}"
            if detail:
                message += f": {detail}"
            if exc.code == 401:
                message += " Check TINYFISH_API_KEY."
            elif exc.code == 402:
                message += " The daily free allowance is exhausted (it resets at 00:00 UTC)."
            elif exc.code == 429:
                message += " TinyFish rate limit reached (30 requests/minute)."
            raise TinyFishError(message) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            raise TinyFishError(
                f"Could not reach TinyFish at {self.base_url}: {reason}"
            ) from exc

        if len(body) > MAX_DOWNLOAD_BYTES:
            raise TinyFishError("TinyFish response exceeded 5 MiB.")

        text, _charset = _decode(body)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TinyFishError(f"TinyFish returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise TinyFishError("TinyFish returned an unexpected JSON payload.")

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raw_results = []
        results = []
        for item in raw_results:
            normalized = _result_item(item)
            if normalized is not None:
                results.append(normalized)
            if len(results) >= max_results:
                break

        return {
            "ok": True,
            "query": text_query,
            "provider": "tinyfish",
            "instance": self.base_url,
            "page": pageno,
            "result_count": len(results),
            "number_of_results": payload.get("total_results"),
            "results": results,
            "answers": [],
            "infoboxes": [],
            "suggestions": [],
            "corrections": [],
            "unresponsive_engines": [],
        }


def _decode(body: bytes) -> tuple[str, str]:
    try:
        return body.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace"), "utf-8"