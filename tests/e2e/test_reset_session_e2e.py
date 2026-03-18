from __future__ import annotations

import json
import multiprocessing
import socket
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import uvicorn


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_health(base_url: str, deadline_s: float = 8.0) -> None:
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok") is True:
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("gateway did not become healthy in time")


def _rpc_call(base_url: str, method: str, params: dict, timeout_s: float = 5.0) -> dict:
    body = {"id": str(uuid.uuid4()), "method": method, "params": params}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/rpc",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_reset_mints_new_session_id_and_reuses_latest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    monkeypatch.setenv("MW4AGENT_IS_ENC", "0")

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    (cfg_dir / "mw4agent.json").write_text(
        json.dumps({"llm": {"provider": "echo", "model_id": "echo"}}),
        encoding="utf-8",
    )

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    def _run_gateway() -> None:
        from mw4agent.gateway.server import create_app

        app = create_app(session_file="")  # multi-agent mode
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        uvicorn.Server(config).run()

    proc = multiprocessing.Process(target=_run_gateway, daemon=True)
    proc.start()
    try:
        _wait_for_health(base_url)

        session_key = "e2e:reset"
        agent_id = "main"

        # 1) First normal call without explicit sessionId -> gateway chooses (new).
        res1 = _rpc_call(
            base_url,
            "agent",
            {
                "message": "hello",
                "sessionKey": session_key,
                "agentId": agent_id,
                "idempotencyKey": str(uuid.uuid4()),
            },
            timeout_s=3.0,
        )
        assert res1.get("ok") is True
        session_id_1 = (res1.get("payload") or {}).get("sessionId")
        assert isinstance(session_id_1, str) and session_id_1

        # 2) /reset with no remaining message should mint a new sessionId.
        res_reset = _rpc_call(
            base_url,
            "agent",
            {
                "message": "/reset",
                "sessionKey": session_key,
                "agentId": agent_id,
                "idempotencyKey": str(uuid.uuid4()),
            },
            timeout_s=3.0,
        )
        assert res_reset.get("ok") is True
        session_id_2 = (res_reset.get("payload") or {}).get("sessionId")
        assert isinstance(session_id_2, str) and session_id_2
        assert session_id_2 != session_id_1

        # 3) Next normal call without explicit sessionId should reuse latest (session_id_2).
        res2 = _rpc_call(
            base_url,
            "agent",
            {
                "message": "after reset",
                "sessionKey": session_key,
                "agentId": agent_id,
                "idempotencyKey": str(uuid.uuid4()),
            },
            timeout_s=3.0,
        )
        assert res2.get("ok") is True
        assert (res2.get("payload") or {}).get("sessionId") == session_id_2

    finally:
        proc.terminate()
        proc.join(timeout=3)

