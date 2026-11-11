"""Persistent Camoufox browser sessions.

The manager owns one worker thread per named session. Each worker keeps its
Playwright/Camoufox objects alive for the lifetime of the MCP server, so cookies,
local storage, and the current page survive across tool calls. Local sessions use
a persistent Firefox profile on disk; remote sessions connect to an existing
Playwright websocket endpoint (``python -m camoufox server`` or
``camoufox-remote-host``).

Camoufox itself is optional. Importing this module never imports Camoufox or
Playwright, so the search server still starts when only SearXNG or Tavily is
configured.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import queue
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from .exceptions import CamoufoxError

if TYPE_CHECKING:
    from .settings import Settings

DEFAULT_PROFILE_NAME = "camoufox"
DEFAULT_SEARCH_SESSION = "search"
DEFAULT_LOCALE = "en-US"
DEFAULT_SEARCH_ENGINE = "duckduckgo"
DEFAULT_TIMEOUT = 45.0
MAX_TIMEOUT = 120.0
DEFAULT_SETTLE_MS = 750
MAX_PAGE_CHARS = 200_000
MAX_ACTION_RESULT_CHARS = 100_000
MAX_SEARCH_RESULTS = 20
WAIT_UNTIL = frozenset({"commit", "domcontentloaded", "load", "networkidle"})
SEARCH_ENGINES = ("duckduckgo", "bing", "google")

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_CHALLENGE_MARKERS = (
    "captcha",
    "unusual traffic",
    "verify you are human",
    "access denied",
    "cf-chl",
    "challenge-platform",
    "are you a robot",
)

_ENGINE_URLS = {
    "duckduckgo": "https://html.duckduckgo.com/html/?q={query}",
    "bing": "https://www.bing.com/search?q={query}",
    "google": "https://www.google.com/search?q={query}",
}
_ENGINE_SELECTORS = {
    "duckduckgo": "div.result, article[data-testid='result']",
    "bing": "li.b_algo",
    "google": "div.MjjYud, div.g",
}
_ENGINE_SCRIPTS = {
    "duckduckgo": r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const decode = (href) => {
    try {
      const parsed = new URL(href, location.href);
      const target = parsed.searchParams.get('uddg');
      return target ? decodeURIComponent(target) : parsed.href;
    } catch (_) { return href; }
  };
  const out = [];
  for (const card of document.querySelectorAll("div.result, article[data-testid='result']")) {
    const a = card.querySelector('a.result__a, a[data-testid="result-title-a"], h2 a');
    if (!a) continue;
    const snippet = card.querySelector('.result__snippet, [data-result="snippet"], .result-snippet');
    out.push({
      title: clean(a.innerText),
      url: decode(a.getAttribute('href') || a.href),
      snippet: clean(snippet ? snippet.innerText : '')
    });
  }
  return out;
}
""",
    "bing": r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const card of document.querySelectorAll('li.b_algo')) {
    const a = card.querySelector('h2 a');
    if (!a) continue;
    const snippet = card.querySelector('.b_caption p, .b_algoSlug, .b_lineclamp2, .b_lineclamp3, p');
    out.push({
      title: clean(a.innerText),
      url: a.href,
      snippet: clean(snippet ? snippet.innerText : '')
    });
  }
  return out;
}
""",
    "google": r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const card of document.querySelectorAll('div.MjjYud, div.g')) {
    const a = card.querySelector('a[href] h3')?.closest('a') || card.querySelector('a[href]');
    if (!a) continue;
    const heading = a.querySelector('h3');
    if (!heading) continue;
    const snippet = card.querySelector('div.VwiC3b, span.aCOpRe, div.IsZvec, div[data-sncf]');
    out.push({
      title: clean(heading.innerText),
      url: a.href,
      snippet: clean(snippet ? snippet.innerText : '')
    });
  }
  return out;
}
""",
}
_GENERIC_SCRIPT = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href;
    if (!/^https?:/i.test(href) || seen.has(href)) continue;
    const text = clean(a.innerText);
    if (text.length < 8) continue;
    const card = a.closest('li, article, main, section, div');
    if (!card) continue;
    const snippet = clean(card.innerText).replace(text, '').slice(0, 400);
    seen.add(href);
    out.push({title: text, url: href, snippet});
  }
  return out.slice(0, 30);
}
"""


def validate_session_name(name: str) -> str:
    value = (name or "").strip()
    if not _SESSION_RE.fullmatch(value):
        raise ValueError(
            "session must be 1-40 characters: letters, digits, dot, dash, or underscore"
        )
    return value


def parse_proxy(raw: str) -> dict[str, str] | None:
    """Convert a proxy URL (or JSON object) to Playwright's proxy dict."""
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CAMOUFOX_MCP_PROXY is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict) or not parsed.get("server"):
            raise ValueError("CAMOUFOX_MCP_PROXY JSON must include a 'server' key")
        return {str(key): str(item) for key, item in parsed.items()}
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https", "socks5"}:
        raise ValueError("CAMOUFOX_MCP_PROXY must use http, https, or socks5")
    if not parsed.hostname or not parsed.port:
        raise ValueError("CAMOUFOX_MCP_PROXY must include a host and port")
    server = urllib.parse.urlunsplit(
        (parsed.scheme, f"{parsed.hostname}:{parsed.port}", "", "", "")
    )
    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        proxy["password"] = urllib.parse.unquote(parsed.password)
    return proxy


def _lang_region(language: str) -> str | None:
    value = (language or "").strip().replace("_", "-").lower()
    if not value or value == "auto":
        return None
    parts = [part for part in value.split("-") if part]
    if len(parts) >= 2:
        return f"{parts[-1]}-{parts[0]}"
    return f"{parts[0]}-{parts[0]}"


def build_search_url(
    *,
    engine: str,
    template: str,
    query: str,
    language: str = "auto",
    time_range: str | None = None,
    safesearch: int = 0,
) -> str:
    """Build a search URL and add the common engine filters.

    A custom template is returned after only the ``{query}`` substitution; the
    caller owns all other parameters in that case.
    """
    selected = (engine or DEFAULT_SEARCH_ENGINE).strip().lower()
    custom = bool((template or "").strip())
    if not custom and selected not in _ENGINE_URLS:
        raise ValueError(
            "camoufox_search_engine must be one of: "
            + ", ".join(SEARCH_ENGINES)
            + ", or set CAMOUFOX_MCP_SEARCH_URL"
        )
    raw_template = (template or "").strip() or _ENGINE_URLS[selected]
    if "{query}" not in raw_template:
        raise ValueError("CAMOUFOX_MCP_SEARCH_URL must contain a {query} placeholder")
    url = raw_template.replace("{query}", urllib.parse.quote_plus(query))
    if custom:
        return url

    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if selected == "duckduckgo":
        if time_range:
            params.append(("df", {"day": "d", "week": "w", "month": "m", "year": "y"}[time_range]))
        if safesearch:
            params.append(("kp", "1" if safesearch == 2 else "-1"))
        region = _lang_region(language)
        if region:
            params.append(("kl", region))
    elif selected == "google":
        if time_range:
            params.append(("tbs", {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}[time_range]))
        if safesearch:
            params.append(("safe", "active"))
        if language and language.strip().lower() != "auto":
            params.append(("hl", language.strip().split("-")[0].lower()))
    elif selected == "bing":
        if safesearch:
            params.append(("adlt", "strict"))
        if language and language.strip().lower() != "auto":
            params.append(("setlang", language.strip().split("-")[0].lower()))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), "")
    )


@dataclass
class _Job:
    fn: Callable[[Any], Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


@dataclass
class _Info:
    alive: bool = False
    url: str | None = None
    title: str | None = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class _SessionWorker:
    """One thread owning one Playwright/Camoufox session."""

    def __init__(self, manager: "CamoufoxBrowserManager", name: str) -> None:
        self.manager = manager
        self.name = name
        self.settings = manager.settings
        self.profile_dir = manager.profile_dir / name
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._closed = False
        self._info = _Info()
        self._info_lock = threading.Lock()
        self._context: Any = None
        self._page: Any = None
        self._browser: Any = None
        self._camoufox_cm: Any = None
        self._playwright_cm: Any = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._closed:
            raise CamoufoxError(f"Camoufox session {self.name!r} is closed")
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"camoufox-{self.name}",
            daemon=True,
        )
        self._thread.start()
        launch_timeout = float(self.settings.camoufox_timeout) + 30.0
        if not self._ready.wait(timeout=launch_timeout):
            raise CamoufoxError(
                f"Camoufox session {self.name!r} did not start within "
                f"{launch_timeout:g}s."
            )
        if self._start_error is not None:
            raise CamoufoxError(
                f"Could not start Camoufox session {self.name!r}: {self._start_error}"
            ) from self._start_error

    def submit(self, fn: Callable[[Any], Any], timeout: float | None = None) -> Any:
        if self._closed or not self._thread or not self._thread.is_alive():
            raise CamoufoxError(f"Camoufox session {self.name!r} is not running.")
        job = _Job(fn=fn)
        self._jobs.put(job)
        wait = timeout if timeout is not None else float(self.settings.camoufox_timeout) + 30.0
        if not job.event.wait(timeout=wait):
            raise CamoufoxError(
                f"Camoufox session {self.name!r} did not answer within {wait:g}s."
            )
        if job.error is not None:
            if isinstance(job.error, (ValueError, CamoufoxError)):
                raise job.error
            raise CamoufoxError(
                f"Camoufox session {self.name!r} failed: {job.error}"
            ) from job.error
        return job.result

    def close(self) -> None:
        self._closed = True
        if self._thread and self._thread.is_alive():
            self._jobs.put(None)
            self._thread.join(timeout=10.0)
        with self._info_lock:
            self._info.alive = False

    def info(self) -> dict[str, Any]:
        with self._info_lock:
            info = {
                "session": self.name,
                "alive": bool(self._thread and self._thread.is_alive() and not self._closed),
                "url": self._info.url,
                "title": self._info.title,
                "created_at": self._info.created_at,
                "last_used": self._info.last_used,
                "profile_dir": str(self.profile_dir),
            }
        return info

    def _touch(self, *, snapshot: dict[str, Any] | None = None) -> None:
        with self._info_lock:
            self._info.last_used = time.time()
            if snapshot is not None:
                self._info.url = snapshot.get("url")
                self._info.title = snapshot.get("title")

    # -- worker ------------------------------------------------------------
    def _thread_main(self) -> None:
        try:
            self._open()
            self._ready.set()
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                try:
                    job.result = job.fn(self)
                except BaseException as exc:  # noqa: BLE001 - re-raised in caller
                    job.error = exc
                finally:
                    job.event.set()
        except BaseException as exc:  # noqa: BLE001 - launch failures surface to caller
            self._start_error = exc
            self._ready.set()
        finally:
            self._close_objects()
            with self._info_lock:
                self._info.alive = False

    def _open(self) -> None:
        settings = self.settings
        if settings.camoufox_mode == "remote":
            self._open_remote()
        else:
            self._open_local()
        if self._context is None:
            raise CamoufoxError("Camoufox did not produce a browser context")
        pages = list(getattr(self._context, "pages", []))
        self._page = next((page for page in pages if not page.is_closed()), None)
        if self._page is None:
            self._page = self._context.new_page()
        timeout_ms = int(float(settings.camoufox_timeout) * 1000)
        self._page.set_default_timeout(timeout_ms)
        self._page.set_default_navigation_timeout(timeout_ms)
        self._snapshot(max_chars=1000)

    def _open_local(self) -> None:
        settings = self.settings
        if importlib.util.find_spec("camoufox") is None:
            raise CamoufoxError(
                "Camoufox is not installed. Run "
                "`pip install -e \".[browser]\"` and `python -m camoufox fetch`."
            )
        from camoufox.sync_api import Camoufox  # imported only in local mode

        options: dict[str, Any] = {
            "persistent_context": True,
            "user_data_dir": str(self.profile_dir),
            "headless": bool(settings.camoufox_headless),
            "humanize": bool(settings.camoufox_humanize),
            "locale": settings.camoufox_locale or DEFAULT_LOCALE,
            "enable_cache": True,
            "timeout": int(float(settings.camoufox_timeout) * 1000),
        }
        if settings.camoufox_os:
            options["os"] = settings.camoufox_os
        proxy = parse_proxy(settings.camoufox_proxy)
        if proxy:
            options["proxy"] = proxy
        if settings.camoufox_geoip:
            options["geoip"] = settings.camoufox_geoip
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._camoufox_cm = Camoufox(**options)
        self._context = self._camoufox_cm.__enter__()

    def _open_remote(self) -> None:
        settings = self.settings
        if importlib.util.find_spec("playwright") is None:
            raise CamoufoxError(
                "Playwright is not installed; run `pip install -e \".[browser]\"`."
            )
        if not settings.camoufox_remote_url:
            raise CamoufoxError(
                "CAMOUFOX_MCP_MODE=remote requires CAMOUFOX_MCP_REMOTE_URL "
                "(for example ws://127.0.0.1:9222/)."
            )
        from playwright.sync_api import sync_playwright

        self._playwright_cm = sync_playwright()
        playwright = self._playwright_cm.__enter__()
        timeout_ms = int(float(settings.camoufox_timeout) * 1000)
        self._browser = playwright.firefox.connect(
            settings.camoufox_remote_url,
            timeout=timeout_ms,
        )
        contexts = list(getattr(self._browser, "contexts", []))
        if contexts:
            self._context = contexts[0]
        else:
            self._context = self._browser.new_context()

    def _close_objects(self) -> None:
        page, context = self._page, self._context
        browser, camoufox_cm = self._browser, self._camoufox_cm
        playwright_cm = self._playwright_cm
        self._page = self._context = self._browser = None
        self._camoufox_cm = self._playwright_cm = None
        for obj in (page, context):
            try:
                if obj is not None and not obj.is_closed():
                    obj.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if camoufox_cm is not None:
            try:
                camoufox_cm.__exit__(None, None, None)
            except Exception:
                pass
        if playwright_cm is not None:
            try:
                playwright_cm.__exit__(None, None, None)
            except Exception:
                pass

    # -- page operations ---------------------------------------------------
    def _page_ref(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        pages = list(getattr(self._context, "pages", []))
        page = next((item for item in pages if not item.is_closed()), None)
        if page is None:
            page = self._context.new_page()
        self._page = page
        return page

    def _snapshot(self, *, max_chars: int = 20_000) -> dict[str, Any]:
        page = self._page_ref()
        try:
            text = page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            text = ""
        if not isinstance(text, str):
            text = str(text)
        try:
            title = page.title()
        except Exception:
            title = None
        char_count = len(text)
        snapshot = {
            "session": self.name,
            "url": page.url,
            "title": title,
            "text": text[:max_chars],
            "char_count": char_count,
            "truncated": char_count > max_chars,
        }
        self._touch(snapshot=snapshot)
        return snapshot

    def open(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        settle_ms: int | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        target = _validate_page_url(url)
        if wait_until not in WAIT_UNTIL:
            raise ValueError(
                "wait_until must be one of: " + ", ".join(sorted(WAIT_UNTIL))
            )
        page = self._page_ref()
        page.goto(target, wait_until=wait_until)
        delay = self.settings.camoufox_settle_ms if settle_ms is None else settle_ms
        if delay:
            page.wait_for_timeout(delay)
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
        page = self._page_ref()
        timeout = int(timeout_ms) if timeout_ms is not None else int(
            float(self.settings.camoufox_timeout) * 1000
        )
        if not 1 <= timeout <= 120_000:
            raise ValueError("timeout_ms must be between 1 and 120000")
        result: Any = None

        if action == "click":
            if not (selector or "").strip():
                raise ValueError("click requires a selector")
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout)
            locator.click(timeout=timeout)
        elif action == "type":
            if not (selector or "").strip():
                raise ValueError("type requires a selector")
            if text is None:
                raise ValueError("type requires text")
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout)
            locator.fill(text, timeout=timeout)
            if press_enter:
                locator.press("Enter", timeout=timeout)
        elif action == "press_key":
            if not (key or "").strip():
                raise ValueError("press_key requires a key")
            if selector:
                page.locator(selector).first.press(key, timeout=timeout)
            else:
                page.keyboard.press(key)
        elif action == "evaluate":
            if not (expression or "").strip():
                raise ValueError("evaluate requires an expression")
            result = _jsonable(page.evaluate(expression))
        elif action == "wait":
            if selector:
                page.locator(selector).first.wait_for(state="visible", timeout=timeout)
            else:
                page.wait_for_timeout(timeout)
        else:
            raise ValueError(
                "action must be click, type, press_key, evaluate, or wait"
            )

        delay = self.settings.camoufox_settle_ms if settle_ms is None else settle_ms
        if delay:
            page.wait_for_timeout(delay)
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
        target = build_search_url(
            engine=settings.camoufox_search_engine,
            template=settings.camoufox_search_url,
            query=query,
            language=language,
            time_range=time_range,
            safesearch=safesearch,
        )
        page = self._page_ref()
        page.goto(target, wait_until="domcontentloaded")
        selector = _ENGINE_SELECTORS.get(settings.camoufox_search_engine.strip().lower())
        if selector and not settings.camoufox_search_url:
            try:
                page.wait_for_selector(selector, timeout=min(5000, int(settings.camoufox_timeout * 1000)))
            except Exception:
                pass
        delay = settings.camoufox_settle_ms if settle_ms is None else settle_ms
        if delay:
            page.wait_for_timeout(delay)

        script = _ENGINE_SCRIPTS.get(settings.camoufox_search_engine.strip().lower())
        if script is None or settings.camoufox_search_url:
            script = _GENERIC_SCRIPT
        raw = page.evaluate(script)
        if not isinstance(raw, list):
            raw = []
        results = _normalize_search_items(raw, settings.camoufox_search_engine, max_results)
        title = None
        try:
            title = page.title()
        except Exception:
            pass
        body_for_challenge = ""
        if not results:
            try:
                body_for_challenge = page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 4000) : ''"
                )
            except Exception:
                body_for_challenge = ""
        challenge = _looks_challenged(title, body_for_challenge)
        snapshot = None
        try:
            snapshot = self._snapshot(max_chars=1000)
        except Exception:
            pass
        return {
            "ok": True,
            "query": query,
            "provider": "camoufox",
            "instance": settings.camoufox_search_engine,
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
            "final_url": page.url,
            "page_title": title,
            "challenge_detected": challenge,
            "session": self.name,
            "page_text_preview": (snapshot or {}).get("text", "")[:2000],
        }


class CamoufoxBrowserManager:
    """Lifecycle manager for persistent named Camoufox sessions."""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.profile_dir = Path(settings.camoufox_profile_dir)
        self._workers: dict[str, _SessionWorker] = {}
        self._lock = threading.Lock()
        self._closed = False
        atexit.register(self.shutdown)

    # -- availability ------------------------------------------------------
    def availability(self) -> tuple[bool, str | None]:
        mode = self.settings.camoufox_mode
        if mode == "remote":
            if importlib.util.find_spec("playwright") is None:
                return False, "Playwright is not installed; install the 'browser' extra."
            if not self.settings.camoufox_remote_url:
                return False, (
                    "CAMOUFOX_MCP_MODE=remote requires CAMOUFOX_MCP_REMOTE_URL."
                )
            return True, None
        if importlib.util.find_spec("camoufox") is None:
            return False, (
                "Camoufox is not installed. Run `pip install -e \".[browser]\"` and "
                "`python -m camoufox fetch`."
            )
        if importlib.util.find_spec("playwright") is None:
            return False, "Playwright is not installed; install the 'browser' extra."
        return True, None

    @property
    def available(self) -> bool:
        return self.availability()[0]

    @property
    def unavailable_reason(self) -> str | None:
        return self.availability()[1]

    # -- session access ----------------------------------------------------
    def _worker(self, session: str) -> _SessionWorker:
        name = validate_session_name(session)
        with self._lock:
            if self._closed:
                raise CamoufoxError("Camoufox browser manager is shut down")
            worker = self._workers.get(name)
            if worker is None:
                worker = _SessionWorker(self, name)
                self._workers[name] = worker
            worker.start()
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
            raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        worker = self._worker(session)
        return worker.submit(
            lambda item: item.search(
                query,
                max_results=max_results,
                language=language,
                time_range=time_range,
                safesearch=safesearch,
            )
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
        worker = self._worker(session)
        return worker.submit(
            lambda item: item.open(
                url,
                wait_until=wait_until,
                settle_ms=settle_ms,
                max_chars=max_chars,
            )
        )

    def read(
        self, *, session: str = "default", max_chars: int = 20_000
    ) -> dict[str, Any]:
        _validate_max_chars(max_chars)
        worker = self._worker(session)
        return worker.submit(lambda item: item.read(max_chars=max_chars))

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
        worker = self._worker(session)
        return worker.submit(
            lambda item: item.act(
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
        )

    def status(self, session: str | None = None) -> dict[str, Any]:
        with self._lock:
            workers = list(self._workers.values())
        infos = [worker.info() for worker in workers]
        if session:
            name = validate_session_name(session)
            infos = [info for info in infos if info["session"] == name]
        return {
            "ok": True,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "mode": self.settings.camoufox_mode,
            "headless": bool(self.settings.camoufox_headless)
            if self.settings.camoufox_mode == "local"
            else None,
            "remote_url": self.settings.camoufox_remote_url or None,
            "profile_dir": str(self.profile_dir),
            "session_count": len(infos),
            "sessions": infos,
        }

    def close(self, session: str) -> dict[str, Any]:
        name = validate_session_name(session)
        with self._lock:
            worker = self._workers.pop(name, None)
        if worker is None:
            return {"ok": True, "session": name, "closed": False}
        worker.close()
        return {"ok": True, "session": name, "closed": True}

    def close_all(self) -> dict[str, Any]:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.close()
        return {
            "ok": True,
            "closed": len(workers),
            "sessions": [worker.name for worker in workers],
        }

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.close()


def _validate_page_url(raw: str) -> str:
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


def _validate_max_chars(max_chars: int) -> int:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError("max_chars must be an integer")
    if not 1 <= max_chars <= MAX_PAGE_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}")
    return max_chars


def _jsonable(value: Any) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        encoded = json.dumps(str(value), ensure_ascii=False)
    if len(encoded) > MAX_ACTION_RESULT_CHARS:
        return {
            "truncated": True,
            "char_count": len(encoded),
            "preview": encoded[:MAX_ACTION_RESULT_CHARS],
        }
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return str(value)


def _normalize_search_items(
    raw: list[Any], engine: str, max_results: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
            continue
        seen.add(url)
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": str(item.get("snippet") or "").strip(),
                "engine": f"camoufox:{engine}",
                "engines": ["camoufox", engine],
                "score": len(results) + 1,
                "category": None,
                "published_date": None,
                "thumbnail": None,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _looks_challenged(title: str | None, body: str | None) -> bool:
    text = f"{title or ''} {body or ''}".lower()
    return any(marker in text for marker in _CHALLENGE_MARKERS)