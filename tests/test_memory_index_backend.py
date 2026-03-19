from __future__ import annotations

import json
import sqlite3

import pytest

from mw4agent.memory.backend import (
    get_memory_backend,
    LocalIndexBackend,
    reset_memory_backend_singleton,
    SearchOptions,
)


@pytest.fixture(autouse=True)
def _reset_memory_backend_singleton_autouse():
    """Avoid cross-test pollution of MemoryBackend singleton + cached indexes."""
    reset_memory_backend_singleton()
    yield
    reset_memory_backend_singleton()


def _count_chunks(*, db_path: str, source: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source = ?",
            (source,),
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


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

    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))

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
    session_hits = [r for r in results if r.source == "session"]
    assert session_hits
    assert session_hits[0].session_id == sid
    assert session_hits[0].path == f"sessions/{sid}.jsonl"
    assert session_hits[0].updated_at is not None
    assert session_hits[0].created_at is not None


def test_search_index_includes_timestamps_and_session_id(tmp_path):
    from mw4agent.memory.index import search_index, upsert_chunk

    db_path = str(tmp_path / "idx.sqlite")
    upsert_chunk(
        db_path=db_path,
        source="session",
        path="sessions/abc.jsonl",
        content="foo bar baz",
    )
    rows = search_index(db_path=db_path, query="foo", max_results=5)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "abc"
    assert rows[0]["created_at"] is not None
    assert rows[0]["updated_at"] is not None


def test_local_index_session_sync_threshold_skips_eager_chunk(tmp_path, monkeypatch):
    from mw4agent.agents.session.transcript import append_messages, resolve_session_transcript_path

    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))

    (cfg_dir / "mw4agent.json").write_text(
        json.dumps(
            {
                "memory": {
                    "enabled": True,
                    "sync": {"sessions": {"deltaMessages": 50, "deltaBytes": 999999999}},
                }
            }
        ),
        encoding="utf-8",
    )

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("bootstrap\n", encoding="utf-8")

    backend = get_memory_backend()
    assert isinstance(backend, LocalIndexBackend)
    db_path = backend._db_path_for("main")
    # Materialize index DB (memory chunks only) before counting session rows.
    backend.search(
        "bootstrap",
        str(ws),
        options=SearchOptions(max_results=3, min_score=0.0, session_id=None, agent_id="main"),
    )

    sid = "th1"
    tf = resolve_session_transcript_path(agent_id="main", session_id=sid)
    append_messages(
        transcript_file=tf,
        session_id=sid,
        cwd=str(ws),
        messages=[{"role": "user", "content": "threshold marker phrase"}],
    )

    assert _count_chunks(db_path=db_path, source="session") == 0

    opts = SearchOptions(max_results=5, min_score=0.0, session_id=sid, agent_id="main")
    results = backend.search("threshold marker", str(ws), options=opts)
    assert any(r.source == "session" for r in results)
    assert _count_chunks(db_path=db_path, source="session") >= 1


def test_local_index_sync_invalidates_workspace_index(tmp_path, monkeypatch):
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    (cfg_dir / "mw4agent.json").write_text(json.dumps({"memory": {"enabled": True}}), encoding="utf-8")

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("alpha_unique_token\n", encoding="utf-8")

    backend = get_memory_backend()
    assert isinstance(backend, LocalIndexBackend)
    opts = SearchOptions(max_results=5, min_score=0.0, session_id=None, agent_id="main")
    assert backend.search("alpha_unique", str(ws), options=opts)

    (ws / "MEMORY.md").write_text("beta_unique_token\n", encoding="utf-8")
    assert not backend.search("beta_unique", str(ws), options=opts)

    backend.sync()
    assert backend.search("beta_unique", str(ws), options=opts)
