from __future__ import annotations

import json
from pathlib import Path

from mw4agent.memory.backend import get_memory_backend, LocalIndexBackend, SearchOptions


def test_local_index_backend_search_uses_sqlite_index(tmp_path, monkeypatch):
    # Redirect state/config dirs to tmp so we don't touch real ~/.mw4agent.
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))

    # Enable memory section in root config.
    root_cfg = {
        "memory": {
            "enabled": True,
        }
    }
    (cfg_dir / "mw4agent.json").write_text(json.dumps(root_cfg), encoding="utf-8")

    # Create a fake workspace with MEMORY.md
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("# Notes\n\nhello index backend\n", encoding="utf-8")

    # New backend should be LocalIndexBackend when enabled=true.
    backend = get_memory_backend()
    assert isinstance(backend, LocalIndexBackend)

    opts = SearchOptions(max_results=5, min_score=0.0, session_id=None, agent_id="main")
    results = backend.search("hello", str(ws), options=opts)
    paths = [r.path for r in results]
    assert "MEMORY.md" in paths


def test_local_index_backend_search_includes_session_transcript(tmp_path, monkeypatch):
    from mw4agent.agents.session.transcript import append_messages, resolve_session_transcript_path

    # Redirect state/config dirs to tmp so we don't touch real ~/.mw4agent.
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))

    # Enable memory section in root config.
    (cfg_dir / "mw4agent.json").write_text(json.dumps({"memory": {"enabled": True}}), encoding="utf-8")

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("bootstrap\n", encoding="utf-8")

    backend = get_memory_backend()
    assert isinstance(backend, LocalIndexBackend)

    sid = "s1"
    tf = resolve_session_transcript_path(agent_id="main", session_id=sid)
    append_messages(
        transcript_file=tf,
        session_id=sid,
        cwd=str(ws),
        messages=[
            {"role": "user", "content": "session hello world"},
            {"role": "assistant", "content": "ack"},
        ],
    )

    opts = SearchOptions(max_results=5, min_score=0.0, session_id=sid, agent_id="main")
    results = backend.search("session hello", str(ws), options=opts)
    assert any(r.source == "session" for r in results)
