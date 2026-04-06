from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_web_search_missing_api_key(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    def _cfg(section, default=None):
        # Force empty tools so web_search stays disabled regardless of host ~/.orbit.
        if section == "tools":
            return {}
        return default

    monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)

    tool = WebSearchTool()
    res = await tool.execute("tc1", {"query": "hello"})
    assert res.success is False
    assert res.result.get("error") == "disabled"


@pytest.mark.asyncio
async def test_web_search_brave_parsing_and_wrapping(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    monkeypatch.setenv("BRAVE_API_KEY", "k_test")
    monkeypatch.setattr(
        "orbit.agents.tools.web_search_tool.read_root_section",
        lambda section, default=None: {"web": {"search": {"enabled": True}}} if section == "tools" else default,
    )

    payload = {
        "web": {
            "results": [
                {"title": "t1", "url": "https://example.com/1", "description": "d1", "age": "2026-03-17"},
                {"title": "t2", "url": "https://example.com/2", "description": "d2"},
            ]
        }
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    tool = WebSearchTool()
    res = await tool.execute("tc2", {"query": "q", "count": 2})
    assert res.success is True

    data = res.result
    assert data["provider"] == "brave"
    assert data["count"] == 2
    assert data["cache"]["hit"] is False
    assert len(data["results"]) == 2

    r0 = data["results"][0]
    assert r0["url"] == "https://example.com/1"
    assert "EXTERNAL_UNTRUSTED_CONTENT" in r0["title"]
    assert "EXTERNAL_UNTRUSTED_CONTENT" in r0["description"]


@pytest.mark.asyncio
async def test_web_search_cache_hit(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    monkeypatch.setenv("BRAVE_API_KEY", "k_test")
    monkeypatch.setattr(
        "orbit.agents.tools.web_search_tool.read_root_section",
        lambda section, default=None: {"web": {"search": {"enabled": True}}} if section == "tools" else default,
    )

    payload = {"web": {"results": [{"title": "t1", "url": "https://example.com/1", "description": "d1"}]}}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    calls = SimpleNamespace(n=0)

    def fake_urlopen(req, timeout=0):
        calls.n += 1
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    tool = WebSearchTool()
    res1 = await tool.execute("tc3", {"query": "q", "count": 1})
    res2 = await tool.execute("tc4", {"query": "q", "count": 1})
    assert res1.success is True and res2.success is True
    assert calls.n == 1
    assert res2.result["cache"]["hit"] is True


@pytest.mark.asyncio
async def test_web_search_perplexity_missing_key(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    # Force provider selection to perplexity (via config).
    def _cfg(section, default=None):
        if section == "tools":
            return {"web": {"search": {"enabled": True, "provider": "perplexity"}}}
        return default

    monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)

    tool = WebSearchTool()
    res = await tool.execute("tc_p0", {"query": "q"})
    assert res.success is True
    assert res.result.get("error") == "missing_perplexity_api_key"


@pytest.mark.asyncio
async def test_web_search_perplexity_parsing(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    def _cfg(section, default=None):
        if section == "tools":
            return {
                "web": {"search": {"enabled": True, "provider": "perplexity", "perplexity": {"apiKey": "pplx_test"}}}
            }
        return default

    monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)

    payload = {
        "content": "answer text",
        "citations": ["https://c1.example", "https://c2.example"],
        "results": [{"title": "t1", "url": "https://r1.example", "snippet": "s1"}],
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    tool = WebSearchTool()
    res = await tool.execute("tc_p1", {"query": "q", "count": 3, "language": "en"})
    assert res.success is True
    data = res.result
    assert data["provider"] == "perplexity"
    assert "EXTERNAL_UNTRUSTED_CONTENT" in (data.get("content") or "")
    assert data.get("citations") == ["https://c1.example", "https://c2.example"]
    assert data.get("results") and data["results"][0]["url"] == "https://r1.example"


@pytest.mark.asyncio
async def test_web_search_serper_parsing(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    def _cfg(section, default=None):
        if section == "tools":
            return {
                "web": {
                    "search": {
                        "enabled": True,
                        "provider": "serper",
                        "proxy": "http://127.0.0.1:9",
                        "serper": {"apiKey": "serper_test_key"},
                    }
                }
            }
        return default

    monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)

    api_payload = {
        "organic": [
            {"title": "T1", "link": "https://a.example", "snippet": "S1"},
            {"title": "T2", "link": "https://b.example", "snippet": "S2"},
        ]
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(api_payload).encode("utf-8")

    opens = []

    def fake_open(req, *, timeout=0, proxy=None):
        opens.append((req, proxy))
        return _Resp()

    monkeypatch.setattr("orbit.agents.tools.web_search_tool._urlopen", fake_open)

    tool = WebSearchTool()
    res = await tool.execute(
        "tc_s1",
        {"query": "orbit", "count": 5, "gl": "cn", "hl": "zh-cn", "page": 2},
    )
    assert res.success is True
    data = res.result
    assert data["provider"] == "serper"
    assert data["count"] == 2
    assert data["results"][0]["url"] == "https://a.example"
    assert "EXTERNAL_UNTRUSTED_CONTENT" in (data["results"][0].get("title") or "")
    assert opens
    req0, proxy0 = opens[0]
    assert proxy0 == "http://127.0.0.1:9"
    hdrs = {k: v for k, v in req0.header_items()}
    assert hdrs.get("X-api-key") == "serper_test_key"


def test_playwright_proxy_dict_with_credentials():
    from orbit.agents.tools import web_search_tool as w

    d = w._build_playwright_proxy_dict("http://user:secret@127.0.0.1:8888", {})
    assert d["server"] == "http://127.0.0.1:8888"
    assert d["username"] == "user"
    assert d["password"] == "secret"

    d2 = w._build_playwright_proxy_dict("http://127.0.0.1:7890", {"proxyUsername": "u1", "proxyPassword": "p1"})
    assert d2["server"] == "http://127.0.0.1:7890"
    assert d2["username"] == "u1"
    assert d2["password"] == "p1"


def test_playwright_google_url_and_fallback_config():
    from orbit.agents.tools import web_search_tool as w

    assert w._playwright_url_is_google_search("https://www.google.com/search?q=test+query")
    assert w._playwright_url_is_google_search("https://www.google.co.jp/search?q=a")
    assert not w._playwright_url_is_google_search("https://www.bing.com/search?q=x")
    assert not w._playwright_fallback_bing_on_google_failure({})
    assert not w._playwright_fallback_bing_on_google_failure({"fallbackToBingOnGoogleFailure": False})
    assert w._playwright_fallback_bing_on_google_failure({"fallbackToBingOnGoogleFailure": True})
    assert w._playwright_fallback_bing_on_google_failure({"fallback_to_bing_on_google_failure": "true"})


def test_normalize_search_result_url_ddg_redirect():
    from orbit.agents.tools import web_search_tool as w

    href = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpath"
    assert w._normalize_search_result_url(href) == "https://example.com/path"
    assert w._normalize_search_result_url("https://direct.example/x") == "https://direct.example/x"


@pytest.mark.asyncio
async def test_web_search_playwright_mocked(monkeypatch):
    from orbit.agents.tools.web_search_tool import WebSearchTool

    captured: dict = {}

    async def fake_impl(*, cfg, query, count, language, hl, timeout_s, proxy_url):
        captured.update(
            {"query": query, "count": count, "proxy_url": proxy_url, "hl": hl, "language": language}
        )
        return {
            "query": query,
            "provider": "playwright",
            "count": 1,
            "results": [
                {
                    "title": "x",
                    "url": "https://r.example",
                    "description": "",
                    "published": None,
                }
            ],
            "externalContent": {
                "untrusted": True,
                "source": "web_search",
                "provider": "playwright",
                "wrapped": True,
            },
        }

    def _cfg(section, default=None):
        if section == "tools":
            return {
                "web": {
                    "search": {
                        "enabled": True,
                        "provider": "playwright",
                        "proxy": "http://127.0.0.1:7890",
                        "playwright": {"headless": True, "browser": "chromium"},
                    }
                }
            }
        return default

    monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)
    monkeypatch.setattr("orbit.agents.tools.web_search_tool._playwright_search_impl", fake_impl)

    tool = WebSearchTool()
    res = await tool.execute("tc_pw1", {"query": "hello world", "count": 3, "language": "zh"})
    assert res.success is True
    assert res.result["provider"] == "playwright"
    assert res.result["count"] == 1
    assert captured["proxy_url"] == "http://127.0.0.1:7890"
    assert captured["query"] == "hello world"
    assert captured["count"] == 3


@pytest.mark.asyncio
async def test_web_search_playwright_import_error_surfaces(monkeypatch):
    """Simulate missing/broken Playwright so behavior does not depend on CI having browsers."""
    import sys
    import types

    from orbit.agents.tools.web_search_tool import WebSearchTool

    removed = {}
    for key in list(sys.modules):
        if key == "playwright" or key.startswith("playwright."):
            removed[key] = sys.modules.pop(key)

    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.async_api"] = types.ModuleType("playwright.async_api")

    def _cfg(section, default=None):
        if section == "tools":
            return {"web": {"search": {"enabled": True, "provider": "playwright"}}}
        return default

    try:
        monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)
        tool = WebSearchTool()
        res = await tool.execute("tc_pw2", {"query": "q"})
        assert res.success is True
        assert res.result.get("error") == "web_search_failed"
        assert "playwright" in (res.result.get("message") or "").lower()
    finally:
        sys.modules.pop("playwright", None)
        sys.modules.pop("playwright.async_api", None)
        sys.modules.update(removed)

