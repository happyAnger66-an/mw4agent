from __future__ import annotations

import json
from pathlib import Path

from mw4agent.agents.session.transcript import (
    branch_to_parent,
    build_messages_from_leaf,
    append_compaction,
    get_leaf_entry_meta,
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


def test_session_entry_normalizes_updated_at(monkeypatch) -> None:
    # Force deterministic "now".
    import mw4agent.agents.session.manager as mod

    monkeypatch.setattr(mod.time, "time", lambda: 1730000000.0)  # seconds
    from mw4agent.agents.session.manager import SessionEntry

    # Legacy tiny placeholder should be treated as invalid and replaced with now_ms.
    e1 = SessionEntry(session_id="s1", session_key="k1", created_at=1, updated_at=2)
    assert e1.updated_at >= 1_000_000_000_000

    # Epoch seconds should be converted to ms.
    e2 = SessionEntry(
        session_id="s2",
        session_key="k2",
        created_at=1730000000,
        updated_at=1730000001,
    )
    assert e2.updated_at == 1730000001 * 1000


def test_transcript_leaf_chain_and_branch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    session_id = "sess_002"
    transcript = resolve_session_transcript_path(agent_id="main", session_id=session_id)

    # u1/a1/u2 (orphan user at end)
    append_messages(
        transcript_file=transcript,
        session_id=session_id,
        cwd=str(tmp_path),
        messages=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ],
    )
    # build from leaf includes u2
    chain1 = build_messages_from_leaf(transcript_file=transcript)
    assert [m.get("content") for m in chain1] == ["u1", "a1", "u2"]

    # Branch back one step: remove trailing orphan user by moving leaf to parent (a1).
    leaf_id, parent_id, leaf_msg = get_leaf_entry_meta(transcript_file=transcript)
    assert leaf_msg and leaf_msg.get("role") == "user"
    assert parent_id is not None
    branch_to_parent(transcript_file=transcript, parent_id=parent_id)
    chain_branch = build_messages_from_leaf(transcript_file=transcript)
    assert [m.get("content") for m in chain_branch] == ["u1", "a1"]

    # Compaction becomes a system message and new leaf.
    cid = append_compaction(transcript_file=transcript, session_id=session_id, cwd=str(tmp_path), summary="summary")
    chain2 = build_messages_from_leaf(transcript_file=transcript)
    assert chain2[-1]["role"] == "system"
    assert "summary" in (chain2[-1].get("content") or "")
    # Now branch to compaction's parent (which was previous leaf) to drop compaction.
    _, comp_parent, _ = get_leaf_entry_meta(transcript_file=transcript)
    branch_to_parent(transcript_file=transcript, parent_id=comp_parent)
    chain3 = build_messages_from_leaf(transcript_file=transcript)
    assert chain3 == chain_branch


def test_single_store_transcript_is_colocated(tmp_path: Path) -> None:
    from mw4agent.agents.session.manager import SessionManager

    store = tmp_path / "sessions.json"
    mgr = SessionManager(str(store))
    p = mgr.resolve_transcript_path("sess_001")
    assert p.endswith("sess_001.jsonl")
    assert str(tmp_path) in p

