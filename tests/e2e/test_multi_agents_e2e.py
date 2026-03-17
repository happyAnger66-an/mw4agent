from __future__ import annotations

import json
import multiprocessing
import os
import socket
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import uvicorn

# Ensure repo root stays on sys.path even if tests chdir into tmp_path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

def _force_local_mw4agent_import() -> None:
    """Ensure we import mw4agent from this repo checkout, not site-packages."""
    import importlib

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    else:
        # Move to front to win over site-packages.
        sys.path.remove(str(_REPO_ROOT))
        sys.path.insert(0, str(_REPO_ROOT))

    mod = sys.modules.get("mw4agent")
    if mod is not None:
        mod_file = getattr(mod, "__file__", "") or ""
        if str(_REPO_ROOT) not in mod_file:
            # Drop previously imported site-packages copy.
            for name in list(sys.modules.keys()):
                if name == "mw4agent" or name.startswith("mw4agent."):
                    del sys.modules[name]
    importlib.invalidate_caches()
    importlib.import_module("mw4agent")


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


def test_multi_agent_migrates_legacy_sessions_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    monkeypatch.setenv("MW4AGENT_IS_ENC", "0")

    _force_local_mw4agent_import()

    from mw4agent.agents.agent_manager import AgentManager
    from mw4agent.agents.session import MultiAgentSessionManager

    # Create a legacy session store in the current working directory.
    legacy = tmp_path / "mw4agent.sessions.json"
    legacy.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "session_id": "legacy-s1",
                        "session_key": "legacy:k1",
                        "agent_id": None,
                        "created_at": 1,
                        "updated_at": 2,
                        "message_count": 3,
                        "total_tokens": 4,
                        "metadata": {"source": "legacy"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    mgr = AgentManager()
    MultiAgentSessionManager(agent_manager=mgr)  # triggers best-effort auto-migration

    target = Path(mgr.resolve_sessions_file("main"))
    assert target.exists(), "Expected per-agent sessions store to be created"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "sessions" in data
    assert any(s.get("session_id") == "legacy-s1" for s in data["sessions"])

    # Backup should exist next to the legacy file.
    backups = sorted(tmp_path.glob("mw4agent.sessions.json.bak.*"))
    assert backups, "Expected a backup of legacy sessions store to be created"


def test_gateway_multi_agent_creates_isolated_state(tmp_path: Path, monkeypatch) -> None:
    """E2E: start gateway in multi-agent mode (no --session-file), run two agents, and verify per-agent dirs/stores."""

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    monkeypatch.setenv("MW4AGENT_IS_ENC", "0")
    # Ensure the gateway child process imports local repo code even if cwd changes.
    monkeypatch.setenv("PYTHONPATH", str(_REPO_ROOT))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    _force_local_mw4agent_import()

    # Use echo provider for deterministic runs (no external network).
    (cfg_dir / "mw4agent.json").write_text(
        json.dumps({"llm": {"provider": "echo", "model_id": "echo"}}),
        encoding="utf-8",
    )

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    def _run_gateway() -> None:
        # Ensure child imports local checkout even if mw4agent is installed globally.
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
        from mw4agent.gateway.server import create_app

        app = create_app(session_file="")  # multi-agent mode
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        uvicorn.Server(config).run()

    proc = multiprocessing.Process(target=_run_gateway, daemon=True)
    proc.start()

    try:
        _wait_for_health(base_url)

        for agent_id in ("a1", "a2"):
            idem = str(uuid.uuid4())
            res = _rpc_call(
                base_url,
                "agent",
                {
                    "message": f"hello from {agent_id}",
                    "sessionKey": f"e2e:{agent_id}",
                    "sessionId": "same-session-id",
                    "agentId": agent_id,
                    "idempotencyKey": idem,
                    "deliver": False,
                    "channel": "internal",
                },
                timeout_s=3.0,
            )
            assert res.get("ok") is True
            run_id = (res.get("payload") or {}).get("runId") or res.get("runId")
            assert run_id
            wait = _rpc_call(base_url, "agent.wait", {"runId": run_id, "timeoutMs": 8000}, timeout_s=10.0)
            assert wait.get("ok") is True
            assert (wait.get("payload") or {}).get("status") == "ok"

        # Verify per-agent directories and session stores exist.
        for agent_id in ("a1", "a2"):
            agent_dir = tmp_path / ".mw4agent" / "agents" / agent_id
            assert agent_dir.exists()
            assert (agent_dir / "workspace").exists()
            store = agent_dir / "sessions" / "sessions.json"
            assert store.exists()
            store_data = json.loads(store.read_text(encoding="utf-8"))
            assert any(s.get("session_id") == "same-session-id" for s in store_data.get("sessions", []))

    finally:
        proc.terminate()
        proc.join(timeout=3)

