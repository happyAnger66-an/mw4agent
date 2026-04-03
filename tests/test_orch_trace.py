"""``orch_trace``: append/read JSONL 与 ``record_user_message_trace`` 最小用例。"""

from __future__ import annotations

import json

import pytest

from mw4agent.gateway.orch_trace import (
    TRACE_FILENAME,
    append_trace_events,
    orch_trace_file_path,
    read_trace_events,
    record_user_message_trace,
)


@pytest.fixture()
def state_dir(tmp_path, monkeypatch) -> str:
    root = str(tmp_path / ".mw4agent")
    monkeypatch.setenv("MW4AGENT_STATE_DIR", root)
    return root


def test_append_trace_events_assigns_seq_and_read_respects_after_seq(state_dir: str) -> None:
    oid = "trace-test-orch"
    n1 = append_trace_events(
        oid,
        [{"orchId": oid, "type": "a", "payload": {}}],
        next_seq=0,
    )
    assert n1 == 1
    n2 = append_trace_events(
        oid,
        [{"orchId": oid, "type": "b", "payload": {}}],
        next_seq=n1,
    )
    assert n2 == 2

    all_rows = read_trace_events(oid, limit=10)
    assert len(all_rows) == 2
    assert all_rows[0]["seq"] == 0
    assert all_rows[0]["type"] == "a"
    assert all_rows[1]["seq"] == 1
    assert all_rows[1]["type"] == "b"

    tail = read_trace_events(oid, after_seq=0, limit=10)
    assert len(tail) == 1
    assert tail[0]["type"] == "b"


def test_record_user_message_trace_writes_user_message_row(state_dir: str) -> None:
    oid = "u-msg-orch"
    nxt = record_user_message_trace(oid, orch_round=3, text="hello trace", next_seq=0)
    assert nxt == 1

    path = orch_trace_file_path(oid)
    assert path.endswith(TRACE_FILENAME)
    raw = open(path, encoding="utf-8").read().strip()
    row = json.loads(raw)
    assert row["type"] == "user_message"
    assert row["agentId"] == "user"
    assert row["orchRound"] == 3
    assert "hello trace" in str(row.get("payload", {}))


def test_read_trace_events_empty_when_no_file(state_dir: str) -> None:
    assert read_trace_events("no-such-orch-yet", after_seq=0, limit=10) == []
