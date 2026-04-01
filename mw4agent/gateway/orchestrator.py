from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agents.agent_manager import AgentManager
from ..agents.runner.runner import AgentRunner
from ..agents.types import AgentRunParams
from ..config.paths import get_state_dir, normalize_agent_id, resolve_orchestration_agent_workspace_dir
from ..llm.backends import _call_openai_chat, _thinking_extra_body, list_providers  # type: ignore
from ..memory.bootstrap import load_bootstrap_for_orchestration

from .dag_spec import MAX_UPSTREAM_SNIPPET, normalize_dag_dict

logger = logging.getLogger(__name__)

# Router LLM: transcript / snippets (characters) for routing prompt and agent message context.
_ROUTER_LLM_TRANSCRIPT_MAX_CHARS = 12000
_ROUTER_LLM_ORIGINAL_USER_MAX_CHARS = 4000
_ROUTER_LLM_IMMEDIATE_MAX_CHARS = 8000
# Per-agent role line for router prompt (identity / responsibilities).
_ROUTER_AGENT_ROLE_MAX_CHARS = 2000

# Appended to every orchestration ``orch_hint``: match user language for reasoning (if shown) and replies.
_ORCH_LANGUAGE_HINT_EN_ZH = (
    "Language: Use the same language as the user for reasoning (if shown) and for the final reply "
    "(e.g. Chinese when the user writes in Chinese).\n"
    "语言：与用户保持一致——若展示推理过程，与最终回复均使用用户所用语言（例如用户使用中文则用中文）。\n"
)

# Supervisor LLM: delay between retries after connection/transport/empty response failures.
_SUPERVISOR_LLM_RETRY_DELAY_S = 10.0


async def _supervisor_retry_delay() -> None:
    await asyncio.sleep(_SUPERVISOR_LLM_RETRY_DELAY_S)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _orchestrations_root_dir() -> str:
    return os.path.join(get_state_dir(), "orchestrations")


def _orch_dir(orch_id: str) -> str:
    return os.path.join(_orchestrations_root_dir(), orch_id)


def _orch_state_path(orch_id: str) -> str:
    return os.path.join(_orch_dir(orch_id), "orch.json")


def _orch_agent_workspace(orch_id: str, agent_id: str) -> str:
    """Isolated workspace for orchestration runs (MEMORY.md, tools cwd, memory index)."""
    p = resolve_orchestration_agent_workspace_dir(orch_id, agent_id)
    os.makedirs(p, exist_ok=True)
    return p


def _strip_at_mentions(text: str) -> str:
    """Remove ``@agentId`` tokens for the model prompt (same charset as normalize_agent_id)."""
    s = re.sub(r"@[a-zA-Z0-9._-]+\s*", " ", text or "")
    return " ".join(s.split()).strip()


@dataclass
class OrchMessage:
    id: str
    ts: int
    round: int
    speaker: str  # "user" or agentId
    role: str  # "user"|"assistant"
    text: str
    nodeId: str = ""  # DAG node id when strategy=dag and role=assistant


@dataclass
class OrchState:
    orchId: str
    sessionKey: str
    createdAt: int
    updatedAt: int
    status: str  # idle|running|error|aborted
    name: str = ""
    strategy: str = "round_robin"  # round_robin|router_llm|dag|supervisor_pipeline
    maxRounds: int = 8  # round_robin: assistant turns per user message; dag: largely ignored (one run per node)
    routerLlm: Optional[Dict[str, str]] = None
    # strategy=router_llm: agentId -> user-provided role / identity (injected into router prompt each pick)
    routerAgentRoles: Optional[Dict[str, str]] = None
    participants: List[str] = field(default_factory=list)
    agentSessions: Dict[str, str] = field(default_factory=dict)  # agentId -> sessionId
    currentRound: int = 0
    messages: List[OrchMessage] = field(default_factory=list)
    error: Optional[str] = None
    orchSchemaVersion: int = 1
    dagSpec: Optional[Dict[str, Any]] = None  # normalized {nodes, parallelism}; optional position per node for Web editor
    dagProgress: Optional[Dict[str, Any]] = None  # nodeId -> {status, outputPreview, error?}
    dagParallelism: int = 4
    dagNodeSessions: Dict[str, str] = field(default_factory=dict)  # DAG node id -> sessionId
    # Last send: off | on | stream — controls AgentRunParams.reasoning_level for agent runs
    orchReasoningLevel: Optional[str] = None
    # Next linear run: reply only from this agent (round_robin / router_llm), then cleared
    pendingDirectAgent: Optional[str] = None
    pendingSingleTurn: bool = False
    # supervisor_pipeline: ordered stroke A→B→C, then supervisor LLM continue/stop
    supervisorPipeline: List[str] = field(default_factory=list)
    supervisorLlm: Optional[Dict[str, str]] = None
    supervisorMaxIterations: int = 5
    # After a failed or empty supervisor HTTP call, wait _SUPERVISOR_LLM_RETRY_DELAY_S and retry; at most this many retries (not counting the first attempt).
    supervisorLlmMaxRetries: int = 12
    supervisorIteration: int = 0
    supervisorLastDecision: Optional[Dict[str, Any]] = None


def _truncate_text(s: str, max_len: int) -> str:
    t = (s or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


def _strip_optional_markdown_fence(s: str) -> str:
    """Remove optional ``` fences (same idea as supervisor JSON parsing)."""
    t = (s or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_first_json_object(s: str) -> Optional[str]:
    """Return first balanced `{...}` substring, or None."""
    i = (s or "").find("{")
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i : j + 1]
    return None


def _last_user_message_text(messages: List[OrchMessage]) -> str:
    for m in reversed(messages or []):
        if m.role == "user":
            return (m.text or "").strip()
    return ""


def _format_transcript_since_last_user(messages: List[OrchMessage], max_chars: int) -> str:
    """Transcript from the last user message through the latest message (for router context)."""
    msgs = messages or []
    last_u = -1
    for i, m in enumerate(msgs):
        if m.role == "user":
            last_u = i
    if last_u < 0:
        return ""
    parts: List[str] = []
    for m in msgs[last_u:]:
        who = (m.speaker or "").strip() or ("user" if m.role == "user" else "assistant")
        label = "user" if m.role == "user" else who
        text = (m.text or "").strip()
        if not text:
            continue
        parts.append(f"[{label}]\n{text}")
    blob = "\n\n".join(parts)
    return _truncate_text(blob, max_chars)


def _parse_router_agent_pick(raw: str, participants: List[str]) -> Optional[str]:
    """Parse router reply: JSON ``next_agent`` preferred, else first line as agent id."""
    s = _strip_optional_markdown_fence(raw or "")
    s = s.strip()
    if not s:
        return None
    cand_set = set(participants)
    for fragment in (s, _extract_first_json_object(s) or ""):
        if not fragment:
            continue
        try:
            obj = json.loads(fragment)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        for key in ("next_agent", "agent_id", "agent", "speaker"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip() in cand_set:
                return v.strip()
    line = s.splitlines()[0].strip().strip("`").strip('"').strip("'")
    if line in cand_set:
        return line
    return None


def _format_router_agent_roles_block(
    participants: List[str], roles: Optional[Dict[str, str]]
) -> str:
    """Human-readable block listing each candidate id and optional role description."""
    if not participants:
        return "(no candidates)"
    lines: List[str] = []
    r = roles or {}
    for pid in participants:
        desc = (r.get(pid) or "").strip()
        if desc:
            lines.append(f"- {pid}: {desc}")
        else:
            lines.append(f"- {pid}: (no role description)")
    return "\n".join(lines)


def _canon_participant_id(agent_id: str, participants: List[str]) -> Optional[str]:
    """Return the id string as it appears in ``participants``, or None if not a member."""
    want = normalize_agent_id(agent_id)
    for p in participants:
        if normalize_agent_id(p) == want:
            return p
    return None


def _filter_router_agent_roles_to_participants(
    old: Optional[Dict[str, str]], participants: List[str]
) -> Optional[Dict[str, str]]:
    if not old or not participants:
        return None
    out: Dict[str, str] = {}
    for k, v in old.items():
        canon = _canon_participant_id(str(k), participants)
        if canon is not None and (v or "").strip():
            out[canon] = _truncate_text(str(v).strip(), _ROUTER_AGENT_ROLE_MAX_CHARS)
    return out or None


def _patch_router_agent_roles(
    old: Optional[Dict[str, str]],
    patch: Optional[Dict[str, Any]],
    participants: List[str],
) -> Optional[Dict[str, str]]:
    """Merge per-agent role descriptions; ``patch`` None means keep ``old`` (filtered)."""
    if patch is None:
        return _filter_router_agent_roles_to_participants(old, participants)
    if not isinstance(patch, dict):
        return _filter_router_agent_roles_to_participants(old, participants)
    base: Dict[str, str] = {}
    if isinstance(old, dict):
        for k, v in old.items():
            canon = _canon_participant_id(str(k), participants)
            if canon is not None and v is not None and str(v).strip():
                base[canon] = _truncate_text(str(v).strip(), _ROUTER_AGENT_ROLE_MAX_CHARS)
    for k, v in patch.items():
        canon = _canon_participant_id(str(k), participants)
        if canon is None:
            continue
        if v is None:
            base.pop(canon, None)
            continue
        s = str(v).strip()
        if not s:
            base.pop(canon, None)
        else:
            base[canon] = _truncate_text(s, _ROUTER_AGENT_ROLE_MAX_CHARS)
    return base or None


def _build_router_llm_user_prompt(
    *,
    participants: List[str],
    original_user: str,
    transcript: str,
    last_immediate: str,
    turn_1based: int,
    max_turns: int,
    agent_roles: Optional[Dict[str, str]] = None,
) -> str:
    agent_list = ", ".join(participants)
    ou = _truncate_text(original_user, _ROUTER_LLM_ORIGINAL_USER_MAX_CHARS)
    tr = (transcript or "").strip() or "(empty)"
    li = _truncate_text(last_immediate, _ROUTER_LLM_IMMEDIATE_MAX_CHARS)
    roles_block = _format_router_agent_roles_block(participants, agent_roles)
    return (
        "You are the routing model for a multi-agent team. "
        "Choose exactly ONE next speaker from the candidates.\n\n"
        f"Candidates (pick exactly one id verbatim): {agent_list}\n\n"
        "Agent identity / responsibilities (use these to match expertise to the user request "
        "and the immediate next step):\n"
        f"{roles_block}\n\n"
        f"Assistant turn in this user-message batch: {turn_1based} of {max_turns}\n\n"
        f"Original user request:\n{ou}\n\n"
        f"Orchestration transcript since that user message:\n{tr}\n\n"
        f"Immediate context for the next step (primary input for the chosen agent):\n{li}\n\n"
        "Reply with ONLY valid JSON, no markdown fences, a single object:\n"
        '{"next_agent":"<candidate_id>"}\n'
        "The next_agent value must be exactly one of the candidate ids."
    )


def _parse_supervisor_decision(raw: str) -> Dict[str, Any]:
    """Parse supervisor JSON (optional ``` fences). Raises ValueError on failure."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty supervisor reply")
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"supervisor JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("supervisor reply must be a JSON object")
    action = str(obj.get("action") or "").strip().lower()
    if action not in ("continue", "stop"):
        raise ValueError("supervisor action must be continue or stop")
    out: Dict[str, Any] = {"action": action, "reason": str(obj.get("reason") or "").strip()}
    if action == "continue":
        b = str(obj.get("brief_for_next_stroke") or obj.get("brief") or "").strip()
        out["brief_for_next_stroke"] = b
    if obj.get("final_user_visible_summary") is not None:
        out["final_user_visible_summary"] = str(obj.get("final_user_visible_summary") or "").strip()
    return out


class Orchestrator:
    def __init__(self, *, agent_manager: AgentManager, runner: AgentRunner) -> None:
        self.agent_manager = agent_manager
        self.runner = runner
        self._tasks: Dict[str, asyncio.Task] = {}

    @staticmethod
    def _patch_router_llm(
        old: Optional[Dict[str, str]], patch: Optional[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """Merge router LLM fields; omit empty ``api_key`` in patch to keep stored key."""
        if not isinstance(patch, dict):
            return old
        base: Dict[str, str] = {}
        if isinstance(old, dict) and old:
            o = old
            pairs = [
                ("provider", o.get("provider")),
                ("model", o.get("model")),
                ("base_url", o.get("base_url") or o.get("baseUrl")),
                ("api_key", o.get("api_key") or o.get("apiKey")),
                ("thinking_level", o.get("thinking_level") or o.get("thinkingLevel")),
            ]
            for nk, v in pairs:
                if v is not None and str(v).strip():
                    base[nk] = str(v).strip()
        allowed_map = {
            "provider": "provider",
            "model": "model",
            "base_url": "base_url",
            "baseUrl": "base_url",
            "api_key": "api_key",
            "apiKey": "api_key",
            "thinking_level": "thinking_level",
            "thinkingLevel": "thinking_level",
        }
        for k, v in patch.items():
            if k not in allowed_map:
                continue
            nk = allowed_map[k]
            if nk == "api_key":
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                base[nk] = s
                continue
            if v is None:
                base.pop(nk, None)
                continue
            s = str(v).strip()
            if not s:
                base.pop(nk, None)
            else:
                base[nk] = s
        return base or None

    @staticmethod
    def _reasoning_level_for_orch(st: OrchState) -> Optional[str]:
        v = getattr(st, "orchReasoningLevel", None)
        if isinstance(v, str) and v.strip():
            x = v.strip().lower()
            if x in ("off", "on", "stream"):
                return x
        return "stream"

    def _save(self, st: OrchState) -> None:
        st.updatedAt = _now_ms()
        root = Path(_orch_dir(st.orchId))
        root.mkdir(parents=True, exist_ok=True)
        payload = asdict(st)
        ds = payload.get("dagSpec")
        if isinstance(ds, dict):
            payload["dagSpec"] = {k: v for k, v in ds.items() if k != "topologicalOrder"}
        Path(_orch_state_path(st.orchId)).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load(self, orch_id: str) -> Optional[OrchState]:
        p = Path(_orch_state_path(orch_id))
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        msgs_raw = data.get("messages") if isinstance(data.get("messages"), list) else []
        msgs: List[OrchMessage] = []
        for m in msgs_raw:
            if not isinstance(m, dict):
                continue
            msgs.append(
                OrchMessage(
                    id=str(m.get("id") or ""),
                    ts=int(m.get("ts") or 0) or 0,
                    round=int(m.get("round") or 0) or 0,
                    speaker=str(m.get("speaker") or ""),
                    role=str(m.get("role") or ""),
                    text=str(m.get("text") or ""),
                    nodeId=str(m.get("nodeId") or m.get("node_id") or ""),
                )
            )
        dag_raw = data.get("dagSpec")
        dag_spec: Optional[Dict[str, Any]] = dict(dag_raw) if isinstance(dag_raw, dict) else None
        dag_prog = data.get("dagProgress")
        dag_progress: Optional[Dict[str, Any]] = dict(dag_prog) if isinstance(dag_prog, dict) else None
        dns = data.get("dagNodeSessions")
        dag_node_sess: Dict[str, str] = dict(dns) if isinstance(dns, dict) else {}
        try:
            dpar = int(data.get("dagParallelism") or data.get("dag_parallelism") or 4)
        except (TypeError, ValueError):
            dpar = 4
        dpar = max(1, min(32, dpar))
        try:
            osv = int(data.get("orchSchemaVersion") or 1)
        except (TypeError, ValueError):
            osv = 1
        orch_rl_raw = data.get("orchReasoningLevel") or data.get("orch_reasoning_level")
        orch_rl: Optional[str] = None
        if isinstance(orch_rl_raw, str):
            x = orch_rl_raw.strip().lower()
            if x in ("off", "on", "stream"):
                orch_rl = x
        pd_raw = data.get("pendingDirectAgent") or data.get("pending_direct_agent")
        pd_agent: Optional[str] = None
        if isinstance(pd_raw, str) and pd_raw.strip():
            pd_agent = normalize_agent_id(pd_raw.strip())
        ps_raw = data.get("pendingSingleTurn")
        pending_single = ps_raw is True or str(ps_raw).lower() in ("1", "true", "yes")
        sp_raw = data.get("supervisorPipeline") or data.get("supervisor_pipeline")
        sup_pipeline: List[str] = []
        if isinstance(sp_raw, list):
            sup_pipeline = [
                normalize_agent_id(str(x).strip())
                for x in sp_raw
                if str(x).strip()
            ]
        sup_llm_raw = data.get("supervisorLlm") or data.get("supervisor_llm")
        sup_llm: Optional[Dict[str, str]] = None
        if isinstance(sup_llm_raw, dict) and sup_llm_raw:
            sup_llm = {str(k): str(v) for k, v in sup_llm_raw.items() if v is not None}
        try:
            smi = int(data.get("supervisorMaxIterations") or data.get("supervisor_max_iterations") or 5)
        except (TypeError, ValueError):
            smi = 5
        smi = max(1, min(64, smi))
        try:
            smr = int(
                data.get("supervisorLlmMaxRetries")
                or data.get("supervisor_llm_max_retries")
                or 12
            )
        except (TypeError, ValueError):
            smr = 12
        smr = max(0, min(64, smr))
        try:
            s_iter = int(data.get("supervisorIteration") or data.get("supervisor_iteration") or 0)
        except (TypeError, ValueError):
            s_iter = 0
        s_last = data.get("supervisorLastDecision") or data.get("supervisor_last_decision")
        s_decision: Optional[Dict[str, Any]] = dict(s_last) if isinstance(s_last, dict) else None
        rar_raw = data.get("routerAgentRoles") or data.get("router_agent_roles")
        router_agent_roles_loaded: Optional[Dict[str, str]] = None
        if isinstance(rar_raw, dict) and rar_raw:
            router_agent_roles_loaded = {
                str(k): str(v) for k, v in rar_raw.items() if v is not None and str(v).strip()
            }
        parts_load = [str(x) for x in (data.get("participants") or []) if str(x).strip()]
        rar_filtered = _filter_router_agent_roles_to_participants(
            router_agent_roles_loaded, parts_load
        )
        return OrchState(
            orchId=str(data.get("orchId") or orch_id),
            sessionKey=str(data.get("sessionKey") or ""),
            createdAt=int(data.get("createdAt") or 0) or 0,
            updatedAt=int(data.get("updatedAt") or 0) or 0,
            status=str(data.get("status") or ""),
            name=str(data.get("name") or ""),
            strategy=str(data.get("strategy") or "round_robin"),
            maxRounds=int(data.get("maxRounds") or 8) or 8,
            routerLlm=dict(data.get("routerLlm") or {}) or None,
            routerAgentRoles=rar_filtered,
            participants=parts_load,
            agentSessions=dict(data.get("agentSessions") or {}),
            currentRound=int(data.get("currentRound") or 0) or 0,
            messages=msgs,
            error=str(data.get("error") or "") or None,
            orchSchemaVersion=osv,
            dagSpec=dag_spec,
            dagProgress=dag_progress,
            dagParallelism=dpar,
            dagNodeSessions=dag_node_sess,
            orchReasoningLevel=orch_rl,
            pendingDirectAgent=pd_agent,
            pendingSingleTurn=pending_single,
            supervisorPipeline=sup_pipeline,
            supervisorLlm=sup_llm,
            supervisorMaxIterations=smi,
            supervisorLlmMaxRetries=smr,
            supervisorIteration=s_iter,
            supervisorLastDecision=s_decision,
        )

    def get(self, orch_id: str) -> Optional[OrchState]:
        return self._load(orch_id)

    def delete(self, orch_id: str) -> bool:
        """Delete an orchestration directory and all persisted state."""
        oid = (orch_id or "").strip()
        if not oid:
            raise ValueError("orchId is required")
        root = Path(_orch_dir(oid)).resolve()
        if not root.exists():
            return False
        if not root.is_dir():
            raise ValueError("orchestration path is not a directory")
        # Safety: ensure under orchestrations root
        base = Path(_orchestrations_root_dir()).resolve()
        if base != root and base not in root.parents:
            raise ValueError("refusing to delete path outside orchestrations root")
        shutil.rmtree(root)
        return True

    def is_running(self, orch_id: str) -> bool:
        st = self._load(orch_id)
        return bool(st and st.status == "running")

    def list(self) -> List[OrchState]:
        root = Path(_orchestrations_root_dir())
        if not root.exists() or not root.is_dir():
            return []
        items: List[OrchState] = []
        for p in root.iterdir():
            if not p.is_dir():
                continue
            st = self._load(p.name)
            if st:
                items.append(st)
        items.sort(key=lambda s: int(s.updatedAt or 0), reverse=True)
        return items

    def create(
        self,
        *,
        session_key: str,
        name: str,
        participants: List[str],
        max_rounds: int = 8,
        strategy: str = "round_robin",
        router_llm: Optional[Dict[str, str]] = None,
        router_agent_roles: Optional[Dict[str, Any]] = None,
        dag: Optional[Dict[str, Any]] = None,
        supervisor_pipeline: Optional[List[str]] = None,
        supervisor_llm: Optional[Dict[str, str]] = None,
        supervisor_max_iterations: Optional[int] = None,
        supervisor_llm_max_retries: Optional[int] = None,
    ) -> OrchState:
        orch_id = str(uuid.uuid4())
        strat = (strategy or "round_robin").strip() or "round_robin"
        dag_spec: Optional[Dict[str, Any]] = None
        dag_progress: Optional[Dict[str, Any]] = None
        dag_parallelism = 4
        dag_node_sess: Dict[str, str] = {}
        sup_pipeline: List[str] = []
        sup_llm: Optional[Dict[str, str]] = None
        try:
            smi_default = int(supervisor_max_iterations) if supervisor_max_iterations is not None else 5
        except (TypeError, ValueError):
            smi_default = 5
        smi_default = max(1, min(64, smi_default))
        try:
            smr_default = (
                int(supervisor_llm_max_retries) if supervisor_llm_max_retries is not None else 12
            )
        except (TypeError, ValueError):
            smr_default = 12
        smr_default = max(0, min(64, smr_default))

        if dag is not None:
            dag_spec = normalize_dag_dict(dag if isinstance(dag, dict) else {})
            strat = "dag"
            dag_parallelism = int(dag_spec.get("parallelism") or 4)
            parts = sorted(
                {normalize_agent_id(n["agentId"]) for n in dag_spec["nodes"]},
                key=lambda x: x,
            )
            if not parts:
                parts = ["main"]
            dag_progress = {
                str(n["id"]): {"status": "pending", "outputPreview": ""} for n in dag_spec["nodes"]
            }
            dag_node_sess = {str(n["id"]): str(uuid.uuid4()) for n in dag_spec["nodes"]}
            agent_sess = {p: str(uuid.uuid4()) for p in parts}
            router_final = dict(router_llm) if isinstance(router_llm, dict) and router_llm else None
        else:
            if strat.lower() == "dag":
                raise ValueError("dag spec is required when strategy is dag")
            if strat.lower() == "supervisor_pipeline":
                src = supervisor_pipeline if supervisor_pipeline else participants
                pl = [normalize_agent_id(str(x).strip()) for x in src if str(x).strip()]
                if not pl:
                    raise ValueError("supervisor_pipeline requires at least one agent")
                parts = list(dict.fromkeys(pl))
                agent_sess = {p: str(uuid.uuid4()) for p in parts}
                sup_pipeline = pl
                sup_llm = dict(supervisor_llm) if isinstance(supervisor_llm, dict) and supervisor_llm else None
                strat = "supervisor_pipeline"
                router_final = None
            else:
                parts = [normalize_agent_id(x) for x in participants if str(x).strip()]
                parts = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
                if not parts:
                    parts = ["main"]
                agent_sess = {p: str(uuid.uuid4()) for p in parts}
                router_final = dict(router_llm) if isinstance(router_llm, dict) and router_llm else None

        roles_stored: Optional[Dict[str, str]] = None
        if dag is None and strat.lower() == "router_llm":
            roles_stored = _patch_router_agent_roles(None, router_agent_roles, parts)

        now = _now_ms()
        st = OrchState(
            orchId=orch_id,
            sessionKey=session_key,
            createdAt=now,
            updatedAt=now,
            status="idle",
            name=(name or "").strip(),
            strategy=strat,
            maxRounds=max(1, int(max_rounds or 8)),
            routerLlm=router_final,
            routerAgentRoles=roles_stored,
            participants=parts,
            agentSessions=agent_sess,
            currentRound=0,
            messages=[],
            orchSchemaVersion=1,
            dagSpec=dag_spec,
            dagProgress=dag_progress,
            dagParallelism=dag_parallelism,
            dagNodeSessions=dag_node_sess,
            pendingDirectAgent=None,
            pendingSingleTurn=False,
            supervisorPipeline=sup_pipeline,
            supervisorLlm=sup_llm,
            supervisorMaxIterations=smi_default if sup_pipeline else 5,
            supervisorLlmMaxRetries=smr_default if sup_pipeline else 12,
            supervisorIteration=0,
            supervisorLastDecision=None,
        )
        if dag is not None:
            st.supervisorPipeline = []
            st.supervisorLlm = None
            st.supervisorMaxIterations = 5
            st.supervisorLlmMaxRetries = 12
        self._save(st)
        return st

    def update(
        self,
        orch_id: str,
        *,
        session_key: str,
        name: str,
        participants: List[str],
        max_rounds: int = 8,
        strategy: str = "round_robin",
        router_llm: Optional[Dict[str, str]] = None,
        router_agent_roles: Optional[Dict[str, Any]] = None,
        dag: Optional[Dict[str, Any]] = None,
        supervisor_pipeline: Optional[List[str]] = None,
        supervisor_llm: Optional[Dict[str, str]] = None,
        supervisor_max_iterations: Optional[int] = None,
        supervisor_llm_max_retries: Optional[int] = None,
    ) -> OrchState:
        """Update orchestration metadata. Refuses when ``status == running``."""
        oid = (orch_id or "").strip()
        if not oid:
            raise ValueError("orchId is required")
        st = self._load(oid)
        if not st:
            raise ValueError("orchestration not found")
        if (st.status or "").strip() == "running":
            raise ValueError("orchestration is running")

        strat = (strategy or "round_robin").strip() or "round_robin"
        dag_spec: Optional[Dict[str, Any]] = None
        dag_progress: Optional[Dict[str, Any]] = None
        dag_parallelism = 4
        dag_node_sess: Dict[str, str] = {}
        parts: List[str]
        agent_sess: Dict[str, str]
        router_llm_out: Optional[Dict[str, str]]

        if dag is not None:
            dag_spec = normalize_dag_dict(dag if isinstance(dag, dict) else {})
            strat = "dag"
            dag_parallelism = max(1, min(32, int(dag_spec.get("parallelism") or 4)))
            parts = sorted(
                {normalize_agent_id(n["agentId"]) for n in dag_spec["nodes"]},
                key=lambda x: x,
            )
            if not parts:
                parts = ["main"]
            dag_progress = {
                str(n["id"]): {"status": "pending", "outputPreview": ""} for n in dag_spec["nodes"]
            }
            old_ns = dict(st.dagNodeSessions or {})
            dag_node_sess = {
                str(n["id"]): old_ns.get(str(n["id"])) or str(uuid.uuid4())
                for n in dag_spec["nodes"]
            }
            old_as = dict(st.agentSessions or {})
            agent_sess = {p: old_as.get(p) or str(uuid.uuid4()) for p in parts}
            router_llm_out = None
            st.routerAgentRoles = None
            st.supervisorPipeline = []
            st.supervisorLlm = None
            st.supervisorMaxIterations = 5
            st.supervisorLlmMaxRetries = 12
            st.supervisorIteration = 0
            st.supervisorLastDecision = None
        else:
            if strat.lower() == "dag":
                raise ValueError("dag spec is required when strategy is dag")
            dag_spec = None
            dag_progress = None
            dag_parallelism = 4
            dag_node_sess = {}
            if strat.lower() == "supervisor_pipeline":
                src = supervisor_pipeline if supervisor_pipeline else participants
                pl = [normalize_agent_id(str(x).strip()) for x in src if str(x).strip()]
                if not pl:
                    raise ValueError("supervisor_pipeline requires at least one agent")
                parts = list(dict.fromkeys(pl))
                old_as = dict(st.agentSessions or {})
                agent_sess = {p: old_as.get(p) or str(uuid.uuid4()) for p in parts}
                st.supervisorPipeline = pl
                st.supervisorLlm = self._patch_router_llm(st.supervisorLlm, supervisor_llm)
                try:
                    smi = (
                        int(supervisor_max_iterations)
                        if supervisor_max_iterations is not None
                        else int(st.supervisorMaxIterations or 5)
                    )
                except (TypeError, ValueError):
                    smi = 5
                st.supervisorMaxIterations = max(1, min(64, smi))
                try:
                    smr = (
                        int(supervisor_llm_max_retries)
                        if supervisor_llm_max_retries is not None
                        else int(st.supervisorLlmMaxRetries or 12)
                    )
                except (TypeError, ValueError):
                    smr = 12
                st.supervisorLlmMaxRetries = max(0, min(64, smr))
                st.supervisorIteration = 0
                st.supervisorLastDecision = None
                router_llm_out = None
                strat = "supervisor_pipeline"
                st.routerAgentRoles = None
            else:
                parts = [normalize_agent_id(x) for x in participants if str(x).strip()]
                parts = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
                if not parts:
                    parts = ["main"]
                old_as = dict(st.agentSessions or {})
                agent_sess = {p: old_as.get(p) or str(uuid.uuid4()) for p in parts}
                if strat == "router_llm":
                    router_llm_out = self._patch_router_llm(st.routerLlm, router_llm)
                    st.routerAgentRoles = _patch_router_agent_roles(
                        st.routerAgentRoles, router_agent_roles, parts
                    )
                else:
                    router_llm_out = None
                    st.routerAgentRoles = None
                st.supervisorPipeline = []
                st.supervisorLlm = None
                st.supervisorMaxIterations = 5
                st.supervisorLlmMaxRetries = 12
                st.supervisorIteration = 0
                st.supervisorLastDecision = None

        sk = (session_key or "").strip()
        st.sessionKey = sk or st.sessionKey
        st.name = (name or "").strip()
        st.strategy = strat
        st.maxRounds = max(1, int(max_rounds or 8))
        st.participants = parts
        st.agentSessions = agent_sess
        st.dagSpec = dag_spec
        st.dagProgress = dag_progress
        st.dagParallelism = dag_parallelism
        st.dagNodeSessions = dag_node_sess
        st.routerLlm = router_llm_out
        st.error = None
        st.pendingDirectAgent = None
        st.pendingSingleTurn = False
        self._save(st)
        return st

    def run(
        self,
        *,
        session_key: str,
        message: str,
        participants: List[str],
        max_rounds: int = 8,
        strategy: str = "round_robin",
        router_llm: Optional[Dict[str, str]] = None,
        router_agent_roles: Optional[Dict[str, Any]] = None,
        dag: Optional[Dict[str, Any]] = None,
    ) -> OrchState:
        # Back-compat: create + send
        st = self.create(
            session_key=session_key,
            name="",
            participants=participants,
            max_rounds=max_rounds,
            strategy=strategy,
            router_llm=router_llm,
            router_agent_roles=router_agent_roles,
            dag=dag,
        )
        self.send(orch_id=st.orchId, message=message)
        return self._load(st.orchId) or st

    def send(
        self,
        *,
        orch_id: str,
        message: str,
        reasoning_level: Optional[str] = None,
        target_agent: Optional[str] = None,
    ) -> OrchState:
        st = self._load(orch_id)
        if not st:
            raise ValueError("orchestration not found")
        if st.status == "running":
            raise ValueError("orchestration is running")
        text = (message or "").strip()
        if not text:
            raise ValueError("message required")
        strat = (st.strategy or "").strip()
        norm_target: Optional[str] = None
        if target_agent and str(target_agent).strip():
            tid = normalize_agent_id(str(target_agent).strip())
            if tid in st.participants:
                norm_target = tid
        if strat == "dag" or strat == "supervisor_pipeline":
            st.pendingDirectAgent = None
            st.pendingSingleTurn = False
        else:
            st.pendingDirectAgent = norm_target
            st.pendingSingleTurn = bool(norm_target)
        if reasoning_level is not None:
            v = str(reasoning_level).strip().lower()
            st.orchReasoningLevel = v if v in ("off", "on", "stream") else "stream"
        st.status = "running"
        st.error = None
        if (st.strategy or "").strip() == "dag" and st.dagSpec and isinstance(st.dagSpec.get("nodes"), list):
            nodes_raw = st.dagSpec["nodes"]
            st.dagProgress = {
                str(n.get("id") or ""): {"status": "pending", "outputPreview": ""}
                for n in nodes_raw
                if isinstance(n, dict) and str(n.get("id") or "").strip()
            }
        st.messages.append(
            OrchMessage(
                id=str(uuid.uuid4()),
                ts=_now_ms(),
                round=st.currentRound,
                speaker="user",
                role="user",
                text=text,
            )
        )
        self._save(st)
        self._start_background(st.orchId)
        return st

    def _start_background(self, orch_id: str) -> None:
        if orch_id in self._tasks and not self._tasks[orch_id].done():
            return

        st0 = self._load(orch_id)
        if st0 and (st0.strategy or "").strip() == "dag":
            self._tasks[orch_id] = asyncio.create_task(self._task_dag(orch_id))
            return
        if st0 and (st0.strategy or "").strip() == "supervisor_pipeline":
            self._tasks[orch_id] = asyncio.create_task(self._task_supervisor_pipeline(orch_id))
            return

        async def _task_linear() -> None:
            st = self._load(orch_id)
            if not st:
                return
            if st.status != "running":
                return
            try:
                direct = (getattr(st, "pendingDirectAgent", None) or "").strip()
                single = bool(getattr(st, "pendingSingleTurn", False))
                st.pendingDirectAgent = None
                st.pendingSingleTurn = False
                self._save(st)

                last_text = st.messages[-1].text if st.messages else ""
                start_round = int(st.currentRound or 0)
                if single and direct and direct in st.participants:
                    target_round = start_round + 1
                else:
                    target_round = start_round + int(st.maxRounds or 8)
                for r in range(start_round, target_round):
                    st = self._load(orch_id)
                    if not st or st.status != "running":
                        return
                    # Speaker selection (phase A):
                    # - @mention: first turn to that participant (single-turn when set in send)
                    # - round_robin: deterministic rotation
                    # - router_llm: choose via LLM if configured (skipped when @mention picks speaker)
                    agent_id = st.participants[r % max(1, len(st.participants))]
                    direct_first = r == start_round and bool(direct) and direct in st.participants
                    if direct_first:
                        agent_id = direct
                    elif (st.strategy or "").strip() == "router_llm" and st.routerLlm:
                        try:
                            router = st.routerLlm
                            orig_u = _last_user_message_text(st.messages)
                            tx = _format_transcript_since_last_user(
                                st.messages, _ROUTER_LLM_TRANSCRIPT_MAX_CHARS
                            )
                            batch_turns = max(1, target_round - start_round)
                            cur_turn = r - start_round + 1
                            prompt = _build_router_llm_user_prompt(
                                participants=st.participants,
                                original_user=orig_u or last_text,
                                transcript=tx,
                                last_immediate=last_text,
                                turn_1based=cur_turn,
                                max_turns=batch_turns,
                                agent_roles=getattr(st, "routerAgentRoles", None),
                            )
                            provider = (router.get("provider") or "").strip() or "openai"
                            model = (router.get("model") or "").strip() or "gpt-4o-mini"
                            base_url = (router.get("base_url") or router.get("baseUrl") or "").strip()
                            api_key = (router.get("api_key") or router.get("apiKey") or "").strip()
                            thinking_level = (router.get("thinking_level") or router.get("thinkingLevel") or "").strip()
                            if provider not in ("", "echo") and provider not in list_providers():
                                # Still allow "openai" even if not registered in list_providers() (it is).
                                provider = "openai"
                            # Router uses a minimal OpenAI-compatible caller.
                            reply, _usage = await asyncio.to_thread(
                                _call_openai_chat,
                                prompt,
                                model=model,
                                api_key=api_key or "none",
                                base_url=base_url or "https://api.openai.com",
                                extra_body=_thinking_extra_body(provider, thinking_level),
                                agent_id=f"{orch_id}:router",
                            )
                            pick = _parse_router_agent_pick(reply or "", st.participants)
                            if pick:
                                agent_id = pick
                        except Exception as e:
                            logger.warning(
                                "router_llm: speaker pick failed; using round-robin fallback: %s",
                                e,
                            )
                    session_id = st.agentSessions.get(agent_id) or str(uuid.uuid4())
                    st.agentSessions[agent_id] = session_id

                    cfg = self.agent_manager.get_or_create(agent_id)
                    workspace_dir = _orch_agent_workspace(orch_id, agent_id)
                    bootstrap = load_bootstrap_for_orchestration(cfg.workspace_dir, workspace_dir)
                    is_router_llm = (st.strategy or "").strip() == "router_llm"
                    orch_hint = (
                        (
                            "You are part of a multi-agent orchestration (dynamic router).\n"
                            "When your message includes [Original user request] and [Previous agent output], "
                            "fulfill the user's goal while building on prior agents' work.\n"
                            "Reply concisely, and include actionable outputs.\n"
                        )
                        if is_router_llm
                        else (
                            "You are part of a multi-agent orchestration.\n"
                            "Reply concisely, and include actionable outputs.\n"
                        )
                    ) + _ORCH_LANGUAGE_HINT_EN_ZH
                    extra_system_prompt = f"{bootstrap}\n\n{orch_hint}".strip() if bootstrap else orch_hint

                    if r == start_round and st.messages and st.messages[-1].role == "user":
                        raw_u = st.messages[-1].text
                        agent_message = _strip_at_mentions(raw_u)
                        if not agent_message.strip():
                            agent_message = raw_u
                    elif is_router_llm and r > start_round:
                        ou = _last_user_message_text(st.messages)
                        if not (ou or "").strip():
                            ou = last_text
                        agent_message = (
                            "[Original user request]\n"
                            f"{ou}\n\n"
                            "[Previous agent output]\n"
                            f"{last_text}\n"
                        ).strip()
                    else:
                        agent_message = last_text

                    result = await self.runner.run(
                        AgentRunParams(
                            message=agent_message,
                            run_id=str(uuid.uuid4()),
                            session_key=f"orch:{orch_id}",
                            session_id=session_id,
                            agent_id=agent_id,
                            channel="orchestrator",
                            deliver=False,
                            extra_system_prompt=extra_system_prompt,
                            workspace_dir=workspace_dir,
                            reasoning_level=self._reasoning_level_for_orch(st),
                        )
                    )
                    out_text = "\n".join([p.text or "" for p in result.payloads if (p.text or "").strip()]).strip()
                    if not out_text:
                        out_text = "(no output)"
                    last_text = out_text

                    st = self._load(orch_id)
                    if not st:
                        return
                    st.currentRound = r + 1
                    st.messages.append(
                        OrchMessage(
                            id=str(uuid.uuid4()),
                            ts=_now_ms(),
                            round=r + 1,
                            speaker=agent_id,
                            role="assistant",
                            text=out_text,
                        )
                    )
                    self._save(st)

                st = self._load(orch_id)
                if st and st.status == "running":
                    st.status = "idle"
                    self._save(st)
            except Exception as e:
                st = self._load(orch_id)
                if st:
                    st.status = "error"
                    st.error = str(e)
                    self._save(st)

        self._tasks[orch_id] = asyncio.create_task(_task_linear())

    def _supervisor_build_prompt(
        self,
        *,
        original_user_text: str,
        pipeline: List[str],
        c_output: str,
        macro: int,
        max_macro: int,
        brief_hint: str,
    ) -> str:
        pipe_s = " → ".join(pipeline)
        tail = (
            f"Supervisor brief from previous decision:\n{brief_hint}\n\n"
            if (brief_hint or "").strip()
            else ""
        )
        return (
            "You are the orchestration supervisor. After each pipeline run (agents in fixed order), "
            "decide whether to run another macro-iteration.\n\n"
            f"User goal:\n{original_user_text}\n\n"
            f"Pipeline order: {pipe_s}\n"
            f"Macro-iteration index (0-based): {macro}\n"
            f"Max macro-iterations (hard cap): {max_macro}\n\n"
            f"Latest pipeline final output (last agent in order):\n{_truncate_text(c_output, 8000)}\n\n"
            f"{tail}"
            "Reply with ONLY valid JSON, no markdown:\n"
            '{"action":"continue"|"stop","reason":"short",'
            '"brief_for_next_stroke":"required when action is continue — concrete instruction for '
            'the first pipeline agent on the next iteration",'
            '"final_user_visible_summary":"optional when action is stop"}\n\n'
            "Rules: If the user goal is satisfied, use action stop. "
            "If more refinement is needed, use continue and a clear brief_for_next_stroke."
        )

    async def _supervisor_call_llm(self, orch_id: str, sup: Dict[str, str], prompt: str) -> str:
        router = sup
        provider = (router.get("provider") or "").strip() or "openai"
        model = (router.get("model") or "").strip() or "gpt-4o-mini"
        base_url = (router.get("base_url") or router.get("baseUrl") or "").strip()
        api_key = (router.get("api_key") or router.get("apiKey") or "").strip()
        thinking_level = (router.get("thinking_level") or router.get("thinkingLevel") or "").strip()
        if provider not in ("", "echo") and provider not in list_providers():
            provider = "openai"
        reply, _usage = await asyncio.to_thread(
            _call_openai_chat,
            prompt,
            model=model,
            api_key=api_key or "none",
            base_url=base_url or "https://api.openai.com",
            extra_body=_thinking_extra_body(provider, thinking_level),
            agent_id=f"{orch_id}:supervisor",
        )
        return reply or ""

    async def _supervisor_call_llm_with_retries(
        self,
        orch_id: str,
        sup: Dict[str, str],
        prompt: str,
        max_retries: int,
    ) -> str:
        """On transport/API failure or empty body, wait and retry.

        ``max_retries``: number of retries after the first attempt (total calls ≤ 1 + max_retries).
        """
        mr = max(0, min(64, int(max_retries)))
        last_exc: Optional[BaseException] = None
        for attempt in range(mr + 1):
            try:
                raw = await self._supervisor_call_llm(orch_id, sup, prompt)
                if (raw or "").strip():
                    return raw
                raise ValueError("empty supervisor LLM response")
            except Exception as e:
                last_exc = e
                if attempt < mr:
                    await _supervisor_retry_delay()
                else:
                    break
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("supervisor LLM retry exhausted")

    async def _task_supervisor_pipeline(self, orch_id: str) -> None:
        c_output = ""
        try:
            st = self._load(orch_id)
            if not st or st.status != "running":
                return
            pipeline = [normalize_agent_id(x) for x in (st.supervisorPipeline or []) if str(x).strip()]
            if not pipeline:
                st.status = "error"
                st.error = "supervisorPipeline is empty"
                self._save(st)
                return
            sup = st.supervisorLlm or {}
            if not sup:
                st.status = "error"
                st.error = "supervisorLlm is not configured"
                self._save(st)
                return
            max_macro = max(1, min(64, int(st.supervisorMaxIterations or 5)))
            max_sup_llm_retries = max(0, min(64, int(st.supervisorLlmMaxRetries or 12)))
            user_msgs = [m for m in st.messages if m.role == "user"]
            original_user_text = user_msgs[-1].text if user_msgs else ""
            brief_for_next = ""
            macro = 0
            next_r = int(st.currentRound or 0) + 1

            while macro < max_macro:
                st = self._load(orch_id)
                if not st or st.status != "running":
                    return
                if macro == 0:
                    stroke_input = original_user_text
                else:
                    stroke_input = (
                        f"{original_user_text}\n\n"
                        f"[Supervisor macro-iteration {macro}]\n"
                        f"{brief_for_next}\n\n"
                        f"[Previous pipeline final output]\n{_truncate_text(c_output, 4000)}"
                    )
                last_text = stroke_input
                for agent_id in pipeline:
                    st = self._load(orch_id)
                    if not st or st.status != "running":
                        return
                    session_id = st.agentSessions.get(agent_id) or str(uuid.uuid4())
                    st.agentSessions[agent_id] = session_id
                    cfg = self.agent_manager.get_or_create(agent_id)
                    workspace_dir = _orch_agent_workspace(orch_id, agent_id)
                    bootstrap = load_bootstrap_for_orchestration(cfg.workspace_dir, workspace_dir)
                    orch_hint = (
                        "You are part of a multi-agent supervisor pipeline orchestration.\n"
                        "Reply concisely, and include actionable outputs.\n"
                    ) + _ORCH_LANGUAGE_HINT_EN_ZH
                    extra_system_prompt = f"{bootstrap}\n\n{orch_hint}".strip() if bootstrap else orch_hint
                    if agent_id == pipeline[0]:
                        agent_message = _strip_at_mentions(last_text)
                        if not agent_message.strip():
                            agent_message = last_text
                    else:
                        agent_message = last_text
                    result = await self.runner.run(
                        AgentRunParams(
                            message=agent_message,
                            run_id=str(uuid.uuid4()),
                            session_key=f"orch:{orch_id}",
                            session_id=session_id,
                            agent_id=agent_id,
                            channel="orchestrator",
                            deliver=False,
                            extra_system_prompt=extra_system_prompt,
                            workspace_dir=workspace_dir,
                            reasoning_level=self._reasoning_level_for_orch(st),
                        )
                    )
                    out_text = "\n".join([p.text or "" for p in result.payloads if (p.text or "").strip()]).strip()
                    if not out_text:
                        out_text = "(no output)"
                    last_text = out_text
                    st = self._load(orch_id)
                    if not st:
                        return
                    st.currentRound = next_r
                    st.messages.append(
                        OrchMessage(
                            id=str(uuid.uuid4()),
                            ts=_now_ms(),
                            round=next_r,
                            speaker=agent_id,
                            role="assistant",
                            text=out_text,
                        )
                    )
                    next_r += 1
                    self._save(st)
                c_output = last_text
                prompt = self._supervisor_build_prompt(
                    original_user_text=original_user_text,
                    pipeline=pipeline,
                    c_output=c_output,
                    macro=macro,
                    max_macro=max_macro,
                    brief_hint=brief_for_next,
                )
                raw = await self._supervisor_call_llm_with_retries(
                    orch_id, sup, prompt, max_sup_llm_retries
                )
                try:
                    decision = _parse_supervisor_decision(raw)
                except ValueError as e:
                    st = self._load(orch_id)
                    if st:
                        st.status = "error"
                        st.error = f"supervisor: {e}"
                        self._save(st)
                    return
                st = self._load(orch_id)
                if not st or st.status != "running":
                    return
                st.supervisorIteration = macro + 1
                st.supervisorLastDecision = decision
                self._save(st)
                if decision.get("action") == "stop":
                    st = self._load(orch_id)
                    if not st:
                        return
                    summary = str(decision.get("final_user_visible_summary") or "").strip()
                    if summary:
                        st.messages.append(
                            OrchMessage(
                                id=str(uuid.uuid4()),
                                ts=_now_ms(),
                                round=next_r,
                                speaker="supervisor",
                                role="assistant",
                                text=summary,
                            )
                        )
                        st.currentRound = next_r
                        next_r += 1
                    st.status = "idle"
                    self._save(st)
                    return
                brief_for_next = str(decision.get("brief_for_next_stroke") or "").strip() or (
                    "Refine using the last pipeline output; close gaps vs the user goal."
                )
                macro += 1

            st = self._load(orch_id)
            if st and st.status == "running":
                st.status = "idle"
                st.messages.append(
                    OrchMessage(
                        id=str(uuid.uuid4()),
                        ts=_now_ms(),
                        round=next_r,
                        speaker="supervisor",
                        role="assistant",
                        text=f"Stopped after {max_macro} macro-iteration(s) (cap).",
                    )
                )
                st.currentRound = next_r
                self._save(st)
        except Exception as e:
            st = self._load(orch_id)
            if st:
                st.status = "error"
                st.error = str(e)
                self._save(st)

    async def _run_single_dag_node(
        self,
        orch_id: str,
        *,
        nid: str,
        node: Dict[str, Any],
        orig_user_message: str,
        outputs: Dict[str, str],
    ) -> str:
        """Execute one DAG node; returns assistant text (may raise)."""
        agent_id = normalize_agent_id(str(node.get("agentId") or "main"))
        parts: List[str] = [f"[Orchestration task]\n{orig_user_message}\n"]
        for dep in node.get("dependsOn") or []:
            ds = str(dep).strip()
            raw = (outputs.get(ds) or "").strip()
            snippet = raw[:MAX_UPSTREAM_SNIPPET] if raw else "(no output)"
            parts.append(f"\n## Upstream node `{ds}`\n{snippet}\n")
        full_message = "".join(parts)

        st = self._load(orch_id)
        if not st:
            return "(no state)"
        rl = self._reasoning_level_for_orch(st)
        session_id = (st.dagNodeSessions or {}).get(nid) or str(uuid.uuid4())
        st.dagNodeSessions[nid] = session_id
        self._save(st)

        cfg = self.agent_manager.get_or_create(agent_id)
        workspace_dir = _orch_agent_workspace(orch_id, agent_id)
        bootstrap = load_bootstrap_for_orchestration(cfg.workspace_dir, workspace_dir)
        orch_hint = (
            "You are part of a multi-agent DAG orchestration.\n"
            f"Current node id: {nid!r}. Reply concisely with actionable output.\n"
        ) + _ORCH_LANGUAGE_HINT_EN_ZH
        extra_system_prompt = f"{bootstrap}\n\n{orch_hint}".strip() if bootstrap else orch_hint

        result = await self.runner.run(
            AgentRunParams(
                message=full_message,
                run_id=str(uuid.uuid4()),
                session_key=f"orch:{orch_id}",
                session_id=session_id,
                agent_id=agent_id,
                channel="orchestrator",
                deliver=False,
                extra_system_prompt=extra_system_prompt,
                workspace_dir=workspace_dir,
                reasoning_level=rl,
            )
        )
        out_text = "\n".join([p.text or "" for p in result.payloads if (p.text or "").strip()]).strip()
        if not out_text:
            out_text = "(no output)"
        return out_text

    async def _task_dag(self, orch_id: str) -> None:
        try:
            st = self._load(orch_id)
            if not st or st.status != "running" or not st.dagSpec:
                return
            try:
                spec = normalize_dag_dict(dict(st.dagSpec))
            except ValueError as e:
                st = self._load(orch_id)
                if st:
                    st.status = "error"
                    st.error = str(e)
                    self._save(st)
                return

            nodes = spec["nodes"]
            nodes_by_id = {str(n["id"]): n for n in nodes}
            all_ids = sorted(nodes_by_id.keys(), key=lambda x: x)
            children: Dict[str, List[str]] = {i: [] for i in all_ids}
            rem: Dict[str, int] = {i: 0 for i in all_ids}
            for nid, n in nodes_by_id.items():
                deps = [str(d).strip() for d in (n.get("dependsOn") or []) if str(d).strip()]
                rem[nid] = len(deps)
                for d in deps:
                    if d in children:
                        children[d].append(nid)

            user_msgs = [m for m in st.messages if m.role == "user"]
            orig_user_message = user_msgs[-1].text if user_msgs else ""

            outputs: Dict[str, str] = {}
            pending = set(all_ids)
            wave = 0
            par = max(1, min(32, int(st.dagParallelism or spec.get("parallelism") or 4)))
            sem = asyncio.Semaphore(par)

            while pending:
                ready = sorted([nid for nid in pending if rem.get(nid, 0) == 0])
                if not ready:
                    st = self._load(orch_id)
                    if st:
                        st.status = "error"
                        st.error = "dag scheduling stuck (cycle or invalid state)"
                        self._save(st)
                    return
                wave += 1

                async def _bound(nid: str) -> tuple:
                    async with sem:
                        st2 = self._load(orch_id)
                        if not st2 or st2.status != "running":
                            return nid, None, "aborted"
                        prog = st2.dagProgress or {}
                        ent = dict(prog.get(nid) or {})
                        ent["status"] = "running"
                        prog[nid] = ent
                        st2.dagProgress = prog
                        self._save(st2)
                        try:
                            text = await self._run_single_dag_node(
                                orch_id,
                                nid=nid,
                                node=nodes_by_id[nid],
                                orig_user_message=orig_user_message,
                                outputs=outputs,
                            )
                            return nid, text, None
                        except Exception as ex:
                            return nid, None, str(ex)

                batch = await asyncio.gather(*[_bound(nid) for nid in ready])
                for item in batch:
                    nid, text, err = item
                    if err:
                        st = self._load(orch_id)
                        if st:
                            st.status = "error"
                            st.error = err or f"node {nid} failed"
                            prog = dict(st.dagProgress or {})
                            prog[nid] = {
                                "status": "error",
                                "outputPreview": "",
                                "error": err,
                            }
                            st.dagProgress = prog
                            self._save(st)
                        return
                    assert text is not None
                    outputs[nid] = text
                    st = self._load(orch_id)
                    if not st or st.status != "running":
                        return
                    preview = text[:2000]
                    prog = dict(st.dagProgress or {})
                    prog[nid] = {"status": "done", "outputPreview": preview, "error": ""}
                    st.dagProgress = prog
                    st.currentRound = wave
                    agent_id = normalize_agent_id(str(nodes_by_id[nid].get("agentId") or "main"))
                    st.messages.append(
                        OrchMessage(
                            id=str(uuid.uuid4()),
                            ts=_now_ms(),
                            round=wave,
                            speaker=agent_id,
                            role="assistant",
                            text=text,
                            nodeId=nid,
                        )
                    )
                    self._save(st)
                    pending.discard(nid)
                    for c in children.get(nid) or []:
                        rem[c] = max(0, rem.get(c, 0) - 1)

            st = self._load(orch_id)
            if st and st.status == "running":
                st.status = "idle"
                self._save(st)
        except Exception as e:
            st = self._load(orch_id)
            if st:
                st.status = "error"
                st.error = str(e)
                self._save(st)

