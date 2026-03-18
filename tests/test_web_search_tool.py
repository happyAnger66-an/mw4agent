from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_web_search_missing_api_key(monkeypatch):
    from mw4agent.agents.tools.web_search_tool import WebSearchTool

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    tool = WebSearchTool()
    res = await tool.execute("tc1", {"query": "hello"})
    assert res.success is True
    assert isinstance(res.result, dict)
    assert res.result.get("error") == "missing_brave_api_key"


@pytest.mark.asyncio
async def test_web_search_brave_parsing_and_wrapping(monkeypatch):
    from mw4agent.agents.tools.web_search_tool import WebSearchTool

    monkeypatch.setenv("BRAVE_API_KEY", "k_test")

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
    from mw4agent.agents.tools.web_search_tool import WebSearchTool

    monkeypatch.setenv("BRAVE_API_KEY", "k_test")

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

