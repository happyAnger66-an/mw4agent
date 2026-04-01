"""Lifetime LLM token usage per agent, persisted under ``<agent_dir>/stats.json``."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from ...config.paths import normalize_agent_id, resolve_agent_dir

if TYPE_CHECKING:
    from ...llm.backends import LLMUsage

_locks: Dict[str, threading.Lock] = {}

SCHEMA_VERSION = 1


def _lock_for(agent_id: str) -> threading.Lock:
    if agent_id not in _locks:
        _locks[agent_id] = threading.Lock()
    return _locks[agent_id]


def get_agent_stats_path(agent_id: str) -> Path:
    aid = normalize_agent_id(agent_id or "main")
    return Path(resolve_agent_dir(aid)) / "stats.json"


def _default_stats(agent_id: str) -> Dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "agentId": normalize_agent_id(agent_id or "main"),
        "updatedAtMs": 0,
        "llmUsage": {
            "promptTokensTotal": 0,
            "completionTokensTotal": 0,
            "totalTokensTotal": 0,
            "numRequests": 0,
        },
        "byProviderModel": {},
    }


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_agent_stats(agent_id: str) -> Dict[str, Any]:
    """Return stats dict for ``agent_id`` (defaults if missing)."""
    aid = normalize_agent_id(agent_id or "main")
    path = get_agent_stats_path(aid)
    if not path.is_file():
        return _default_stats(aid)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return _default_stats(aid)
        if raw.get("schemaVersion") != SCHEMA_VERSION:
            merged = _default_stats(aid)
            merged.update({k: v for k, v in raw.items() if k in merged or k == "byProviderModel"})
            merged["schemaVersion"] = SCHEMA_VERSION
            return merged
        return raw
    except Exception:
        return _default_stats(aid)


def _merge_usage_block(
    block: Dict[str, Any],
    usage: Any,
) -> None:
    pt = usage.input_tokens
    ct = usage.output_tokens
    tt = usage.total_tokens
    if pt is not None:
        block["promptTokensTotal"] = int(block.get("promptTokensTotal") or 0) + int(pt)
    if ct is not None:
        block["completionTokensTotal"] = int(block.get("completionTokensTotal") or 0) + int(ct)
    if tt is not None:
        block["totalTokensTotal"] = int(block.get("totalTokensTotal") or 0) + int(tt)
    if any(x is not None for x in (pt, ct, tt)):
        block["numRequests"] = int(block.get("numRequests") or 0) + 1


def _provider_model_key(provider: str, model: str) -> str:
    p = (provider or "unknown").strip() or "unknown"
    m = (model or "unknown").strip() or "unknown"
    key = f"{p}/{m}"
    return key[:240]


def apply_llm_usage(
    agent_id: Optional[str],
    usage: Any,
    provider: str,
    model: str,
) -> None:
    """Add one run's aggregated ``LLMUsage`` to persistent stats (thread-safe)."""
    # Lazy import avoids import cycles (runner → llm → agents.types vs agents.stats).
    from ...llm.backends import LLMUsage as LLMUsageCls

    if not isinstance(usage, LLMUsageCls):
        return
    if not any(v is not None for v in (usage.input_tokens, usage.output_tokens, usage.total_tokens)):
        return

    aid = normalize_agent_id(agent_id or "main")
    path = get_agent_stats_path(aid)
    lock = _lock_for(aid)
    with lock:
        data = load_agent_stats(aid)
        if not isinstance(data.get("llmUsage"), dict):
            data["llmUsage"] = _default_stats(aid)["llmUsage"]
        lu = data["llmUsage"]
        assert isinstance(lu, dict)
        _merge_usage_block(lu, usage)

        bpm = data.get("byProviderModel")
        if not isinstance(bpm, dict):
            bpm = {}
            data["byProviderModel"] = bpm
        pk = _provider_model_key(provider, model)
        if pk not in bpm or not isinstance(bpm[pk], dict):
            bpm[pk] = {
                "promptTokensTotal": 0,
                "completionTokensTotal": 0,
                "totalTokensTotal": 0,
                "numRequests": 0,
            }
        _merge_usage_block(bpm[pk], usage)

        data["agentId"] = aid
        data["schemaVersion"] = SCHEMA_VERSION
        data["updatedAtMs"] = int(time.time() * 1000)

        _atomic_write_json(path, data)
