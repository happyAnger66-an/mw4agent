from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_web_fetch_disabled_by_default(monkeypatch, tmp_path):
    from mw4agent.agents.tools.web_fetch_tool import WebFetchTool

    # No tools.web.fetch.enabled config -> disabled (safe default)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    tool = WebFetchTool()
    res = await tool.execute("tc1", {"url": "https://example.com"})
    assert res.success is False
    assert res.error


@pytest.mark.asyncio
async def test_web_fetch_ssrf_blocks_private_ip(monkeypatch, tmp_path):
    from mw4agent.agents.tools.web_fetch_tool import WebFetchTool
    from mw4agent.config.root import write_root_config

    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    write_root_config({"tools": {"web": {"fetch": {"enabled": True}}}})

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # Map to private IP
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)

    tool = WebFetchTool()
    res = await tool.execute("tc2", {"url": "https://example.com"})
    assert res.success is True
    assert res.result["error"] == "ssrf_blocked"


@pytest.mark.asyncio
async def test_web_fetch_fetches_and_wraps(monkeypatch, tmp_path):
    from mw4agent.agents.tools.web_fetch_tool import WebFetchTool
    from mw4agent.config.root import write_root_config

    write_root_config({"tools": {"web": {"fetch": {"enabled": True, "cacheTtlMinutes": 5}}}})

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)

    html_doc = "<html><title>t</title><body><h1>Hello</h1><p>World</p></body></html>"

    class _Resp:
        status = 200

        def __init__(self):
            self.headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, n=-1):
            b = html_doc.encode("utf-8")
            return b if n < 0 else b[:n]

    calls = SimpleNamespace(n=0)

    def fake_urlopen(req, timeout=0):
        calls.n += 1
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    tool = WebFetchTool()
    res1 = await tool.execute("tc3", {"url": "https://example.com", "extractMode": "markdown", "maxChars": 200})
    assert res1.success is True
    assert res1.result["cache"]["hit"] is False
    assert "EXTERNAL_UNTRUSTED_CONTENT" in res1.result["text"]

    res2 = await tool.execute("tc4", {"url": "https://example.com", "extractMode": "markdown", "maxChars": 200})
    assert res2.success is True
    assert res2.result["cache"]["hit"] is True
    assert calls.n == 1

