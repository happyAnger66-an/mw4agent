from __future__ import annotations

from pathlib import Path

from mw4agent.agents.session.transcript import (
    append_messages,
    build_messages_from_leaf,
    resolve_session_transcript_path,
)
from mw4agent.agents.runner.runner import _auto_compact_if_needed


def test_auto_compaction_rewrites_leaf_chain(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    session_id = "sess_compact_001"
    transcript = resolve_session_transcript_path(agent_id="main", session_id=session_id)

    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
        {"role": "user", "content": "u5"},
        {"role": "assistant", "content": "a5"},
    ]
    append_messages(transcript_file=transcript, session_id=session_id, cwd=str(tmp_path), messages=msgs)
    history = build_messages_from_leaf(transcript_file=transcript)
    assert [m.get("content") for m in history][-2:] == ["u5", "a5"]

    root_cfg = {
        "session": {
            "compaction": {
                "enabled": True,
                "keepTurns": 2,
                "triggerTurns": 4,
                "summaryMaxChars": 2000,
            }
        }
    }
    new_history = _auto_compact_if_needed(
        history_messages=history,
        root_cfg=root_cfg,
        transcript_file=transcript,
        transcript_session_id=session_id,
        transcript_cwd=str(tmp_path),
    )

    assert new_history[0].get("role") == "system"
    assert "Session compaction summary (auto)" in (new_history[0].get("content") or "")
    # Keep last 2 user turns: u4/a4/u5/a5 should remain after the compaction summary.
    tail = [m.get("content") for m in new_history[1:]]
    assert tail == ["u4", "a4", "u5", "a5"]

