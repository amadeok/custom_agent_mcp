"""Camoufox backend that drives an existing ``camofox-browser`` daemon.

The Node ``camofox`` MCP server is a thin stdio front-end; the browser state
lives in the ``camofox-browser`` HTTP daemon on port 9377. Setting
``CAMOUFOX_MCP_MODE=camofox`` makes the Dynamic Search Kit talk to that same
daemon instead of launching its own Camoufox through Playwright. Cookies,
logins, and the anti-detect profile stay in the daemon, and every MCP session
maps to a tab group named ``<prefix>-<session>`` under a shared daemon user.

Only the standard library is used, so this mode works without the ``browser``
or ``remote`` extras. The manager exposes the same surface as
:class:`custom_agent_mcp.camoufox_browser.CamoufoxBrowserManager`, which keeps
the search orchestration and MCP tools unchanged.
"""

from __future__ import annotations

import atexit
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

from .camoufox_browser import (
    DEFAULT_SEARCH_ENGINE,
    DEFAULT_SEARCH_SESSION,
    DEFAULT_TIMEOUT,
    ENGINE_SCRIPTS,
    ENGINE_SELECTORS,
    GENERIC_SCRIPT,
    MAX_PAGE_CHARS,
    MAX_SEARCH_RESULTS,
    WAIT_UNTIL,
    build_search_url,
    jsonable_result,
    looks_challenged,
    normalize_search_items,
    validate_page_url,
    validate_session_name,
)
from .exceptions import CamoufoxError

if TYPE_CHECKING:
    from .settings import Settings

DEFAULT_CAMOFOX_URL = "http://127.0.0.1:9377"
DEFAULT_CAMOFOX_USER = "default"
DEFAULT_SESSION_PREFIX = "dsk"
DEFAULT_USER_AGENT = "custom-agent-mcp/0.2 (camofox-browser client)"
MAX_TIMEOUT = 300.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
ERROR_DETAIL_CHARS = 2000
_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_PAGE_META_EXPRESSION = (
    "({url: location.href, title: document.title, "
    "text: document.body ? document.body.innerText : ''})"
)


def normalize_server_url(raw: str) -> str:
    """Return a clean camofox-browser base URL without a trailing slash."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("camofox-browser URL must not be empty")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            "camofox-browser URL must start with http:// or https://"
        )
    if not parsed.netloc:
        raise ValueError("camofox-browser URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "camofox-browser URL must not contain a query or fragment"
        )
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def validate_session_prefix(prefix: str) -> str:
    """Validate the optional prefix used to isolate tab groups."""
    value = (prefix or "").strip()
    if value and not _PREFIX_RE.fullmatch(value):
        raise ValueError(
            "CAMOUFOX_MCP_SERVER_SESSION_PREFIX must be empty or 1-32 "
            "characters: letters, digits, dot, dash, or underscore"
        )
    return value


def build_session_key(prefix: str, session: str) -> str:
    """Map an MCP session name to a daemon tab-group key."""
    value = (prefix or "").strip()
    if not value:
        return session
    return f"{value}-{session}"


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number")
    value = float(timeout)
    if not 0 < value <= MAX_TIMEOUT:
        raise ValueError(
            f"timeout must be greater than 0 and at most {MAX_TIMEOUT:g} seconds"
        )
    return value


def _validate_max_chars(max_chars: int) -> int:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError("max_chars must be an integer")
    if not 1 <= max_chars <= MAX_PAGE_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}")
    return max_chars


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _wrap_expression(expression: str) -> str:
    """Make a Playwright-style expression callable by the daemon.

    Playwright treats a string that evaluates to a function as a value unless
    it is invoked. The daemon's ``/evaluate`` endpoint always evaluates the raw
    string, so an arrow function like ``() => document.title`` would return
    undefined. Wrapping the expression in an async IIFE keeps both forms
    working: plain expressions and functions.
    """
    value = (expression or "").strip()
    if not value:
        raise ValueError("evaluate requires an expression")
    while value.endswith(";"):
        value = value[:-1].rstrip()
    return (
        "(async () => { const __value = (" + value + "); "
        "return typeof __value === 'function' ? await __value() : await __value; })()"
    )


def _visible_expression(selector: str) -> str:
    return (
        "(() => { const el = document.querySelector("
        + json.dumps(selector)
        + "); if (!el) return false; const style = getComputedStyle(el); "
        "if (style.visibility === 'hidden' || style.display === 'none' || el.hidden) "
        "return false; const rect = el.getBoundingClientRect(); "
        "return rect.width > 0 && rect.height > 0; })()"
    )


def _focus_expression(selector: str) -> str:
    return (
        "(() => { const el = document.querySelector("
        + json.dumps(selector)
        + "); if (!el) return false; el.focus(); return true; })()"
    )


@dataclass
class _Info:
    alive: bool = False
    url: str | None = None
    title: str | None = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(ERROR_DETAIL_CHARS)
    except Exception:  # noqa: BLE001 - best-effort diagnostics
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        text = str(payload["error"])
    text = re.sub(r"\s+", " ", text)
    return text[:ERROR_DETAIL_CHARS]


class CamofoxServerClient:
    """Minimal client for the camofox-browser REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_CAMOFOX_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str = "",
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = normalize_server_url(base_url)
        self.timeout = _validate_timeout(timeout)
        self.api_key = (api_key or "").strip()
        self.user_agent = (user_agent or "").strip() or DEFAULT_USER_AGENT

    # -- transport ---------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            message = (
                f"camofox-browser {method} {path} failed: "
                f"HTTP {exc.code} {exc.reason}"
            )
            if detail:
                message += f": {detail}"
            raise CamoufoxError(message) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            raise CamoufoxError(
                f"Could not reach camofox-browser at {self.base_url}: {reason}. "
                "Start the daemon (for example `start-camofox-browser.cmd start`) "
                "and verify its /health endpoint."
            ) from exc
        except OSError as exc:
            raise CamoufoxError(
                f"Could not reach camofox-browser at {self.base_url}: {exc}"
            ) from exc
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise CamoufoxError(
                f"camofox-browser returned invalid JSON for {method} {path}"
            ) from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise CamoufoxError(
                f"camofox-browser {method} {path} failed: {payload['error']}"
            )
        return payload

    # -- endpoints ---------------------------------------------------------
    def health(self, *, timeout: float | None = None) -> dict[str, Any]:
        payload = self._request("GET", "/health", timeout=timeout)
        return payload if isinstance(payload, dict) else {}

    def create_tab(self, user_id: str, session_key: str) -> tuple[str, str | None]:
        payload = self._request(
            "POST",
            "/tabs",
            body={"userId": user_id, "sessionKey": session_key},
        )
        if not isinstance(payload, dict):
            raise CamoufoxError("camofox-browser returned an invalid tab response")
        tab = payload.get("tab")
        tab_id = (
            payload.get("tabId")
            or payload.get("id")
            or (tab.get("id") if isinstance(tab, dict) else None)
        )
        if not isinstance(tab_id, str) or not tab_id:
            raise CamoufoxError("camofox-browser did not return a tab id")
        url = payload.get("url")
        return tab_id, str(url) if url else None

    def list_tabs(self, user_id: str) -> list[dict[str, Any]]:
        payload = self._request("GET", "/tabs", query={"userId": user_id})
        if not isinstance(payload, dict):
            return []
        tabs = payload.get("tabs")
        if not isinstance(tabs, list):
            return []
        return [tab for tab in tabs if isinstance(tab, dict)]

    def navigate(self, tab_id: str, user_id: str, url: str) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/tabs/{_quote(tab_id)}/navigate",
            body={"userId": user_id, "url": url},
        )
        return payload if isinstance(payload, dict) else {}

    def evaluate(
        self,
        tab_id: str,
        user_id: str,
        expression: str,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        body: dict[str, Any] = {"userId": user_id, "expression": expression}
        if timeout_ms is not None:
            body["timeout"] = int(timeout_ms)
        payload = self._request(
            "POST", f"/tabs/{_quote(tab_id)}/evaluate", body=body
        )
        if not isinstance(payload, dict):
            return payload
        if payload.get("ok") is False:
            raise CamoufoxError(str(payload.get("error") or "JavaScript evaluation failed"))
        return payload.get("result")

    def click(
        self,
        tab_id: str,
        user_id: str,
        *,
        selector: str | None = None,
        ref: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"userId": user_id}
        if selector:
            body["selector"] = selector
        if ref:
            body["ref"] = ref
        payload = self._request(
            "POST", f"/tabs/{_quote(tab_id)}/click", body=body
        )
        return payload if isinstance(payload, dict) else {}

    def type_text(
        self,
        tab_id: str,
        user_id: str,
        *,
        text: str,
        selector: str | None = None,
        ref: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"userId": user_id, "text": text}
        if selector:
            body["selector"] = selector
        if ref:
            body["ref"] = ref
        payload = self._request(
            "POST", f"/tabs/{_quote(tab_id)}/type", body=body
        )
        return payload if isinstance(payload, dict) else {}

    def press(self, tab_id: str, user_id: str, key: str) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/tabs/{_quote(tab_id)}/press",
            body={"userId": user_id, "key": key},
        )
        return payload if isinstance(payload, dict) else {}

    def wait_for_ready(
        self,
        tab_id: str,
        user_id: str,
        *,
        timeout_ms: int,
        wait_for_network: bool = True,
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/tabs/{_quote(tab_id)}/wait",
            body={
                "userId": user_id,
                "timeout": int(timeout_ms),
                "waitForNetwork": bool(wait_for_network),
            },
        )
        return payload if isinstance(payload, dict) else {}

    def close_tab(self, tab_id: str, user_id: str) -> None:
        self._request(
            "DELETE",
            f"/tabs/{_quote(tab_id)}",
            body={"userId": user_id},
        )

    def close_group(self, session_key: str, user_id: str) -> None:
        self._request(
            "DELETE",
            f"/tabs/group/{_quote(session_key)}",
            body={"userId": user_id},
        )


class _ServerSession:
    """One named MCP session backed by a daemon tab group."""

    def __init__(self, manager: "CamofoxServerManager", name: str) -> None:
        self.manager = manager
        self.name = name
        self.settings = manager.settings
        self.client = manager.client
        self.user_id = manager.user_id
        self.session_key = build_session_key(manager.session_prefix, name)
        self._lock = threading.RLock()
        self._info_lock = threading.Lock()
        self._tab_id: str | None = None
        self._closed = False
        self._info = _Info()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise CamoufoxError(f"Camoufox session {self.name!r} is closed")
            self.manager.check_daemon()

    def close(self, *, notify_daemon: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._tab_id = None
            with self._info_lock:
                self._info.alive = False
        if not notify_daemon:
            return
        try:
            self.client.close_group(self.session_key, self.user_id)
        except CamoufoxError:
            # Closing is best effort; the daemon may already be gone.
            pass

    def info(self) -> dict[str, Any]:
        with self._info_lock:
            return {
                "session": self.name,
                "alive": bool(self._tab_id) and not self._closed,
                "url": self._info.url,
                "title": self._info.title,
                "created_at": self._info.created_at,
                "last_used": self._info.last_used,
                "profile_dir": None,
                "tab_id": self._tab_id,
                "user_id": self.user_id,
                "session_key": self.session_key,
            }

    def _touch(self, *, snapshot: dict[str, Any] | None = None) -> None:
        with self._info_lock:
            self._info.last_used = time.time()
            if snapshot is not None:
                self._info.url = snapshot.get("url")
                self._info.title = snapshot.get("title")

    # -- tab plumbing ------------------------------------------------------
    def _ensure_tab(self) -> str:
        with self._lock:
            if self._closed:
                raise CamoufoxError(f"Camoufox session {self.name!r} is closed")
            if self._tab_id:
                return self._tab_id
            for tab in reversed(self.client.list_tabs(self.user_id)):
                if str(tab.get("listItemId") or "") != self.session_key:
                    continue
                tab_id = tab.get("tabId") or tab.get("targetId")
                if tab_id:
                    self._tab_id = str(tab_id)
                    self._touch(
                        snapshot={
                            "url": tab.get("url"),
                            "title": tab.get("title"),
                        }
                    )
                    break
            if self._tab_id is None:
                self._tab_id, url = self.client.create_tab(
                    self.user_id, self.session_key
                )
                self._touch(snapshot={"url": url, "title": None})
            return self._tab_id

    def _run_on_tab(self, operation: Callable[[str], Any]) -> Any:
        """Run an operation, serializing access to this session's tab."""
        with self._lock:
            for attempt in (0, 1):
                tab_id = self._ensure_tab()
                try:
                    return operation(tab_id)
                except CamoufoxError as exc:
                    if attempt == 0 and "Tab not found" in str(exc):
                        self._tab_id = None
                        continue
                    raise
        raise CamoufoxError("camofox-browser tab is unavailable")  # pragma: no cover

    def _evaluate(
        self, tab_id: str, expression: str, timeout_ms: int | None = None
    ) -> Any:
        return self.client.evaluate(
            tab_id,
            self.user_id,
            _wrap_expression(expression),
            timeout_ms=timeout_ms,
        )

    def _wait_for_selector(
        self,
        tab_id: str,
        selector: str,
        timeout_ms: int,
        *,
        required: bool = True,
    ) -> bool:
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
        expression = _visible_expression(selector)
        while True:
            try:
                if self._evaluate(tab_id, expression) is True:
                    return True
            except CamoufoxError:
                if not required:
                    return False
                raise
            if time.monotonic() >= deadline:
                if not required:
                    return False
                raise CamoufoxError(
                    f"Selector {selector!r} did not become visible within "
                    f"{timeout_ms}ms"
                )
            time.sleep(0.25)

    def _snapshot(self, *, max_chars: int = 20_000) -> dict[str, Any]:
        timeout_ms = int(float(self.settings.camoufox_timeout) * 1000)

        def operation(tab_id: str) -> dict[str, Any]:
            raw = self._evaluate(tab_id, _PAGE_META_EXPRESSION, timeout_ms)
            meta = raw if isinstance(raw, dict) else {}
            text = meta.get("text")
            if not isinstance(text, str):
                text = ""
            char_count = len(text)
            try:
                url = str(meta.get("url") or "")
            except Exception:  # noqa: BLE001 - malformed page metadata
                url = ""
            title = meta.get("title")
            return {
                "session": self.name,
                "url": url,
                "title": str(title) if title is not None else None,
                "text": text[:max_chars],
                "char_count": char_count,
                "truncated": char_count > max_chars,
            }

        snapshot = self._run_on_tab(operation)
        self._touch(snapshot=snapshot)
        return snapshot

    # -- operations --------------------------------------------------------
    def open(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        settle_ms: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        target = validate_page_url(url)
        if wait_until not in WAIT_UNTIL:
            raise ValueError(
                "wait_until must be one of: " + ", ".join(sorted(WAIT_UNTIL))
            )
        delay = self.settings.camoufox_settle_ms if settle_ms is None else settle_ms
        timeout_ms = int(float(self.settings.camoufox_timeout) * 1000)

        def operation(tab_id: str) -> None:
            self.client.navigate(tab_id, self.user_id, target)
            if wait_until in {"load", "networkidle"}:
                # The daemon always navigates with domcontentloaded; /wait is
                # the closest equivalent for stricter wait modes.
                try:
                    self.client.wait_for_ready(
                        tab_id,
                        self.user_id,
                        timeout_ms=timeout_ms,
                        wait_for_network=True,
                    )
                except CamoufoxError:
                    pass
            if delay:
                time.sleep(delay / 1000.0)

        self._run_on_tab(operation)
        snapshot = self._snapshot(max_chars=max_chars)
        return {"ok": True, **snapshot}

    def read(self, *, max_chars: int = 20_000) -> dict[str, Any]:
        return {"ok": True, **self._snapshot(max_chars=max_chars)}

    def act(
        self,
        action: str,
        *,
        selector: str | None = None,
        text: str | None = None,
        key: str | None = None,
        expression: str | None = None,
        timeout_ms: int | None = None,
        press_enter: bool = False,
        settle_ms: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        timeout = (
            int(timeout_ms)
            if timeout_ms is not None
            else int(float(self.settings.camoufox_timeout) * 1000)
        )
        if not 1 <= timeout <= 120_000:
            raise ValueError("timeout_ms must be between 1 and 120000")
        if action == "click" and not (selector or "").strip():
            raise ValueError("click requires a selector")
        if action == "type":
            if not (selector or "").strip():
                raise ValueError("type requires a selector")
            if text is None:
                raise ValueError("type requires text")
        if action == "press_key" and not (key or "").strip():
            raise ValueError("press_key requires a key")
        if action == "evaluate" and not (expression or "").strip():
            raise ValueError("evaluate requires an expression")
        if action not in {"click", "type", "press_key", "evaluate", "wait"}:
            raise ValueError(
                "action must be click, type, press_key, evaluate, or wait"
            )

        result: Any = None

        def operation(tab_id: str) -> None:
            nonlocal result
            if action == "click":
                self.client.click(
                    tab_id, self.user_id, selector=(selector or "").strip()
                )
            elif action == "type":
                self.client.type_text(
                    tab_id,
                    self.user_id,
                    selector=(selector or "").strip(),
                    text=text or "",
                )
                if press_enter:
                    self.client.press(tab_id, self.user_id, "Enter")
            elif action == "press_key":
                if selector:
                    focused = self._evaluate(
                        tab_id, _focus_expression(selector), timeout
                    )
                    if focused is not True:
                        raise CamoufoxError(
                            f"Could not focus selector {selector!r}"
                        )
                self.client.press(tab_id, self.user_id, key or "")
            elif action == "evaluate":
                result = jsonable_result(
                    self._evaluate(tab_id, expression or "", timeout)
                )
            elif action == "wait":
                if selector:
                    self._wait_for_selector(tab_id, selector, timeout)
                else:
                    time.sleep(timeout / 1000.0)

        self._run_on_tab(operation)
        delay = (
            self.settings.camoufox_settle_ms if settle_ms is None else settle_ms
        )
        if delay:
            time.sleep(delay / 1000.0)
        snapshot = self._snapshot(max_chars=max_chars)
        return {
            "ok": True,
            "session": self.name,
            "action": action,
            "result": result,
            "page": snapshot,
        }

    def search(
        self,
        query: str,
        *,
        max_results: int,
        language: str,
        time_range: str | None,
        safesearch: int,
        settle_ms: int | None = None,
    ) -> dict[str, Any]:
        settings = self.settings
        engine = (settings.camoufox_search_engine or DEFAULT_SEARCH_ENGINE).strip().lower()
        target = build_search_url(
            engine=engine,
            template=settings.camoufox_search_url,
            query=query,
            language=language,
            time_range=time_range,
            safesearch=safesearch,
        )
        timeout_ms = int(float(settings.camoufox_timeout) * 1000)
        delay = settings.camoufox_settle_ms if settle_ms is None else settle_ms

        def operation(tab_id: str) -> dict[str, Any]:
            self.client.navigate(tab_id, self.user_id, target)
            selector = ENGINE_SELECTORS.get(engine)
            if selector and not settings.camoufox_search_url:
                self._wait_for_selector(
                    tab_id,
                    selector,
                    min(5000, timeout_ms),
                    required=False,
                )
            if delay:
                time.sleep(delay / 1000.0)
            script = ENGINE_SCRIPTS.get(engine)
            if script is None or settings.camoufox_search_url:
                script = GENERIC_SCRIPT
            raw = self._evaluate(tab_id, script, timeout_ms)
            if not isinstance(raw, list):
                raw = []
            results = normalize_search_items(raw, engine, max_results)
            meta_raw = self._evaluate(
                tab_id, _PAGE_META_EXPRESSION, timeout_ms
            )
            meta = meta_raw if isinstance(meta_raw, dict) else {}
            title = meta.get("title")
            final_url = str(meta.get("url") or target)
            page_text = meta.get("text")
            if not isinstance(page_text, str):
                page_text = ""
            body_for_challenge = "" if results else page_text[:4000]
            return {
                "ok": True,
                "query": query,
                "provider": "camoufox",
                "instance": engine,
                "page": 1,
                "result_count": len(results),
                "number_of_results": None,
                "results": results,
                "answers": [],
                "infoboxes": [],
                "suggestions": [],
                "corrections": [],
                "unresponsive_engines": [],
                "search_url": target,
                "final_url": final_url,
                "page_title": str(title) if title is not None else None,
                "challenge_detected": looks_challenged(
                    str(title) if title is not None else None,
                    body_for_challenge,
                ),
                "session": self.name,
                "page_text_preview": page_text[:2000],
            }

        result = self._run_on_tab(operation)
        self._touch(
            snapshot={
                "url": result.get("final_url"),
                "title": result.get("page_title"),
            }
        )
        return result


class CamofoxServerManager:
    """Session manager that proxies to a running camofox-browser daemon."""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.server_url = normalize_server_url(settings.camoufox_server_url)
        self.user_id = (settings.camoufox_server_user or "").strip()
        if not self.user_id:
            raise ValueError("CAMOUFOX_MCP_SERVER_USER must not be empty")
        self.session_prefix = validate_session_prefix(
            settings.camoufox_server_session_prefix
        )
        self.client = CamofoxServerClient(
            self.server_url,
            timeout=settings.camoufox_timeout,
            api_key=settings.camoufox_server_api_key,
        )
        self._sessions: dict[str, _ServerSession] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._health_checked = False
        atexit.register(self.shutdown)

    # -- availability ------------------------------------------------------
    def availability(self) -> tuple[bool, str | None]:
        return True, None

    @property
    def available(self) -> bool:
        return self.availability()[0]

    @property
    def unavailable_reason(self) -> str | None:
        return self.availability()[1]

    def check_daemon(self) -> None:
        """Fail fast with a helpful message when the daemon is unreachable."""
        if self._health_checked:
            return
        try:
            health = self.client.health(timeout=min(self.client.timeout, 5.0))
        except CamoufoxError as exc:
            raise CamoufoxError(
                f"camofox-browser is not reachable at {self.server_url}. "
                "Start the daemon and verify /health. " + str(exc)
            ) from exc
        if health.get("ok") is False or health.get("running") is False:
            raise CamoufoxError(
                f"camofox-browser at {self.server_url} reported an unhealthy "
                f"state: {health}"
            )
        self._health_checked = True

    # -- session access ----------------------------------------------------
    def _session(self, session: str) -> _ServerSession:
        name = validate_session_name(session)
        with self._lock:
            if self._closed:
                raise CamoufoxError("Camoufox browser manager is shut down")
            worker = self._sessions.get(name)
            if worker is None:
                worker = _ServerSession(self, name)
                self._sessions[name] = worker
                try:
                    worker.start()
                except BaseException:
                    self._sessions.pop(name, None)
                    raise
            return worker

    def search(
        self,
        query: str,
        *,
        session: str = DEFAULT_SEARCH_SESSION,
        max_results: int = 10,
        language: str = "auto",
        time_range: str | None = None,
        safesearch: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(
                f"max_results must be between 1 and {MAX_SEARCH_RESULTS}"
            )
        worker = self._session(session)
        return worker.search(
            query,
            max_results=max_results,
            language=language,
            time_range=time_range,
            safesearch=safesearch,
        )

    def open(
        self,
        url: str,
        *,
        session: str = "default",
        wait_until: str = "domcontentloaded",
        settle_ms: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        _validate_max_chars(max_chars)
        worker = self._session(session)
        return worker.open(
            url,
            wait_until=wait_until,
            settle_ms=settle_ms,
            max_chars=max_chars,
        )

    def read(
        self, *, session: str = "default", max_chars: int = 20_000
    ) -> dict[str, Any]:
        _validate_max_chars(max_chars)
        worker = self._session(session)
        return worker.read(max_chars=max_chars)

    def act(
        self,
        action: str,
        *,
        session: str = "default",
        selector: str | None = None,
        text: str | None = None,
        key: str | None = None,
        expression: str | None = None,
        timeout_ms: int | None = None,
        press_enter: bool = False,
        settle_ms: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        _validate_max_chars(max_chars)
        worker = self._session(session)
        return worker.act(
            action,
            selector=selector,
            text=text,
            key=key,
            expression=expression,
            timeout_ms=timeout_ms,
            press_enter=press_enter,
            settle_ms=settle_ms,
            max_chars=max_chars,
        )

    def status(self, session: str | None = None) -> dict[str, Any]:
        with self._lock:
            workers = list(self._sessions.values())
        infos = [worker.info() for worker in workers]

        daemon_health: dict[str, Any] | None = None
        daemon_error: str | None = None
        try:
            daemon_health = self.client.health(
                timeout=min(self.client.timeout, 5.0)
            )
        except CamoufoxError as exc:
            daemon_error = str(exc)

        groups: dict[str, list[dict[str, Any]]] = {}
        if daemon_health is not None:
            try:
                tabs = self.client.list_tabs(self.user_id)
            except CamoufoxError as exc:
                daemon_error = daemon_error or str(exc)
                tabs = []
            for tab in tabs:
                key = str(tab.get("listItemId") or "")
                if key:
                    groups.setdefault(key, []).append(tab)

        known = {info["session"]: info for info in infos}
        for info in infos:
            group = groups.get(info["session_key"]) or []
            if group:
                latest = group[-1]
                info["alive"] = True
                info["url"] = latest.get("url") or info.get("url")
                info["title"] = latest.get("title") or info.get("title")
                info["tab_count"] = len(group)

        if self.session_prefix:
            known_names = {info["session"] for info in infos}
            for key, group in groups.items():
                name = _session_name_for_key(key, self.session_prefix)
                if not name or name in known_names:
                    continue
                latest = group[-1]
                infos.append(
                    {
                        "session": name,
                        "alive": True,
                        "url": latest.get("url"),
                        "title": latest.get("title"),
                        "created_at": None,
                        "last_used": None,
                        "profile_dir": None,
                        "tab_id": latest.get("tabId") or latest.get("targetId"),
                        "user_id": self.user_id,
                        "session_key": key,
                        "tab_count": len(group),
                        "discovered": True,
                    }
                )

        if session:
            name = validate_session_name(session)
            infos = [info for info in infos if info["session"] == name]

        return {
            "ok": True,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "mode": self.settings.camoufox_mode,
            "headless": None,
            "remote_url": None,
            "server_url": self.server_url,
            "user_id": self.user_id,
            "session_prefix": self.session_prefix,
            "daemon": daemon_health
            if daemon_health is not None
            else {"ok": False, "error": daemon_error},
            "profile_dir": None,
            "session_count": len(infos),
            "sessions": infos,
        }

    def close(self, session: str) -> dict[str, Any]:
        name = validate_session_name(session)
        with self._lock:
            worker = self._sessions.pop(name, None)
        if worker is None:
            # Clean up a tab group left behind by a previous server run.
            try:
                self.client.close_group(
                    build_session_key(self.session_prefix, name), self.user_id
                )
            except CamoufoxError:
                pass
            return {"ok": True, "session": name, "closed": False}
        worker.close()
        return {"ok": True, "session": name, "closed": True}

    def close_all(self) -> dict[str, Any]:
        with self._lock:
            workers = list(self._sessions.values())
            self._sessions.clear()
        names: list[str] = []
        for worker in workers:
            worker.close()
            names.append(worker.name)

        # Also clean up groups from an earlier server run, without touching
        # other daemon users or the Node camofox MCP server's own tab groups.
        keys = {
            build_session_key(self.session_prefix, name) for name in names
        }
        if self.session_prefix:
            try:
                tabs = self.client.list_tabs(self.user_id)
            except CamoufoxError:
                tabs = []
            for tab in tabs:
                key = str(tab.get("listItemId") or "")
                if key.startswith(f"{self.session_prefix}-"):
                    keys.add(key)
        for key in keys:
            try:
                self.client.close_group(key, self.user_id)
            except CamoufoxError:
                pass
        return {"ok": True, "closed": len(names), "sessions": names}

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            workers = list(self._sessions.values())
            self._sessions.clear()
        for worker in workers:
            try:
                worker.close(notify_daemon=False)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass


def _session_name_for_key(key: str, prefix: str) -> str | None:
    marker = f"{prefix}-"
    if not key.startswith(marker):
        return None
    try:
        return validate_session_name(key[len(marker) :])
    except ValueError:
        return None