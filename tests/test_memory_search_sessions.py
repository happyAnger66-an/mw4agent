from __future__ import annotations

import os
from pathlib import Path

from mw4agent.memory.search import search, read_file
from mw4agent.agents.session.transcript import resolve_session_transcript_path, append_messages


def test_memory_search_includes_sessions(monkeypatch, tmp_path: Path) -> None:
    # Ensure transcripts are written under an isolated state dir.
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))

    # Workspace can be empty; we are testing sessions source.
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)

    session_id = "sess_hello"
    transcript = resolve_session_transcript_path(agent_id="main", session_id=session_id)
    append_messages(
        transcript_file=transcript,
        session_id=session_id,
        cwd=str(tmp_path),
        messages=[
            {"role": "user", "content": "你好，我叫张晓安"},
            {"role": "assistant", "content": "收到"},
        ],
    )

    results = search(
        "张晓安",
        workspace_dir,
        max_results=10,
        session_id=session_id,
        agent_id="main",
    )
    assert any(r.source == "sessions" for r in results), results
    assert any(r.path == f"sessions/{session_id}.jsonl" for r in results), results

    # memory_get-like read for sessions virtual path
    rr = read_file(
        workspace_dir,
        f"sessions/{session_id}.jsonl",
        from_line=1,
        lines=5,
        agent_id="main",
    )
    assert rr.missing is False
    assert "张晓安" in rr.text

