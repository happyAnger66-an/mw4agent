"""Tests for configurable LLM provider/model via encrypted config."""

from __future__ import annotations

from pathlib import Path

from mw4agent.agents.types import AgentRunParams
from mw4agent.config import ConfigManager, get_default_config_manager
from mw4agent.llm.backends import (
    _call_openai_chat_with_tools,
    _extract_text_and_reasoning_from_message,
    _normalize_thinking_level,
    _thinking_extra_body,
    generate_reply,
)


def test_llm_config_provider_and_model_precedence(monkeypatch, tmp_path: Path) -> None:
    """Config-driven provider/model should be used when params/env are not set."""
    # Isolate config directory
    cfg_dir = tmp_path / "config"
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / "mw_state"))

    # Reset default config manager singleton
    import mw4agent.config.manager as cfg_mod

    cfg_mod._default_config_manager = None  # type: ignore[attr-defined]

    # Ensure env does not override
    monkeypatch.delenv("MW4AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MW4AGENT_LLM_MODEL", raising=False)

    mgr: ConfigManager = get_default_config_manager()
    mgr.write_config(
        "llm",
        {
            "provider": "echo",
            "model": "test-model-from-config",
        },
    )

    text, provider, model, usage = generate_reply(AgentRunParams(message="hi"))
    assert provider == "echo"
    assert model == "test-model-from-config"


def test_thinking_level_normalize_and_provider_mapping() -> None:
    assert _normalize_thinking_level("on") == "medium"
    assert _normalize_thinking_level("OFF") == "off"
    assert _normalize_thinking_level("xhigh") == "xhigh"
    assert _normalize_thinking_level("weird") == "off"

    assert _thinking_extra_body("openai", "off") == {}
    assert _thinking_extra_body("openai", "high") == {"reasoning_effort": "high"}
    assert _thinking_extra_body("deepseek", "minimal") == {"reasoning_effort": "low"}
    assert _thinking_extra_body("vllm", "xhigh") == {"reasoning": {"effort": "high"}}
    assert _thinking_extra_body("aliyun-bailian", "adaptive") == {"reasoning": {"effort": "medium"}}


def test_qwen_reasoning_content_extraction() -> None:
    msg = {"content": "final", "reasoning_content": "step by step"}
    visible, reasoning = _extract_text_and_reasoning_from_message(msg)
    assert visible == "final"
    assert reasoning == "step by step"

    visible2, reasoning2 = _extract_text_and_reasoning_from_message(
        {"content": "final2"},
        choice={"reasoning_content": "trace2"},
    )
    assert visible2 == "final2"
    assert reasoning2 == "trace2"


def test_qwen_tool_calls_with_reasoning_content_payload(monkeypatch) -> None:
    import json

    payload = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "好的，继续执行。",
                    "reasoning_content": "继续执行步骤 8-11。",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "exec",
                                "arguments": "{\"command\":\"echo ok\"}",
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=60.0: _Resp(),
    )

    content, tool_calls, usage = _call_openai_chat_with_tools(
        messages=[{"role": "user", "content": "继续"}],
        tools=[{"type": "function", "function": {"name": "exec", "parameters": {"type": "object"}}}],
        model="qwen3.5-plus",
        api_key="x",
        base_url="https://example.com",
    )
    assert content is not None and "<think>继续执行步骤 8-11。</think>" in content
    assert "好的，继续执行。" in content
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "exec"
    assert tool_calls[0]["arguments"] == {"command": "echo ok"}
    assert usage.total_tokens == 13

