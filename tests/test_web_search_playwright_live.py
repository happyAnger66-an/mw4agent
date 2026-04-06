"""Live Playwright web_search (opt-in).

Uses proxy ``http://127.0.0.1:7890`` as in docs. Requires:

- ``pip install -e '.[playwright]'`` (or ``orbit[playwright]``)
- ``playwright install chromium``
- A proxy listening on 127.0.0.1:7890 (typical Clash **HTTP** mixed port)

If the test fails with timeouts or empty results, try:

- Ensure the proxy is **HTTP** on 7890 (not SOCKS-only); or set
  ``ORBIT_WEB_SEARCH_LIVE_PROXY=socks5://127.0.0.1:7890`` if your client exposes SOCKS there.
- DuckDuckGo may block headless; implementation falls back to Bing automatically. This test uses **Bing** directly for stability.

Run::

    ORBIT_WEB_SEARCH_LIVE=1 pytest tests/test_web_search_playwright_live.py -v

CI: skipped unless the env var is set (and Playwright is installed).
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.asyncio

_LIVE_PROXY = os.getenv("ORBIT_WEB_SEARCH_LIVE_PROXY", "http://127.0.0.1:7890").strip()


def _live_enabled() -> bool:
    return os.getenv("ORBIT_WEB_SEARCH_LIVE", "").strip() == "1"


@pytest.mark.skipif(not _live_enabled(), reason="set ORBIT_WEB_SEARCH_LIVE=1 for live Playwright web_search")
async def test_playwright_web_search_through_proxy_7890(monkeypatch):
    pytest.importorskip("playwright.async_api")

    from orbit.agents.tools.web_search_tool import WebSearchTool

    def _cfg(section, default=None):
        if section == "tools":
            return {
                "web": {
                    "search": {
                        "enabled": True,
                        "provider": "playwright",
                        "proxy": _LIVE_PROXY,
                        "timeoutSeconds": 90,
                        "playwright": {
                            "headless": True,
                            "browser": "chromium",
                            # Bing SERP is more stable than DDG for headless + proxy (especially in CN).
                            "searchUrlTemplate": "https://www.bing.com/search?q={query}",
                            "timeoutMs": 60000,
                        },
                    }
                }
            }
        return default

    monkeypatch.setattr("orbit.agents.tools.web_search_tool.read_root_section", _cfg)
    # Avoid cross-test cache pollution
    monkeypatch.setattr("orbit.agents.tools.web_search_tool._CACHE", {})

    tool = WebSearchTool()
    res = await tool.execute(
        "live_pw1",
        {"query": "orbit agent python", "count": 3},
    )
    assert res.success is True, res.result
    if res.result.get("error") == "web_search_failed":
        pytest.fail(
            "web_search_failed: "
            + str(res.result.get("message"))
            + " — check proxy (HTTP vs socks5), ORBIT_WEB_SEARCH_LIVE_PROXY, and playwright install."
        )
    assert res.result.get("provider") == "playwright"
    results = res.result.get("results") or []
    assert len(results) >= 1, (
        "expected at least one SERP row; "
        "empty results usually mean blocked page, wrong proxy scheme, or selector drift. Full payload: "
        + repr(res.result)
    )
    for row in results:
        assert row.get("url") or row.get("title")
