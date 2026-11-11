"""Provider-neutral search orchestration for Dynamic Search Kit.

SearXNG, Tavily, TinyFish, and Camoufox all return the same result shape. The
combined tool walks a configured provider chain in order and either stops at
the first non-empty result or merges every provider's results.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .camoufox_browser import (
    DEFAULT_SEARCH_SESSION,
    MAX_SEARCH_RESULTS as CAMOUFOX_MAX_RESULTS,
    CamoufoxBrowserManager,
)
from .exceptions import (
    CamoufoxError,
    DynamicSearchError,
    SearxngError,
    TavilyError,
)
from .searxng import (
    DEFAULT_PAGE_CHARS,
    MAX_PAGE_CHARS,
    MAX_SEARCH_RESULTS,
    SearxngClient,
    fetch_page,
)
from .settings import (
    ALL_SEARCH_PROVIDERS,
    DEFAULT_SEARCH_PROVIDERS,
    FETCH_RENDER_MODES,
    SEARCH_STRATEGIES,
    Settings,
)
from .tavily import MAX_SEARCH_RESULTS as TAVILY_MAX_RESULTS, TavilyClient
from .tinyfish import TinyFishClient, TinyFishError

SUPPORTED_PROVIDERS = DEFAULT_SEARCH_PROVIDERS
ALL_PROVIDERS = ALL_SEARCH_PROVIDERS
TIME_RANGES = frozenset({"day", "week", "month", "year"})
_JS_MARKERS = (
    "enable javascript",
    "javascript is required",
    "javascript is disabled",
    "please enable js",
    "requires javascript",
)
_MARKDOWN_LINK = re.compile(r"\]\(https?://", re.IGNORECASE)


def normalize_provider_chain(
    values: Sequence[str] | str | None,
    *,
    default: Sequence[str] = SUPPORTED_PROVIDERS,
) -> tuple[str, ...]:
    """Validate a provider list while preserving its order."""
    if values is None:
        raw = list(default)
    elif isinstance(values, str):
        raw = [part for part in values.split(",")]
    else:
        raw = list(values)
    cleaned: list[str] = []
    for value in raw:
        name = str(value).strip().lower()
        if not name:
            continue
        if name not in ALL_PROVIDERS:
            raise ValueError(
                f"unknown search provider {name!r}; use "
                + ", ".join(ALL_PROVIDERS)
            )
        if name in cleaned:
            raise ValueError(f"search provider {name!r} is listed more than once")
        cleaned.append(name)
    if not cleaned:
        raise ValueError("at least one search provider is required")
    return tuple(cleaned)


def _clean_terms(
    values: Sequence[str] | None, name: str, *, maximum: int = 20
) -> tuple[str, ...] | None:
    if values is None:
        return None
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{name} must not contain empty strings")
        if len(text) > 100:
            raise ValueError(f"{name} entries must be at most 100 characters")
        cleaned.append(text)
    if len(cleaned) > maximum:
        raise ValueError(f"{name} supports at most {maximum} entries")
    return tuple(cleaned) or None


@dataclass
class SearchRequest:
    query: str
    max_results: int = 10
    categories: tuple[str, ...] | None = None
    engines: tuple[str, ...] | None = None
    language: str = "auto"
    pageno: int = 1
    time_range: str | None = None
    safesearch: int = 0
    instance_url: str | None = None
    include_domains: tuple[str, ...] | None = None
    exclude_domains: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        self.query = (self.query or "").strip()
        if not self.query:
            raise ValueError("query must not be empty")
        if len(self.query) > 2000:
            raise ValueError("query must be at most 2000 characters")
        if isinstance(self.max_results, bool) or not isinstance(self.max_results, int):
            raise ValueError("max_results must be an integer")
        if not 1 <= self.max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(
                f"max_results must be between 1 and {MAX_SEARCH_RESULTS}"
            )
        if isinstance(self.pageno, bool) or not isinstance(self.pageno, int):
            raise ValueError("pageno must be an integer")
        if not 1 <= self.pageno <= 20:
            raise ValueError("pageno must be between 1 and 20")
        if self.safesearch not in {0, 1, 2}:
            raise ValueError("safesearch must be 0, 1, or 2")
        if self.time_range is not None and self.time_range not in TIME_RANGES:
            raise ValueError(
                "time_range must be one of: " + ", ".join(sorted(TIME_RANGES))
            )
        self.language = (self.language or "auto").strip() or "auto"
        if len(self.language) > 20:
            raise ValueError("language must be at most 20 characters")
        self.categories = _clean_terms(self.categories, "categories")
        self.engines = _clean_terms(self.engines, "engines")
        self.include_domains = _clean_terms(
            self.include_domains, "include_domains", maximum=20
        )
        self.exclude_domains = _clean_terms(
            self.exclude_domains, "exclude_domains", maximum=20
        )
        if self.instance_url is not None:
            self.instance_url = self.instance_url.strip() or None


class DynamicSearchService:
    """Search across the configured providers and own the browser backend."""

    def __init__(
        self,
        settings: Settings,
        *,
        browser: CamoufoxBrowserManager | None = None,
    ) -> None:
        self.settings = settings
        self.browser = browser or CamoufoxBrowserManager(settings)

    # -- provider availability --------------------------------------------
    def provider_availability(self, provider: str) -> tuple[bool, str | None]:
        if provider == "searxng":
            return True, None
        if provider == "tavily":
            client = self._tavily_client()
            if client.available:
                return True, None
            return False, (
                "Tavily is not configured; set TAVILY_API_KEY or add it to .env."
            )
        if provider == "camoufox":
            availability = getattr(self.browser, "availability", None)
            if callable(availability):
                return availability()
            available = bool(getattr(self.browser, "available", False))
            reason = getattr(self.browser, "unavailable_reason", None)
            return available, reason
        if provider == "tinyfish":
            client = self._tinyfish_client()
            if client.available:
                return True, None
            return False, (
                "TinyFish is not configured; set TINYFISH_API_KEY or add it to .env."
            )
        raise ValueError(f"unknown search provider {provider!r}")

    def _unsupported_reason(
        self, provider: str, request: SearchRequest
    ) -> str | None:
        if (request.categories or request.engines) and provider != "searxng":
            return "categories and engines are only supported by SearXNG"
        if request.instance_url and provider != "searxng":
            return "instance_url is only supported by SearXNG"
        if request.pageno != 1 and provider in {"tavily", "camoufox"}:
            return f"{provider} does not support pagination"
        if (request.include_domains or request.exclude_domains) and provider != "tavily":
            return "include_domains and exclude_domains are only supported by Tavily"
        if (
            provider == "camoufox"
            and request.time_range
            and self.settings.camoufox_search_engine.strip().lower() == "bing"
            and not self.settings.camoufox_search_url
        ):
            return "Camoufox's Bing engine does not support time_range"
        return None

    # -- clients -----------------------------------------------------------
    def _searxng_client(self, instance_url: str | None = None) -> SearxngClient:
        return SearxngClient(
            instance_url or self.settings.searxng_base_url,
            timeout=self.settings.searxng_timeout,
            user_agent=self.settings.searxng_user_agent,
        )

    def _tavily_client(self) -> TavilyClient:
        return TavilyClient(
            self.settings.tavily_base_url,
            timeout=self.settings.tavily_timeout,
            env_file=self.settings.tavily_env_file or None,
            search_depth=self.settings.tavily_search_depth,
            include_answer=self.settings.tavily_include_answer,
            include_raw_content=self.settings.tavily_include_raw_content,
        )

    def _tinyfish_client(self) -> TinyFishClient:
        return TinyFishClient(
            self.settings.tinyfish_base_url,
            timeout=self.settings.tinyfish_timeout,
            env_file=self.settings.tinyfish_env_file or None,
        )

    # -- individual providers ---------------------------------------------
    def search_provider(
        self,
        provider: str,
        request: SearchRequest,
        *,
        camoufox_session: str | None = None,
    ) -> dict[str, Any]:
        if provider == "searxng":
            return self._search_searxng(request)
        if provider == "tavily":
            return self._search_tavily(request)
        if provider == "camoufox":
            return self._search_camoufox(request, session=camoufox_session)
        if provider == "tinyfish":
            return self._search_tinyfish(request)
        raise ValueError(f"unknown search provider {provider!r}")

    def _search_searxng(self, request: SearchRequest) -> dict[str, Any]:
        client = self._searxng_client(request.instance_url)
        try:
            return client.search(
                request.query,
                max_results=request.max_results,
                categories=request.categories,
                engines=request.engines,
                language=request.language,
                pageno=request.pageno,
                time_range=request.time_range,
                safesearch=request.safesearch,
            )
        except SearxngError as exc:
            message = str(exc)
            if self.settings.searxng_start_cmd and "Could not reach" in message:
                message += " Call start_searxng to launch the local instance."
            raise SearxngError(message) from exc

    def _search_tavily(self, request: SearchRequest) -> dict[str, Any]:
        if request.categories or request.engines:
            raise ValueError("Tavily does not support categories or engines")
        if request.instance_url:
            raise ValueError("instance_url is only supported by SearXNG")
        if request.pageno != 1:
            raise TavilyError("Tavily does not support pagination; use pageno=1.")
        effective = min(request.max_results, TAVILY_MAX_RESULTS)
        result = self._tavily_client().search(
            request.query,
            max_results=effective,
            pageno=1,
            time_range=request.time_range,
            safesearch=request.safesearch,
            language=request.language,
            include_domains=request.include_domains,
            exclude_domains=request.exclude_domains,
        )
        if effective < request.max_results:
            result["max_results_limited"] = True
        return result

    def _search_camoufox(
        self, request: SearchRequest, *, session: str | None = None
    ) -> dict[str, Any]:
        if request.categories or request.engines:
            raise ValueError("Camoufox does not support categories or engines")
        if request.instance_url:
            raise ValueError("instance_url is only supported by SearXNG")
        if request.pageno != 1:
            raise CamoufoxError("Camoufox does not support pagination; use pageno=1.")
        effective = min(request.max_results, CAMOUFOX_MAX_RESULTS)
        result = self.browser.search(
            request.query,
            session=session
            or self.settings.camoufox_search_session
            or DEFAULT_SEARCH_SESSION,
            max_results=effective,
            language=request.language,
            time_range=request.time_range,
            safesearch=request.safesearch,
        )
        if effective < request.max_results:
            result["max_results_limited"] = True
        return result

    def _search_tinyfish(self, request: SearchRequest) -> dict[str, Any]:
        if request.categories or request.engines:
            raise ValueError("TinyFish does not support categories or engines")
        if request.instance_url:
            raise ValueError("instance_url is only supported by SearXNG")
        if request.pageno > 11:
            raise TinyFishError("TinyFish supports pages 1-11.")
        return self._tinyfish_client().search(
            request.query,
            max_results=request.max_results,
            pageno=request.pageno,
            time_range=request.time_range,
            safesearch=request.safesearch,
            language=request.language,
        )

    # -- combined ----------------------------------------------------------
    def search_combined(
        self,
        request: SearchRequest,
        *,
        providers: Sequence[str] | str | None = None,
        strategy: str | None = None,
    ) -> dict[str, Any]:
        chain = normalize_provider_chain(
            providers if providers is not None else self.settings.search_providers
        )
        selected_strategy = (strategy or self.settings.search_strategy).strip().lower()
        if selected_strategy not in SEARCH_STRATEGIES:
            raise ValueError(
                "strategy must be one of: " + ", ".join(SEARCH_STRATEGIES)
            )

        attempts: list[dict[str, Any]] = []
        winner: dict[str, Any] | None = None
        winner_provider: str | None = None
        last_empty: dict[str, Any] | None = None
        merged: list[tuple[str, dict[str, Any]]] = []

        for provider in chain:
            unsupported = self._unsupported_reason(provider, request)
            if unsupported:
                attempts.append(
                    {
                        "provider": provider,
                        "status": "skipped",
                        "reason": unsupported,
                    }
                )
                continue
            available, reason = self.provider_availability(provider)
            if not available:
                attempts.append(
                    {
                        "provider": provider,
                        "status": "skipped",
                        "reason": reason or "provider is not configured",
                    }
                )
                continue

            started = time.monotonic()
            try:
                result = self.search_provider(provider, request)
            except (SearxngError, TavilyError, CamoufoxError, TinyFishError) as exc:
                attempts.append(
                    {
                        "provider": provider,
                        "status": "error",
                        "error": str(exc),
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)
            count = int(result.get("result_count") or len(result.get("results") or []))
            if count:
                attempts.append(
                    {
                        "provider": provider,
                        "status": "success",
                        "result_count": count,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                if selected_strategy == "first":
                    winner = result
                    winner_provider = provider
                    break
                merged.append((provider, result))
            else:
                attempts.append(
                    {
                        "provider": provider,
                        "status": "empty",
                        "result_count": 0,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                last_empty = result

        if selected_strategy == "first":
            if winner is not None:
                return self._annotate(
                    winner,
                    request=request,
                    chain=chain,
                    attempts=attempts,
                    selected_provider=winner_provider,
                    strategy=selected_strategy,
                )
            if last_empty is not None:
                return self._annotate(
                    last_empty,
                    request=request,
                    chain=chain,
                    attempts=attempts,
                    selected_provider=None,
                    strategy=selected_strategy,
                )
            raise self._all_failed(attempts)

        if merged:
            return self._merge(
                merged,
                request=request,
                chain=chain,
                attempts=attempts,
                strategy=selected_strategy,
            )
        if last_empty is not None:
            return self._annotate(
                last_empty,
                request=request,
                chain=chain,
                attempts=attempts,
                selected_provider=None,
                strategy=selected_strategy,
            )
        raise self._all_failed(attempts)

    def _annotate(
        self,
        result: dict[str, Any],
        *,
        request: SearchRequest,
        chain: tuple[str, ...],
        attempts: list[dict[str, Any]],
        selected_provider: str | None,
        strategy: str,
    ) -> dict[str, Any]:
        payload = dict(result)
        payload["provider"] = result.get("provider") or selected_provider or "dynamic_search"
        payload["selected_provider"] = selected_provider
        payload["strategy"] = strategy
        payload["provider_chain"] = list(chain)
        payload["attempts"] = attempts
        payload["fallback_used"] = bool(attempts and attempts[-1].get("status") == "success" and len(attempts) > 1)
        payload["fallback_reason"] = _fallback_reason(attempts)
        payload["query"] = request.query
        payload["page"] = request.pageno
        return payload

    def _merge(
        self,
        merged: list[tuple[str, dict[str, Any]]],
        *,
        request: SearchRequest,
        chain: tuple[str, ...],
        attempts: list[dict[str, Any]],
        strategy: str,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        answers: list[dict[str, Any]] = []
        answer_seen: set[str] = set()
        infoboxes: list[dict[str, Any]] = []
        suggestions: list[str] = []
        corrections: list[str] = []
        unresponsive: list[list[str]] = []
        instances: dict[str, Any] = {}

        for provider, result in merged:
            instances[provider] = result.get("instance")
            for item in result.get("results") or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                key = _url_key(url)
                if not key or key in seen:
                    continue
                seen.add(key)
                copy = dict(item)
                copy["provider"] = provider
                results.append(copy)
                if len(results) >= request.max_results:
                    break
            for answer in result.get("answers") or []:
                if not isinstance(answer, dict):
                    continue
                text = str(answer.get("answer") or "").strip()
                if text and text not in answer_seen:
                    answer_seen.add(text)
                    answers.append(answer)
            for box in result.get("infoboxes") or []:
                if isinstance(box, dict):
                    infoboxes.append(box)
            for value in result.get("suggestions") or []:
                text = str(value)
                if text and text not in suggestions:
                    suggestions.append(text)
            for value in result.get("corrections") or []:
                text = str(value)
                if text and text not in corrections:
                    corrections.append(text)
            for value in result.get("unresponsive_engines") or []:
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    unresponsive.append([str(value[0]), str(value[1])])

        return {
            "ok": True,
            "query": request.query,
            "provider": "dynamic_search",
            "selected_provider": None,
            "strategy": strategy,
            "provider_chain": list(chain),
            "providers_used": [provider for provider, _ in merged],
            "instances": instances,
            "page": request.pageno,
            "result_count": len(results),
            "number_of_results": None,
            "results": results,
            "answers": answers,
            "infoboxes": infoboxes,
            "suggestions": suggestions,
            "corrections": corrections,
            "unresponsive_engines": unresponsive,
            "attempts": attempts,
            "fallback_used": False,
            "fallback_reason": None,
        }

    def _all_failed(self, attempts: list[dict[str, Any]]) -> DynamicSearchError:
        details = []
        for attempt in attempts:
            detail = attempt.get("error") or attempt.get("reason") or attempt.get("status")
            details.append(f"{attempt['provider']}: {detail}")
        return DynamicSearchError(
            "All search providers failed or were unavailable: " + "; ".join(details)
        )

    # -- page fetch --------------------------------------------------------
    def fetch_page(self, url: str, *, max_chars: int = DEFAULT_PAGE_CHARS) -> dict[str, Any]:
        if max_chars > MAX_PAGE_CHARS:
            raise ValueError(f"max_chars must be at most {MAX_PAGE_CHARS}")
        return fetch_page(
            url,
            timeout=self.settings.searxng_timeout,
            max_chars=max_chars,
            user_agent=self.settings.searxng_user_agent,
        )

    # -- single-URL fetch with static-first rendering ----------------------
    def fetch_url(
        self,
        url: str,
        *,
        max_chars: int | None = None,
        render: str | None = None,
        session: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one model-selected URL, static first and Camoufox when needed.

        The agent decides which search results matter and passes those URLs
        here one at a time. In ``auto`` mode the page is fetched over plain HTTP
        first; Camoufox is only launched when the static HTML is a JavaScript
        shell, a challenge page, or too short to be useful. ``render="always"``
        forces Camoufox and ``render="never"`` keeps the fetch static.
        """
        target = (url or "").strip()
        if not target:
            raise ValueError("url must not be empty")

        max_chars = self.settings.fetch_max_chars if max_chars is None else max_chars
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise ValueError("max_chars must be an integer")
        if not 1 <= max_chars <= MAX_PAGE_CHARS:
            raise ValueError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}")

        render_mode = (render or self.settings.fetch_render).strip().lower()
        if render_mode not in FETCH_RENDER_MODES:
            raise ValueError(
                "render must be one of: " + ", ".join(FETCH_RENDER_MODES)
            )
        fetch_session = session or self.settings.camoufox_fetch_session or "fetch"

        page = self._fetch_one(
            rank=1,
            item={"url": target, "title": None},
            max_chars=max_chars,
            render_mode=render_mode,
            session=fetch_session,
        )
        payload = {
            "ok": page["ok"],
            "provider": "fetch",
            "url": target,
            "fetch_method": page["fetch_method"],
            "fetch_mode": render_mode,
            "fetch_session": fetch_session,
            "rendered": page["rendered"],
            "render_reason": page["render_reason"],
            "render_error": page["render_error"],
            "static_error": page["static_error"],
            "title": page.get("page_title") or page.get("title"),
            "description": page.get("description"),
            "status": page.get("status"),
            "charset": page.get("charset"),
            "text": page["text"],
            "char_count": page["char_count"],
            "truncated": page["truncated"],
            "download_truncated": page.get("download_truncated"),
            "final_url": page.get("final_url") or target,
            "content_type": page.get("content_type"),
            "link_count": page.get("link_count"),
        }
        if not page["ok"]:
            payload["error"] = page.get("error") or "fetch failed"
        return payload

    def _fetch_one(
        self,
        *,
        rank: int,
        item: dict[str, Any],
        max_chars: int,
        render_mode: str,
        session: str,
    ) -> dict[str, Any]:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip() or None
        page: dict[str, Any] = {
            "rank": rank,
            "ok": False,
            "url": url,
            "title": title,
            "fetch_method": None,
            "rendered": False,
            "static_success": False,
            "static_char_count": None,
            "static_error": None,
            "render_reason": None,
            "render_error": None,
            "text": "",
            "char_count": 0,
            "truncated": False,
            "link_count": None,
        }

        static: dict[str, Any] | None = None
        static_error: str | None = None

        if render_mode != "always":
            try:
                static = self._static_fetch(url, max_chars=max_chars)
                page["static_success"] = True
                page["static_char_count"] = static["char_count"]
                page["link_count"] = static.get("link_count")
            except Exception as exc:  # noqa: BLE001 - reported per page
                static_error = _fetch_error(exc)
                page["static_error"] = static_error

        render_reason: str | None = None
        if render_mode == "always":
            render_reason = "forced"
        elif render_mode == "auto":
            render_reason = _needs_render(static, self.settings)
        needs_render = render_reason is not None
        page["render_reason"] = render_reason

        if render_mode == "auto" and static is not None and not needs_render:
            page.update(
                {
                    "ok": True,
                    "fetch_method": "static",
                    "title": static.get("title") or page.get("title"),
                    "description": static.get("description"),
                    "status": static.get("status"),
                    "charset": static.get("charset"),
                    "text": static["text"],
                    "char_count": static["char_count"],
                    "truncated": static["truncated"],
                    "download_truncated": static.get("download_truncated"),
                    "final_url": static.get("final_url") or url,
                    "content_type": static.get("content_type"),
                }
            )
            return page

        browser_attempted = False
        if needs_render and "camoufox" in self.settings.search_providers:
            browser_attempted = True
            try:
                rendered = self.browser.open(
                    url,
                    session=session,
                    wait_until="domcontentloaded",
                    max_chars=max_chars,
                )
                page.update(
                    {
                        "ok": True,
                        "fetch_method": "camoufox",
                        "rendered": True,
                        "text": rendered["text"],
                        "char_count": rendered["char_count"],
                        "truncated": rendered["truncated"],
                        "final_url": rendered.get("url") or url,
                        "content_type": "text/plain",
                        "page_title": rendered.get("title"),
                    }
                )
                return page
            except Exception as exc:  # noqa: BLE001 - fall back to static
                page["render_error"] = _fetch_error(exc)

        # render="always" skips the static attempt up front. If the browser
        # failed, still try static HTTP rather than returning no content.
        if render_mode == "always" and static is None:
            try:
                static = self._static_fetch(url, max_chars=max_chars)
                page["static_success"] = True
                page["static_char_count"] = static["char_count"]
                page["link_count"] = static.get("link_count")
            except Exception as exc:  # noqa: BLE001 - reported per page
                static_error = _fetch_error(exc)
                page["static_error"] = static_error

        if static is not None:
            page.update(
                {
                    "ok": True,
                    "fetch_method": "static",
                    "title": static.get("title") or page.get("title"),
                    "description": static.get("description"),
                    "status": static.get("status"),
                    "charset": static.get("charset"),
                    "text": static["text"],
                    "char_count": static["char_count"],
                    "truncated": static["truncated"],
                    "download_truncated": static.get("download_truncated"),
                    "final_url": static.get("final_url") or url,
                    "content_type": static.get("content_type"),
                }
            )
            if browser_attempted:
                page["render_reason"] = "camoufox_failed_static_fallback"
            elif render_mode == "always":
                page["render_reason"] = "camoufox_unavailable_static_fallback"
            return page

        page["error"] = static_error or page.get("render_error") or "fetch failed"
        if static_error and page.get("render_error"):
            page["error"] = f"static: {static_error}; camoufox: {page['render_error']}"
        return page

    def _static_fetch(self, url: str, *, max_chars: int) -> dict[str, Any]:
        result = self.fetch_page(url, max_chars=max_chars)
        text = result.get("text") or ""
        result["link_count"] = len(_MARKDOWN_LINK.findall(text))
        return result

    def shutdown(self) -> None:
        shutdown = getattr(self.browser, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _needs_render(
    static: dict[str, Any] | None, settings: Settings
) -> str | None:
    """Return a reason to render, or None when static text is good enough."""
    if static is None:
        return "static_error"
    content_type = str(static.get("content_type") or "").lower()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return None
    text = str(static.get("text") or "")
    total_chars = int(static.get("char_count") or len(text))
    lower = text[:5000].lower()
    if any(marker in lower for marker in _JS_MARKERS):
        return "javascript_shell"
    if total_chars < settings.fetch_min_chars:
        return "short_html"
    if (static.get("link_count") or 0) >= 20 and total_chars < 2000:
        return "link_heavy_shell"
    return None


def _fetch_error(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message[:1000]


def _fallback_reason(attempts: list[dict[str, Any]]) -> str | None:
    if len(attempts) <= 1:
        return None
    prior = attempts[:-1]
    statuses = {attempt.get("status") for attempt in prior}
    if "error" in statuses:
        return "provider_error"
    if "empty" in statuses:
        return "no_results"
    if "skipped" in statuses:
        return "unavailable"
    return None


def _url_key(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, "")
    )


def dedupe_results(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Small helper used by tests and callers that merge result lists."""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in results:
        key = _url_key(str(item.get("url") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output