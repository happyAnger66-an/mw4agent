"""Import orchestration state from a ZIP bundle (from ``orch_dump``) into a new orch id."""

from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Set

from ..config.paths import (
    normalize_agent_id,
    orchestration_state_dir,
    resolve_agent_workspace_dir,
    resolve_orchestration_agent_workspace_dir,
)
from .orch_trace import orch_trace_file_path
from .orchestrator import Orchestrator


def _now_ms() -> int:
    return int(time.time() * 1000)


def _find_bundle_prefix(names: List[str]) -> str:
    suf = "orchestration/orch.json"
    for n in names:
        if "__MACOSX" in n:
            continue
        if n.endswith(suf):
            return n[: -len(suf)].rstrip("/")
    raise ValueError("invalid bundle: missing orchestration/orch.json")


def _safe_rel(seg: str) -> bool:
    if not seg or seg.startswith("/"):
        return False
    parts = seg.replace("\\", "/").split("/")
    return ".." not in parts


def _normalize_import_dict(data: Dict[str, Any], new_orch_id: str) -> None:
    """Rewrite ids and safe-reset runtime fields for a new host."""
    now = _now_ms()
    data["orchId"] = new_orch_id
    data["sessionKey"] = str(uuid.uuid4())
    data["status"] = "idle"
    data["error"] = None
    data["createdAt"] = now
    data["updatedAt"] = now
    data.pop("orchWorkspaceRoot", None)
    data.pop("orch_workspace_root", None)

    parts_raw = [str(x) for x in (data.get("participants") or []) if str(x).strip()]
    parts = [normalize_agent_id(x) for x in parts_raw]
    parts = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
    if not parts:
        parts = [normalize_agent_id("main")]
    data["participants"] = parts
    data["agentSessions"] = {p: str(uuid.uuid4()) for p in parts}

    strat = (data.get("strategy") or "").strip().lower()
    if strat == "dag" and isinstance(data.get("dagSpec"), dict):
        nodes_raw = data["dagSpec"].get("nodes")
        if isinstance(nodes_raw, list):
            nodes = [
                n
                for n in nodes_raw
                if isinstance(n, dict) and str(n.get("id") or "").strip()
            ]
            if nodes:
                data["dagProgress"] = {
                    str(n["id"]): {"status": "pending", "outputPreview": ""} for n in nodes
                }
                data["dagNodeSessions"] = {str(n["id"]): str(uuid.uuid4()) for n in nodes}
            else:
                data["dagProgress"] = {}
                data["dagNodeSessions"] = {}

    data["supervisorIteration"] = 0
    data["supervisorLastDecision"] = None
    data["pendingDirectAgent"] = None
    data["pendingSingleTurn"] = False
    data["orchTraceSeq"] = 0


def _write_zip_prefix_to_dir(zf: zipfile.ZipFile, zip_prefix: str, dest: Path) -> None:
    """Extract files under ``zip_prefix`` (directory prefix with trailing slash) into ``dest``."""
    for name in zf.namelist():
        if not name.startswith(zip_prefix):
            continue
        if name.endswith("/"):
            continue
        rel = name[len(zip_prefix) :]
        if not _safe_rel(rel):
            continue
        out = dest / rel
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(name))
        except OSError:
            continue


def _collect_agent_ids(zf: zipfile.ZipFile, prefix: str) -> Set[str]:
    base = f"{prefix}/agents/"
    seen: Set[str] = set()
    for name in zf.namelist():
        if not name.startswith(base):
            continue
        rest = name[len(base) :]
        seg = rest.split("/")[0]
        if seg and _safe_rel(seg):
            seen.add(normalize_agent_id(seg))
    return seen


def import_orchestration_bundle(
    orchestrator: Orchestrator,
    zip_bytes: bytes,
    *,
    restore_home_workspace: bool = False,
) -> Any:
    """
    Create a new orchestration from bundle bytes.

    Returns loaded :class:`OrchState` after writing disk and ``_load``.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError("not a valid zip bundle") from e

    with zf:
        names = zf.namelist()
        top = _find_bundle_prefix(names)
        orch_json_name = f"{top}/orchestration/orch.json"
        try:
            raw = zf.read(orch_json_name)
            data = json.loads(raw.decode("utf-8"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError("invalid bundle: bad orchestration/orch.json") from e

        if not isinstance(data, dict):
            raise ValueError("invalid bundle: orch.json must be an object")

        new_id = str(uuid.uuid4())
        _normalize_import_dict(data, new_id)

        orch_root = Path(orchestration_state_dir(new_id))
        orch_root.mkdir(parents=True, exist_ok=True)
        Path(orch_root / "orch.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        orch_sub = f"{top}/orchestration/"
        for name in zf.namelist():
            if not name.startswith(orch_sub) or name.endswith("/"):
                continue
            leaf = name[len(orch_sub) :]
            if leaf == "orch.json" or not _safe_rel(leaf):
                continue
            dest = orch_root / leaf
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(name))
            except OSError:
                continue

        trace_arc = f"{top}/orchestration/trace.jsonl"
        if trace_arc in names:
            tp = Path(orch_trace_file_path(new_id))
            try:
                tp.parent.mkdir(parents=True, exist_ok=True)
                tp.write_bytes(zf.read(trace_arc))
            except OSError:
                pass

        agent_ids = _collect_agent_ids(zf, top)
        for aid in agent_ids:
            ow_prefix = f"{top}/agents/{aid}/orchestration_workspace/"
            dest_ow = Path(resolve_orchestration_agent_workspace_dir(new_id, aid))
            dest_ow.mkdir(parents=True, exist_ok=True)
            _write_zip_prefix_to_dir(zf, ow_prefix, dest_ow)

            if restore_home_workspace:
                hw_prefix = f"{top}/agents/{aid}/home_workspace/"
                dest_hw = Path(resolve_agent_workspace_dir(aid))
                dest_hw.mkdir(parents=True, exist_ok=True)
                _write_zip_prefix_to_dir(zf, hw_prefix, dest_hw)

    st = orchestrator._load(new_id)
    if not st:
        raise ValueError("import failed: could not load new orchestration")
    return st
