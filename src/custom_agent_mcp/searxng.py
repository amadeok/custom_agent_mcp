"""SearXNG web search and readable page fetching.

The client uses only the standard library so the SearXNG capability adds no
runtime dependency. SearXNG must expose its JSON API; add ``json`` to
``search.formats`` in the instance's ``settings.yml``.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence

from loguru import logger

from .exceptions import SearxngError

DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
DEFAULT_USER_AGENT = "custom-agent-mcp/0.1 (SearXNG client)"
DEFAULT_TIMEOUT = 15.0
DEFAULT_START_TIMEOUT = 90.0
MAX_TIMEOUT = 120.0
MAX_SEARCH_RESULTS = 50
DEFAULT_PAGE_CHARS = 20_000
MAX_PAGE_CHARS = 200_000
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024

TIME_RANGES = frozenset({"day", "week", "month", "year"})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_JSON_TYPES = frozenset({"application/json", "application/ld+json"})
_TEXT_TYPES = frozenset(
    {
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "tr",
        "ul",
    }
)
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.IGNORECASE
)
_WHITESPACE = re.compile(r"[ \t\f\v]+")


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number")
    value = float(timeout)
    if not 0 < value <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be greater than 0 and at most {MAX_TIMEOUT:g} seconds")
    return value


def _validate_max_chars(max_chars: int) -> int:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError("max_chars must be an integer")
    if not 1 <= max_chars <= MAX_PAGE_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}")
    return max_chars


def normalize_base_url(raw: str) -> str:
    """Return a clean SearXNG base URL without a trailing slash or /search."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("SearXNG instance URL must not be empty")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("SearXNG instance URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("SearXNG instance URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError("SearXNG instance URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/search"):
        path = path[: -len("/search")]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_page_url(raw: str) -> str:
    """Validate an HTTP(S) page URL for ``fetch_page``."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("url must not be empty")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("url must include a host")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )


@dataclass(frozen=True)
class _HttpResponse:
    url: str
    status: int
    content_type: str
    charset: str | None
    body: bytes
    truncated: bool


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


def _http_get(
    url: str,
    *,
    timeout: float,
    user_agent: str,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> _HttpResponse:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/json;q=0.9,"
                "text/plain;q=0.8,*/*;q=0.5"
            ),
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            if urllib.parse.urlsplit(final_url).scheme not in {"http", "https"}:
                raise SearxngError(
                    f"Refusing to follow a redirect to non-HTTP URL {final_url!r}."
                )
            body = response.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            if truncated:
                body = body[:max_bytes]
            return _HttpResponse(
                url=final_url,
                status=int(getattr(response, "status", 200) or 200),
                content_type=(response.headers.get_content_type() or "").lower(),
                charset=response.headers.get_content_charset(),
                body=body,
                truncated=truncated,
            )
    except urllib.error.HTTPError as exc:
        detail = _error_body(exc)
        message = f"HTTP {exc.code} {exc.reason} for {url}"
        if detail:
            message += f": {detail}"
        if exc.code == 403:
            message += (
                " If this is SearXNG, enable JSON output by adding 'json' to "
                "search.formats in settings.yml, and check the limiter."
            )
        elif exc.code == 429:
            message += " The instance is rate limiting requests; wait and retry."
        logger.debug("HTTP request failed: {}", message)
        raise SearxngError(message) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise SearxngError(f"Could not reach {url}: {reason}") from exc


def _decode(body: bytes, charset: str | None) -> tuple[str, str]:
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    match = _META_CHARSET.search(body[:4096])
    if match:
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    candidates.extend(["utf-8", "cp1252"])
    seen: set[str] = set()
    for candidate in candidates:
        encoded = candidate.strip()
        if not encoded or encoded.lower() in seen:
            continue
        seen.add(encoded.lower())
        try:
            return body.decode(encoded), encoded
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8"


class _HtmlToText(HTMLParser):
    """Small, dependency-free HTML to readable-text converter."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.description: str | None = None
        self._parts: list[str] = []
        self._tail = ""
        self._skip_depth = 0
        self._in_title = False
        self._links: list[tuple[int, str]] = []

    def _append(self, value: str) -> None:
        if value:
            self._parts.append(value)
            self._tail = (self._tail + value)[-2:]

    def _link_href(self, raw: str) -> str | None:
        href = raw.strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            return None
        absolute = urllib.parse.urljoin(self.base_url, href)
        scheme = urllib.parse.urlsplit(absolute).scheme.lower()
        if scheme not in {"http", "https", "mailto"}:
            return None
        return absolute

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value or "" for name, value in attrs}

        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in {"description", "og:description"} and self.description is None:
                self.description = attributes.get("content", "").strip() or None
        elif tag == "a":
            href = self._link_href(attributes.get("href", ""))
            if href:
                self._links.append((len(self._parts), href))
        elif tag in _HEADINGS:
            self._append("\n" + "#" * _HEADINGS[tag] + " ")
        elif tag == "li":
            self._append("\n- ")
        elif tag in {"td", "th"}:
            if self._tail and not self._tail.endswith("\n"):
                self._append(" | ")
            else:
                self._append("| ")
        elif tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "title":
            self._in_title = False
        elif tag == "a":
            if self._links:
                index, href = self._links.pop()
                text = "".join(self._parts[index:]).strip()
                if text and text != href:
                    del self._parts[index:]
                    self._parts.append(f"[{text}]({href})")
                    self._tail = self._parts[-1][-2:]
        elif tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self._append(data)

    def text(self) -> str:
        raw = "".join(self._parts).replace("\xa0", " ")
        lines = []
        for line in raw.splitlines():
            cleaned = _WHITESPACE.sub(" ", line).strip()
            if cleaned:
                lines.append(cleaned)
            elif lines and lines[-1] != "":
                lines.append("")
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def title(self) -> str | None:
        title = re.sub(r"\s+", " ", "".join(self.title_parts)).strip()
        return title or None


def html_to_text(html: str, base_url: str) -> tuple[str | None, str | None, str]:
    """Convert HTML to readable text with inline Markdown links."""
    parser = _HtmlToText(base_url)
    parser.feed(html)
    parser.close()
    return parser.title(), parser.description, parser.text()


def _pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


def _infer_content_type(body: bytes) -> str:
    head = body[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return "text/html"
    if head.startswith((b"{", b"[")):
        return "application/json"
    return "text/plain"


def _is_text_content_type(content_type: str) -> bool:
    return (
        content_type.startswith("text/")
        or content_type in _HTML_TYPES
        or content_type in _JSON_TYPES
        or content_type in _TEXT_TYPES
    )


def fetch_page(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_chars: int = DEFAULT_PAGE_CHARS,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    """Download an HTTP(S) page and return readable text.

    HTML is converted to plain text with inline Markdown links. JSON is
    pretty-printed; plain text and XML are returned as-is. Binary content is
    rejected. The text is truncated to ``max_chars`` characters.
    """
    target = normalize_page_url(url)
    timeout = _validate_timeout(timeout)
    max_chars = _validate_max_chars(max_chars)
    if not (user_agent or "").strip():
        raise ValueError("user_agent must not be empty")

    response = _http_get(
        target,
        timeout=timeout,
        user_agent=user_agent.strip(),
    )
    text, charset = _decode(response.body, response.charset)
    content_type = response.content_type or _infer_content_type(response.body)

    if content_type in _HTML_TYPES:
        title, description, text = html_to_text(text, response.url)
    elif content_type in _JSON_TYPES:
        title, description = None, None
        text = _pretty_json(text)
    elif content_type.startswith("text/") or content_type in _TEXT_TYPES:
        title, description = None, None
    else:
        raise SearxngError(
            f"Unsupported content type {content_type!r} at {response.url}. "
            "Only HTML, JSON, XML, and plain text pages can be fetched."
        )

    char_count = len(text)
    truncated = char_count > max_chars
    if truncated:
        text = text[:max_chars]

    return {
        "ok": True,
        "url": target,
        "final_url": response.url,
        "status": response.status,
        "content_type": content_type,
        "charset": charset,
        "title": title,
        "description": description,
        "text": text,
        "char_count": char_count,
        "truncated": truncated,
        "download_truncated": response.truncated,
    }


def _clean_terms(values: Sequence[str] | None, name: str) -> list[str] | None:
    if values is None:
        return None
    cleaned = []
    for value in values:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{name} must not contain empty strings")
        if len(text) > 100:
            raise ValueError(f"{name} entries must be at most 100 characters")
        cleaned.append(text)
    if len(cleaned) > 20:
        raise ValueError(f"{name} supports at most 20 entries")
    return cleaned or None


def _answer_items(value: Any) -> list[dict[str, Any]]:
    output = []
    if not isinstance(value, list):
        return output
    for item in value:
        if isinstance(item, Mapping):
            output.append(
                {
                    "answer": str(item.get("answer") or "").strip(),
                    "url": item.get("url"),
                }
            )
        else:
            output.append({"answer": str(item).strip(), "url": None})
    return output


def _infobox_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)][:10]


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _unresponsive_items(value: Any) -> list[list[str]]:
    output = []
    if not isinstance(value, list):
        return output
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            output.append([str(item[0]), str(item[1])])
    return output


def _result_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    engines = item.get("engines")
    engines = [str(engine) for engine in engines] if isinstance(engines, list) else []
    engine = item.get("engine")
    if not engine and engines:
        engine = ", ".join(engines)
    return {
        "title": str(item.get("title") or "").strip(),
        "url": str(item.get("url") or "").strip(),
        "snippet": str(item.get("content") or "").strip(),
        "engine": str(engine).strip() if engine else None,
        "engines": engines,
        "score": item.get("score"),
        "category": item.get("category"),
        "published_date": item.get("publishedDate") or item.get("published_date"),
        "thumbnail": item.get("thumbnail"),
    }


class SearxngClient:
    """Minimal SearXNG JSON API client."""

    def __init__(
        self,
        base_url: str = DEFAULT_SEARXNG_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = _validate_timeout(timeout)
        self.user_agent = (user_agent or "").strip()
        if not self.user_agent:
            raise ValueError("user_agent must not be empty")

    @property
    def search_url(self) -> str:
        return urllib.parse.urljoin(self.base_url + "/", "search")

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        categories: Sequence[str] | None = None,
        engines: Sequence[str] | None = None,
        language: str = "auto",
        pageno: int = 1,
        time_range: str | None = None,
        safesearch: int = 0,
    ) -> dict[str, Any]:
        """Query the SearXNG JSON API and return normalized results."""
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
        if safesearch not in {0, 1, 2}:
            raise ValueError("safesearch must be 0, 1, or 2")
        if time_range is not None and time_range not in TIME_RANGES:
            raise ValueError(
                "time_range must be one of: " + ", ".join(sorted(TIME_RANGES))
            )
        language = (language or "auto").strip() or "auto"
        if len(language) > 20:
            raise ValueError("language must be at most 20 characters")
        category_list = _clean_terms(categories, "categories")
        engine_list = _clean_terms(engines, "engines")

        params: list[tuple[str, str]] = [
            ("q", text_query),
            ("format", "json"),
            ("pageno", str(pageno)),
            ("safesearch", str(safesearch)),
            ("language", language),
        ]
        if category_list:
            params.append(("categories", ",".join(category_list)))
        if engine_list:
            params.append(("engines", ",".join(engine_list)))
        if time_range:
            params.append(("time_range", time_range))
        url = f"{self.search_url}?{urllib.parse.urlencode(params)}"

        try:
            response = _http_get(
                url,
                timeout=self.timeout,
                user_agent=self.user_agent,
            )
        except SearxngError as exc:
            message = str(exc)
            if message.startswith("Could not reach"):
                raise SearxngError(
                    f"{message} Is SearXNG running on {self.base_url}? Set "
                    "SEARXNG_MCP_BASE_URL to the instance's real address."
                ) from exc
            raise
        if response.truncated:
            raise SearxngError(
                f"SearXNG search response exceeded {MAX_DOWNLOAD_BYTES} bytes."
            )
        text, _charset = _decode(response.body, response.charset)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            hint = ""
            if "html" in response.content_type or text.lstrip().startswith("<"):
                hint = (
                    " The instance returned HTML instead of JSON; add 'json' to "
                    "search.formats in its settings.yml."
                )
            raise SearxngError(
                f"SearXNG returned invalid JSON from {self.search_url}: {exc}.{hint}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise SearxngError(
                f"SearXNG returned an unexpected JSON payload from {self.search_url}."
            )
        if payload.get("error"):
            raise SearxngError(f"SearXNG error: {payload['error']}")

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raw_results = []
        results = [
            normalized
            for item in raw_results[:max_results]
            if (normalized := _result_item(item)) is not None
        ]

        return {
            "ok": True,
            "query": text_query,
            "instance": self.base_url,
            "page": pageno,
            "result_count": len(results),
            "number_of_results": payload.get("number_of_results"),
            "results": results,
            "answers": _answer_items(payload.get("answers")),
            "infoboxes": _infobox_items(payload.get("infoboxes")),
            "suggestions": _string_items(payload.get("suggestions")),
            "corrections": _string_items(payload.get("corrections")),
            "unresponsive_engines": _unresponsive_items(
                payload.get("unresponsive_engines")
            ),
        }


# --------------------------------------------------------------------------- #
# Starting a local instance
# --------------------------------------------------------------------------- #
_CREATE_NO_WINDOW = 0x08000000
_START_LOCK = threading.Lock()


def _is_listening(base_url: str, timeout: float = 2.0) -> bool:
    """Return True when the instance's host and port accept a TCP connection."""
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_until_listening(base_url: str, wait: float) -> bool:
    deadline = time.monotonic() + wait
    while True:
        if _is_listening(base_url):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _start_result(
    base_url: str,
    *,
    already_running: bool,
    started: bool,
    returncode: int | None,
    output: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "url": base_url,
        "already_running": already_running,
        "started": started,
        "returncode": returncode,
        "output": output,
    }


def start_instance(
    base_url: str,
    start_cmd: str,
    *,
    timeout: float = DEFAULT_START_TIMEOUT,
    wait: float = 15.0,
) -> dict[str, Any]:
    """Run a launcher script that starts the SearXNG instance.

    The launcher is expected to be idempotent, like the ``start-searxng.cmd``
    shipped with the Windows installer: it exits 0 when the instance is already
    up. Success is decided by the port accepting connections, never by the exit
    code alone, because an empty or broken launcher also exits 0.
    """
    base = normalize_base_url(base_url)
    command = (start_cmd or "").strip()
    if not command:
        raise ValueError("SEARXNG_MCP_START_CMD is not set")
    timeout = _validate_timeout(timeout)

    if _is_listening(base):
        return _start_result(
            base, already_running=True, started=False, returncode=None, output=""
        )

    with _START_LOCK:
        # Another caller may have started it while this one waited for the lock.
        if _is_listening(base):
            return _start_result(
                base, already_running=True, started=False, returncode=None, output=""
            )

        creationflags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
        # A pipe would deadlock: the launcher detaches a grandchild that keeps
        # the write end open, so reading to EOF never finishes. A temporary file
        # lets us capture output without waiting for the whole process tree.
        with tempfile.TemporaryFile(mode="w+b") as sink:
            try:
                process = subprocess.Popen(
                    ["cmd", "/c", command],
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise SearxngError(f"Could not run {command!r}: {exc}") from exc

            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise SearxngError(
                    f"Starting SearXNG with {command!r} timed out after {timeout:g}s."
                ) from exc

            sink.seek(0)
            output = sink.read().decode("utf-8", errors="replace").strip()

        if len(output) > 2000:
            output = output[:2000] + "..."

        if not _wait_until_listening(base, wait):
            raise SearxngError(
                f"{command!r} exited with code {returncode} but {base} "
                f"is not accepting connections. Output: {output or '(none)'}"
            )

        return _start_result(
            base,
            already_running=False,
            started=True,
            returncode=returncode,
            output=output,
        )
