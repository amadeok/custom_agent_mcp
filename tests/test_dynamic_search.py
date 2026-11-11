from __future__ import annotations

import pytest

from custom_agent_mcp.dynamic_search import (
    DynamicSearchService,
    SearchRequest,
    normalize_provider_chain,
)
from custom_agent_mcp.exceptions import DynamicSearchError, TavilyError
from custom_agent_mcp.settings import Settings


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        **overrides,
    )


def _payload(provider: str, results: list[dict] | None = None) -> dict:
    results = results or []
    return {
        "ok": True,
        "query": "q",
        "provider": provider,
        "instance": f"{provider}.test",
        "page": 1,
        "result_count": len(results),
        "number_of_results": len(results),
        "results": results,
        "answers": [],
        "infoboxes": [],
        "suggestions": [],
        "corrections": [],
        "unresponsive_engines": [],
    }


def _result(url: str, title: str = "Title") -> dict:
    return {
        "title": title,
        "url": url,
        "snippet": "snippet",
        "engine": "test",
        "engines": ["test"],
        "score": 1.0,
        "category": None,
        "published_date": None,
        "thumbnail": None,
    }


def test_normalize_provider_chain_preserves_order():
    assert normalize_provider_chain("camoufox,searxng") == ("camoufox", "searxng")
    assert normalize_provider_chain(["tavily"]) == ("tavily",)
    with pytest.raises(ValueError, match="at least one"):
        normalize_provider_chain([])
    with pytest.raises(ValueError, match="unknown"):
        normalize_provider_chain(["nope"])
    with pytest.raises(ValueError, match="more than once"):
        normalize_provider_chain(["searxng", "searxng"])


def test_search_request_validates_common_parameters():
    request = SearchRequest(query="  hello  ", max_results=5)
    assert request.query == "hello"

    with pytest.raises(ValueError, match="query"):
        SearchRequest(query=" ")
    with pytest.raises(ValueError, match="max_results"):
        SearchRequest(query="x", max_results=51)
    with pytest.raises(ValueError, match="safesearch"):
        SearchRequest(query="x", safesearch=3)
    with pytest.raises(ValueError, match="time_range"):
        SearchRequest(query="x", time_range="decade")


def test_first_strategy_falls_back_in_configured_order(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))
    calls = []

    def fake(provider, request, **kwargs):
        calls.append(provider)
        return _payload(provider, [] if provider == "searxng" else [_result("https://ok.test")])

    monkeypatch.setattr(service, "provider_availability", lambda provider: (True, None))
    monkeypatch.setattr(service, "search_provider", fake)

    result = service.search_combined(SearchRequest(query="q"))

    assert calls == ["searxng", "tavily"]
    assert result["selected_provider"] == "tavily"
    assert result["provider_chain"] == [
        "searxng",
        "tavily",
        "tinyfish",
        "camoufox",
    ]
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "no_results"
    assert [attempt["status"] for attempt in result["attempts"]] == ["empty", "success"]


def test_first_strategy_stops_at_first_success(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))
    calls = []

    def fake(provider, request, **kwargs):
        calls.append(provider)
        return _payload(provider, [_result(f"https://{provider}.test")])

    monkeypatch.setattr(service, "provider_availability", lambda provider: (True, None))
    monkeypatch.setattr(service, "search_provider", fake)

    result = service.search_combined(SearchRequest(query="q"))

    assert calls == ["searxng"]
    assert result["selected_provider"] == "searxng"
    assert result["fallback_used"] is False


def test_unavailable_provider_is_skipped(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))

    def available(provider):
        if provider == "tavily":
            return False, "no key"
        return True, None

    monkeypatch.setattr(service, "provider_availability", available)

    def fake(provider, request, **kwargs):
        if provider == "searxng":
            return _payload(provider, [])
        return _payload(provider, [_result("https://ok.test")])

    monkeypatch.setattr(service, "search_provider", fake)

    result = service.search_combined(SearchRequest(query="q"))

    assert result["selected_provider"] == "tinyfish"
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "empty",
        "skipped",
        "success",
    ]


def test_filters_skip_providers_that_cannot_honor_them(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))
    calls = []

    def fake(provider, request, **kwargs):
        calls.append(provider)
        return _payload(provider, [])

    monkeypatch.setattr(service, "provider_availability", lambda provider: (True, None))
    monkeypatch.setattr(service, "search_provider", fake)

    result = service.search_combined(
        SearchRequest(query="q", categories=("news",))
    )

    assert calls == ["searxng"]
    assert result["selected_provider"] is None
    statuses = {attempt["provider"]: attempt["status"] for attempt in result["attempts"]}
    assert statuses == {
        "searxng": "empty",
        "tavily": "skipped",
        "tinyfish": "skipped",
        "camoufox": "skipped",
    }


def test_per_call_provider_override_changes_order(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))
    calls = []

    def fake(provider, request, **kwargs):
        calls.append(provider)
        return _payload(provider, [] if provider == "tavily" else [_result("https://ok.test")])

    monkeypatch.setattr(service, "provider_availability", lambda provider: (True, None))
    monkeypatch.setattr(service, "search_provider", fake)

    result = service.search_combined(
        SearchRequest(query="q"), providers=["tavily", "searxng"]
    )

    assert calls == ["tavily", "searxng"]
    assert result["selected_provider"] == "searxng"
    assert result["provider_chain"] == ["tavily", "searxng"]


def test_merge_strategy_dedupes_and_marks_sources(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))

    def fake(provider, request, **kwargs):
        if provider == "searxng":
            return _payload(
                provider,
                [
                    _result("https://one.test/page"),
                    _result("https://shared.test/"),
                ],
            )
        return _payload(
            provider,
            [
                _result("https://shared.test"),
                _result("https://two.test"),
            ],
        )

    monkeypatch.setattr(service, "provider_availability", lambda provider: (True, None))
    monkeypatch.setattr(service, "search_provider", fake)

    result = service.search_combined(
        SearchRequest(query="q", max_results=10),
        providers=["searxng", "tavily"],
        strategy="merge",
    )

    assert result["provider"] == "dynamic_search"
    assert result["providers_used"] == ["searxng", "tavily"]
    assert [item["url"] for item in result["results"]] == [
        "https://one.test/page",
        "https://shared.test/",
        "https://two.test",
    ]
    assert [item["provider"] for item in result["results"]] == [
        "searxng",
        "searxng",
        "tavily",
    ]


def test_all_provider_errors_raise_combined_error(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))
    monkeypatch.setattr(service, "provider_availability", lambda provider: (True, None))

    def fail(provider, request, **kwargs):
        raise TavilyError(f"{provider} down")

    monkeypatch.setattr(service, "search_provider", fail)

    with pytest.raises(DynamicSearchError, match="All search providers failed"):
        service.search_combined(SearchRequest(query="q"), providers=["searxng", "tavily"])


def test_all_providers_skipped_raises(monkeypatch, tmp_path):
    service = DynamicSearchService(_settings(tmp_path))
    monkeypatch.setattr(
        service,
        "provider_availability",
        lambda provider: (False, "not configured"),
    )

    with pytest.raises(DynamicSearchError, match="not configured"):
        service.search_combined(SearchRequest(query="q"))


def _static_page(text: str, content_type: str = "text/html") -> dict:
    return {
        "ok": True,
        "url": "https://example.test",
        "final_url": "https://example.test",
        "status": 200,
        "content_type": content_type,
        "title": "Example",
        "description": None,
        "text": text,
        "char_count": len(text),
        "truncated": False,
        "download_truncated": False,
        "link_count": 0,
    }


def test_fetch_url_auto_renders_javascript_shell(monkeypatch, tmp_path):
    service = DynamicSearchService(
        _settings(tmp_path, search_providers=("searxng", "camoufox"))
    )
    monkeypatch.setattr(
        service,
        "_static_fetch",
        lambda url, max_chars: _static_page("Enable JavaScript to view this site"),
    )
    monkeypatch.setattr(
        service.browser,
        "open",
        lambda url, **kwargs: {
            "title": "Rendered",
            "url": url,
            "text": "Rendered page content",
            "char_count": 21,
            "truncated": False,
        },
    )

    result = service.fetch_url("https://example.test/page")

    assert result["ok"] is True
    assert result["fetch_method"] == "camoufox"
    assert result["render_reason"] == "javascript_shell"
    assert result["text"] == "Rendered page content"


def test_fetch_url_auto_keeps_good_static_page(monkeypatch, tmp_path):
    service = DynamicSearchService(
        _settings(tmp_path, search_providers=("searxng", "camoufox"))
    )
    text = "Useful static content. " * 40
    monkeypatch.setattr(
        service,
        "_static_fetch",
        lambda url, max_chars: _static_page(text),
    )
    monkeypatch.setattr(
        service.browser,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Camoufox must not launch for a good static page")
        ),
    )

    result = service.fetch_url("https://example.test/page")

    assert result["fetch_method"] == "static"
    assert result["render_reason"] is None
    assert result["text"] == text


def test_fetch_url_never_skips_camoufox(monkeypatch, tmp_path):
    service = DynamicSearchService(
        _settings(tmp_path, search_providers=("searxng", "camoufox"))
    )
    monkeypatch.setattr(
        service,
        "_static_fetch",
        lambda url, max_chars: _static_page("Enable JavaScript"),
    )
    monkeypatch.setattr(
        service.browser,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("render='never' must not launch Camoufox")
        ),
    )

    result = service.fetch_url("https://example.test/page", render="never")

    assert result["fetch_method"] == "static"
    assert result["render_reason"] is None


def test_fetch_url_always_renders_and_falls_back(monkeypatch, tmp_path):
    service = DynamicSearchService(
        _settings(tmp_path, search_providers=("searxng", "camoufox"))
    )
    text = "Useful static content. " * 40
    monkeypatch.setattr(
        service,
        "_static_fetch",
        lambda url, max_chars: _static_page(text),
    )
    monkeypatch.setattr(
        service.browser,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("browser is unavailable")
        ),
    )

    result = service.fetch_url("https://example.test/page", render="always")

    assert result["fetch_method"] == "static"
    assert result["render_reason"] == "camoufox_failed_static_fallback"
    assert "browser is unavailable" in result["render_error"]


def test_fetch_url_without_camoufox_uses_static(monkeypatch, tmp_path):
    service = DynamicSearchService(
        _settings(tmp_path, search_providers=("searxng",))
    )
    monkeypatch.setattr(
        service,
        "_static_fetch",
        lambda url, max_chars: _static_page("Enable JavaScript"),
    )

    result = service.fetch_url("https://example.test/page")

    assert result["fetch_method"] == "static"
    assert result["render_reason"] == "javascript_shell"


def test_tavily_without_key_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    service = DynamicSearchService(_settings(tmp_path))

    available, reason = service.provider_availability("tavily")

    assert available is False
    assert "TAVILY_API_KEY" in reason