from __future__ import annotations

import json
from pathlib import Path

from mw4agent.agents.session.transcript import (
    drop_trailing_orphan_user,
    limit_history_user_turns,
    resolve_session_transcript_path,
    append_messages,
    read_messages,
)


def test_transcript_roundtrip_and_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    session_id = "sess_001"
    transcript = resolve_session_transcript_path(agent_id="main", session_id=session_id)

    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
    ]
    append_messages(transcript_file=transcript, session_id=session_id, cwd=str(tmp_path), messages=msgs)

    loaded = read_messages(transcript_file=transcript)
    assert [m.get("role") for m in loaded] == [m["role"] for m in msgs]

    trimmed = drop_trailing_orphan_user(loaded)
    assert trimmed[-1]["role"] == "assistant"

    limited = limit_history_user_turns(trimmed, 1)
    # Only the last user turn (u2) and its assistant (a2) remain after trimming u3 orphan.
    assert [m.get("content") for m in limited] == ["u2", "a2"]

