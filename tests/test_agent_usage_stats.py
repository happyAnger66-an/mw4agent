"""Tests for per-agent LLM usage stats persistence."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mw4agent.llm.backends import LLMUsage


@pytest.fixture
def isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mw_state"
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(root))
    return root


def test_apply_llm_usage_accumulates(isolated_state_dir: Path) -> None:
    from mw4agent.agents.stats.agent_usage import apply_llm_usage, get_agent_stats_path, load_agent_stats

    apply_llm_usage(
        "main",
        LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        "openai",
        "gpt-4o-mini",
    )
    st = load_agent_stats("main")
    assert st["llmUsage"]["promptTokensTotal"] == 10
    assert st["llmUsage"]["completionTokensTotal"] == 5
    assert st["llmUsage"]["totalTokensTotal"] == 15
    assert st["llmUsage"]["numRequests"] == 1
    assert "openai/gpt-4o-mini" in (st.get("byProviderModel") or {})
    b = st["byProviderModel"]["openai/gpt-4o-mini"]
    assert b["promptTokensTotal"] == 10

    apply_llm_usage(
        "main",
        LLMUsage(input_tokens=2, output_tokens=1, total_tokens=3),
        "deepseek",
        "deepseek-chat",
    )
    st2 = load_agent_stats("main")
    assert st2["llmUsage"]["promptTokensTotal"] == 12
    assert st2["llmUsage"]["numRequests"] == 2

    p = get_agent_stats_path("main")
    assert p.is_file()
    assert str(p).endswith("stats.json")


def test_apply_llm_usage_concurrent(isolated_state_dir: Path) -> None:
    from mw4agent.agents.stats.agent_usage import apply_llm_usage, load_agent_stats

    n = 40
    barrier = threading.Barrier(n)

    def worker() -> None:
        barrier.wait()
        apply_llm_usage(
            "main",
            LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            "openai",
            "x",
        )

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    st = load_agent_stats("main")
    assert st["llmUsage"]["numRequests"] == n
    assert st["llmUsage"]["promptTokensTotal"] == n


def test_apply_skips_empty_usage(isolated_state_dir: Path) -> None:
    from mw4agent.agents.stats.agent_usage import apply_llm_usage, load_agent_stats

    apply_llm_usage("main", LLMUsage(), "openai", "m")
    st = load_agent_stats("main")
    assert st["llmUsage"]["numRequests"] == 0
