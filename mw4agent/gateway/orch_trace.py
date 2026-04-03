"""Append-only JSONL trace for orchestration runs (tool / lifecycle / llm summaries)."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from ..agents.events.stream import StreamEvent
from ..config.paths import orchestration_state_dir

TRACE_FILENAME = "trace.jsonl"
# Per-string field cap (tool params/results can be large)
TRACE_MAX_CHARS = 12_000
# Rotate file when above this size (drop oldest lines until under cap)
TRACE_MAX_FILE_BYTES = 4 * 1024 * 1024


def orch_trace_file_path(orch_id: str) -> str:
    return os.path.join(orchestration_state_dir(orch_id), TRACE_FILENAME)


def _truncate(s: Optional[str], max_len: int = TRACE_MAX_CHARS) -> str:
    if s is None:
        return ""
    t = str(s)
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 20)] + "\n[... truncated ...]"


def _rotate_trace_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        sz = os.path.getsize(path)
    except OSError:
        return
    if sz <= TRACE_MAX_FILE_BYTES:
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    # Drop from front until under half cap
    target = TRACE_MAX_FILE_BYTES // 2
    while lines and len("".join(lines).encode("utf-8")) > target:
        lines.pop(0)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        pass


def append_trace_events(orch_id: str, events: List[Dict[str, Any]], *, next_seq: int) -> int:
    """Write events with seq, next_seq, next_seq+1, ... Returns next seq after last written."""
    if not events:
        return next_seq
    oid = (orch_id or "").strip()
    if not oid:
        return next_seq
    root = orchestration_state_dir(oid)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, TRACE_FILENAME)
    _rotate_trace_file(path)
    seq = next_seq
    try:
        with open(path, "a", encoding="utf-8") as f:
            for ev in events:
                row = dict(ev)
                row["seq"] = seq
                if "ts" not in row:
                    import time

                    row["ts"] = int(time.time() * 1000)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                seq += 1
    except OSError:
        return next_seq
    return seq


def read_trace_events(
    orch_id: str,
    *,
    after_seq: int = -1,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Read events with ``seq > after_seq``, oldest first, at most ``limit`` lines.

    Use ``after_seq=-1`` for the initial fetch so rows with ``seq == 0`` are included.
    """
    oid = (orch_id or "").strip()
    if not oid or limit <= 0:
        return []
    path = orch_trace_file_path(oid)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(o, dict):
                    continue
                try:
                    s = int(o.get("seq") or 0)
                except (TypeError, ValueError):
                    s = 0
                if s <= after_seq:
                    continue
                out.append(o)
                if len(out) >= limit:
                    break
    except OSError:
        return []
    return out


def build_stream_trace_handler(
    *,
    run_id: str,
    agent_id: str,
    orch_id: str,
    orch_round: int,
    buffer: List[Dict[str, Any]],
    node_id: str = "",
) -> Callable[[StreamEvent], Any]:
    """Collect tool/lifecycle/llm rows for one ``runner.run`` (filter by ``run_id``)."""
    rid = (run_id or "").strip()
    nid = (node_id or "").strip()

    async def _h(evt: StreamEvent) -> None:
        if str(evt.data.get("run_id") or "") != rid:
            return
        ts = int(evt.timestamp or 0)
        base: Dict[str, Any] = {
            "orchId": orch_id,
            "orchRound": orch_round,
            "runId": rid,
            "agentId": agent_id,
        }
        if nid:
            base["nodeId"] = nid
        if evt.stream == "lifecycle":
            phase = str(evt.type or "")
            if phase == "start":
                buffer.append(
                    {
                        **base,
                        "ts": ts,
                        "type": "lifecycle_start",
                        "payload": {},
                    }
                )
            elif phase == "end":
                buffer.append(
                    {
                        **base,
                        "ts": ts,
                        "type": "lifecycle_end",
                        "payload": {
                            "stop_reason": evt.data.get("stop_reason"),
                            "status": evt.data.get("status"),
                        },
                    }
                )
            elif phase == "error":
                buffer.append(
                    {
                        **base,
                        "ts": ts,
                        "type": "lifecycle_error",
                        "payload": {"error": _truncate(str(evt.data.get("error") or ""), 4000)},
                    }
                )
        elif evt.stream == "tool":
            typ = str(evt.type or "")
            if typ == "start":
                params = evt.data.get("params")
                prev = ""
                if isinstance(params, dict):
                    prev = json.dumps(params, ensure_ascii=False)
                elif params is not None:
                    prev = str(params)
                buffer.append(
                    {
                        **base,
                        "ts": ts,
                        "type": "tool_start",
                        "payload": {
                            "tool_call_id": str(evt.data.get("tool_call_id") or ""),
                            "tool_name": str(evt.data.get("tool_name") or ""),
                            "arguments_preview": _truncate(prev),
                        },
                    }
                )
            elif typ == "end":
                res = evt.data.get("result")
                rprev = ""
                if isinstance(res, dict):
                    rprev = json.dumps(res, ensure_ascii=False)
                elif res is not None:
                    rprev = str(res)
                buffer.append(
                    {
                        **base,
                        "ts": ts,
                        "type": "tool_end",
                        "payload": {
                            "tool_call_id": str(evt.data.get("tool_call_id") or ""),
                            "tool_name": str(evt.data.get("tool_name") or ""),
                            "success": bool(evt.data.get("success")),
                            "result_preview": _truncate(rprev),
                        },
                    }
                )
            elif typ == "error":
                buffer.append(
                    {
                        **base,
                        "ts": ts,
                        "type": "tool_error",
                        "payload": {
                            "tool_call_id": str(evt.data.get("tool_call_id") or ""),
                            "tool_name": str(evt.data.get("tool_name") or ""),
                            "error": _truncate(str(evt.data.get("error") or ""), 4000),
                        },
                    }
                )
            elif typ == "processing":
                # Skip noisy periodic processing ticks for trace file
                pass
        elif evt.stream == "llm" and evt.type == "message":
            thinking = evt.data.get("thinking")
            content = evt.data.get("content")
            tcalls = evt.data.get("tool_calls")
            buffer.append(
                {
                    **base,
                    "ts": ts,
                    "type": "llm_round",
                    "payload": {
                        "phase": evt.data.get("phase"),
                        "round": evt.data.get("round"),
                        "thinking_preview": _truncate(
                            str(thinking) if thinking else "", max_len=6000
                        ),
                        "content_preview": _truncate(str(content) if content else "", max_len=6000),
                        "tool_calls": tcalls if isinstance(tcalls, list) else None,
                        "usage": evt.data.get("usage"),
                    },
                }
            )

    return _h


def record_user_message_trace(
    orch_id: str,
    *,
    orch_round: int,
    text: str,
    next_seq: int,
) -> int:
    """Single user_message row when a user sends into the orchestration."""
    t = _truncate(text, 8000)
    return append_trace_events(
        orch_id,
        [
            {
                "orchId": orch_id,
                "orchRound": orch_round,
                "runId": "",
                "agentId": "user",
                "type": "user_message",
                "payload": {"text": t},
            }
        ],
        next_seq=next_seq,
    )


def flush_run_trace(
    orch_id: str,
    *,
    agent_id: str,
    orch_round: int,
    run_id: str,
    agent_message: str,
    assistant_text: str,
    stream_buffer: List[Dict[str, Any]],
    next_seq: int,
    node_id: str = "",
) -> int:
    """Prepend agent_input / append stream_buffer / append agent_output; write in order."""
    nid = (node_id or "").strip()
    in_payload: Dict[str, Any] = {"text": _truncate(agent_message, 16000)}
    out_payload: Dict[str, Any] = {"text": _truncate(assistant_text, 16000)}
    if nid:
        in_payload["nodeId"] = nid
        out_payload["nodeId"] = nid
    rows: List[Dict[str, Any]] = [
        {
            "orchId": orch_id,
            "orchRound": orch_round,
            "runId": run_id,
            "agentId": agent_id,
            **({"nodeId": nid} if nid else {}),
            "type": "agent_input",
            "payload": in_payload,
        }
    ]
    rows.extend(stream_buffer)
    rows.append(
        {
            "orchId": orch_id,
            "orchRound": orch_round,
            "runId": run_id,
            "agentId": agent_id,
            **({"nodeId": nid} if nid else {}),
            "type": "agent_output",
            "payload": out_payload,
        }
    )
    return append_trace_events(orch_id, rows, next_seq=next_seq)
