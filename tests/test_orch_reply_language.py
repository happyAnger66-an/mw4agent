"""Orchestration reply language normalization."""

from mw4agent.gateway.orchestrator import _normalize_orch_reply_language


def test_normalize_orch_reply_language_maps_synonyms() -> None:
    assert _normalize_orch_reply_language(None) == "auto"
    assert _normalize_orch_reply_language("") == "auto"
    assert _normalize_orch_reply_language("  ") == "auto"
    assert _normalize_orch_reply_language("ZH") == "zh"
    assert _normalize_orch_reply_language("zh-CN") == "zh"
    assert _normalize_orch_reply_language("中文") == "zh"
    assert _normalize_orch_reply_language("EN") == "en"
    assert _normalize_orch_reply_language("english") == "en"
    assert _normalize_orch_reply_language("auto") == "auto"
    assert _normalize_orch_reply_language("unknown") == "auto"
