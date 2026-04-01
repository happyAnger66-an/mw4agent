"""router_llm：解析与 transcript 辅助函数单测。"""

from mw4agent.gateway.orchestrator import (
    OrchMessage,
    _build_router_llm_user_prompt,
    _format_transcript_since_last_user,
    _last_user_message_text,
    _parse_router_agent_pick,
    _patch_router_agent_roles,
)


def test_parse_router_agent_pick_json() -> None:
    parts = ["a", "b"]
    assert _parse_router_agent_pick('{"next_agent":"b"}', parts) == "b"
    assert _parse_router_agent_pick("```json\n{\"next_agent\": \"a\"}\n```", parts) == "a"


def test_parse_router_agent_pick_first_line() -> None:
    parts = ["x", "y"]
    assert _parse_router_agent_pick("y", parts) == "y"


def test_parse_router_agent_pick_invalid() -> None:
    parts = ["a", "b"]
    assert _parse_router_agent_pick('{"next_agent":"z"}', parts) is None
    assert _parse_router_agent_pick("", parts) is None


def test_last_user_and_transcript() -> None:
    msgs = [
        OrchMessage("1", 0, 0, "user", "user", "old"),
        OrchMessage("2", 0, 1, "u2", "user", "latest goal"),
        OrchMessage("3", 0, 1, "agent1", "assistant", "out1"),
    ]
    assert _last_user_message_text(msgs) == "latest goal"
    t = _format_transcript_since_last_user(msgs, 10000)
    assert "[user]" in t
    assert "latest goal" in t
    assert "[agent1]" in t
    assert "out1" in t


def test_build_router_prompt_includes_agent_roles() -> None:
    p = _build_router_llm_user_prompt(
        participants=["a", "b"],
        original_user="hi",
        transcript="(empty)",
        last_immediate="hi",
        turn_1based=1,
        max_turns=3,
        agent_roles={"a": "coder", "b": "reviewer"},
    )
    assert "Agent identity" in p
    assert "- a: coder" in p
    assert "- b: reviewer" in p


def test_patch_router_agent_roles_merge() -> None:
    old = {"a": "old"}
    merged = _patch_router_agent_roles(old, {"a": "new", "b": "y"}, ["a", "b"])
    assert merged == {"a": "new", "b": "y"}
    cleared = _patch_router_agent_roles(merged, {"b": ""}, ["a", "b"])
    assert cleared == {"a": "new"}
