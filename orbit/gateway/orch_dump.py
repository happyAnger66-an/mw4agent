"""Build a ZIP export of orchestration state (orch.json, team MDs, per-agent workspaces, capabilities)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..config.paths import (
    normalize_agent_id,
    orchestration_state_dir,
    resolve_agent_workspace_dir,
    resolve_orchestration_agent_workspace_dir,
)
from .orch_trace import orch_trace_file_path
from .orchestrator import Orchestrator

# RPC responses are base64-encoded; keep zip small enough for typical gateways.
MAX_ZIP_BYTES = 28 * 1024 * 1024
_MAX_SINGLE_FILE_BYTES = 6 * 1024 * 1024
_MAX_MD_FILES_ORCH_WS = 400
_TRACE_MAX_BYTES = 8 * 1024 * 1024

_HOME_BOOTSTRAP_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("MEMORY.md", "memory.md"),
    ("AGENTS.md", "agents.md"),
    ("SOUL.md", "soul.md"),
)


def _redact_secrets(obj: Any) -> Any:
    """Strip common credential fields from JSON structures (orch.json may store API keys)."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).casefold().replace("-", "_")
            if lk in ("api_key", "apikey", "password", "authorization") or lk.endswith("_secret"):
                out[k] = "***redacted***"
            else:
                out[k] = _redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [_redact_secrets(x) for x in obj]
    return obj


def _safe_arc_segment(s: str, max_len: int = 64) -> str:
    t = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip()) or "x"
    return t[:max_len]


def _add_bytes(zf: zipfile.ZipFile, arcname: str, data: bytes) -> int:
    zf.writestr(arcname, data, compress_type=zipfile.ZIP_DEFLATED)
    return len(data)


def _add_file_if_exists(zf: zipfile.ZipFile, arcname: str, path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if len(data) > _MAX_SINGLE_FILE_BYTES:
        note = f"[truncated from {len(data)} bytes]\n".encode("utf-8")
        data = note + data[: _MAX_SINGLE_FILE_BYTES - len(note)]
    return _add_bytes(zf, arcname, data)


def _add_home_bootstrap_mds(zf: zipfile.ZipFile, arc_base: str, workspace: Path) -> int:
    total = 0
    if not workspace.is_dir():
        return 0
    root = workspace.resolve()
    for group in _HOME_BOOTSTRAP_GROUPS:
        for name in group:
            p = (root / name).resolve()
            if root != p and root not in p.parents:
                continue
            if p.is_file():
                total += _add_file_if_exists(zf, f"{arc_base}/{name}", p)
                break
    return total


def _add_orch_workspace_mds(zf: zipfile.ZipFile, arc_base: str, workspace: Path) -> int:
    total = 0
    if not workspace.is_dir():
        return 0
    root = workspace.resolve()
    n = 0
    for p in sorted(root.rglob("*.md")):
        if n >= _MAX_MD_FILES_ORCH_WS:
            break
        try:
            rp = p.resolve()
        except OSError:
            continue
        if root != rp and root not in rp.parents:
            continue
        if not p.is_file():
            continue
        rel = rp.relative_to(root)
        arc = f"{arc_base}/{rel.as_posix()}"
        total += _add_file_if_exists(zf, arc, p)
        n += 1
    return total


def _readme_bytes(orch_id: str, name: str) -> bytes:
    text = f"""Orbit orchestration dump
orchId: {orch_id}
name: {name or "(unnamed)"}

Layout:
  README.txt                 — this file
  orchestration/orch.json    — state (API keys redacted)
  orchestration/*.md         — team AGENTS.md if present
  orchestration/trace.jsonl  — run trace if present (may be truncated)
  agents/<agentId>/home_workspace/
    MEMORY.md, AGENTS.md, SOUL.md (whichever exist on the agent default workspace)
  agents/<agentId>/orchestration_workspace/
    All .md files under this orchestration workspace for that agent
  agents/<agentId>/tools_skills.json
    Effective tool names and resolved skills catalog (same logic as desktop “inspect”)

目录说明（中文）：
  orchestration/ 下为编排根目录的 orch.json（密钥已脱敏）、团队 AGENTS.md、trace.jsonl。
  agents/<智能体>/home_workspace/ 为该智能体在 ~/.orbit/agents/<id>/workspace 下的引导 md。
  agents/<智能体>/orchestration_workspace/ 为该智能体在本编排隔离工作区下的全部 .md。
  tools_skills.json 为当时可用的工具名与技能列表快照。
""".strip()
    return text.encode("utf-8")


def build_orchestration_dump_zip(
    *,
    orch_id: str,
    orchestrator: Orchestrator,
) -> Tuple[bytes, str]:
    """Return (zip_bytes, suggested_filename). Raises ValueError on missing orch or oversize output."""
    oid = (orch_id or "").strip()
    if not oid:
        raise ValueError("orchId is required")
    st = orchestrator.get(oid)
    if not st:
        raise ValueError("orchestration not found")

    caps = orchestrator.inspect_participants_capabilities(oid)
    agent_rows: List[Dict[str, Any]] = list(caps.get("agents") or [])

    orch_root = Path(orchestration_state_dir(oid)).resolve()
    if not orch_root.is_dir():
        raise ValueError("orchestration directory missing")

    slug_name = _safe_arc_segment((st.name or "").strip() or "orch", 40)
    short_id = _safe_arc_segment(oid[:8], 8)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    top = f"orch-dump_{slug_name}_{short_id}_{ts}"
    safe_filename = f"{top}.zip"

    buf = io.BytesIO()
    total_uncompressed = 0
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        total_uncompressed += _add_bytes(zf, f"{top}/README.txt", _readme_bytes(oid, st.name or ""))

        orch_json = orch_root / "orch.json"
        if orch_json.is_file():
            try:
                raw = json.loads(orch_json.read_text(encoding="utf-8"))
                red = _redact_secrets(raw) if isinstance(raw, dict) else raw
                payload = json.dumps(red, ensure_ascii=False, indent=2).encode("utf-8")
            except (OSError, json.JSONDecodeError):
                payload = b'{"error":"could not parse orch.json"}\n'
            total_uncompressed += _add_bytes(zf, f"{top}/orchestration/orch.json", payload)

        for md in sorted(orch_root.glob("*.md")):
            if not md.is_file():
                continue
            total_uncompressed += _add_file_if_exists(
                zf, f"{top}/orchestration/{md.name}", md
            )

        trace_p = Path(orch_trace_file_path(oid))
        if trace_p.is_file():
            try:
                data = trace_p.read_bytes()
            except OSError:
                data = b""
            if len(data) > _TRACE_MAX_BYTES:
                head = (
                    f"[truncated: trace.jsonl was {len(data)} bytes, first {_TRACE_MAX_BYTES} kept]\n"
                ).encode("utf-8")
                data = head + data[:_TRACE_MAX_BYTES]
            total_uncompressed += _add_bytes(zf, f"{top}/orchestration/trace.jsonl", data)

        for row in agent_rows:
            aid = normalize_agent_id(str(row.get("agentId") or "main"))
            aid_seg = _safe_arc_segment(aid, 64)
            base = f"{top}/agents/{aid_seg}"

            home_ws = Path(resolve_agent_workspace_dir(aid)).resolve()
            total_uncompressed += _add_home_bootstrap_mds(zf, f"{base}/home_workspace", home_ws)

            orch_ws = Path(resolve_orchestration_agent_workspace_dir(oid, aid)).resolve()
            total_uncompressed += _add_orch_workspace_mds(
                zf, f"{base}/orchestration_workspace", orch_ws
            )

            cap_payload = {
                "agentId": aid,
                "tools": row.get("tools"),
                "skills": row.get("skills"),
                "skillsCount": row.get("skillsCount"),
                "skillsPromptCount": row.get("skillsPromptCount"),
                "skillsPromptTruncated": row.get("skillsPromptTruncated"),
                "skillsPromptCompact": row.get("skillsPromptCompact"),
            }
            cap_bytes = json.dumps(cap_payload, ensure_ascii=False, indent=2).encode("utf-8")
            total_uncompressed += _add_bytes(zf, f"{base}/tools_skills.json", cap_bytes)

        if total_uncompressed > MAX_ZIP_BYTES:
            raise ValueError(
                f"orchestration dump uncompressed payload exceeds limit ({MAX_ZIP_BYTES} bytes)"
            )

    out = buf.getvalue()
    if len(out) > MAX_ZIP_BYTES:
        raise ValueError(f"orchestration dump zip exceeds limit ({MAX_ZIP_BYTES} bytes)")
    return out, safe_filename
