"""LLM backends: extensible provider registry (echo, OpenAI, DeepSeek, vLLM, etc.).

Design:
- Default backend is 'echo' (no external calls) for stable tests.
- HTTP-based providers use a single OpenAI-compatible Chat Completions caller.
- New providers are added by registering a ProviderSpec in _OPENAI_COMPAT_SPECS;
  each spec defines default base_url, default model, API key env var, and requirements.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..agents.types import AgentRunParams
from ..config import get_default_config_manager
from mw4agent.log import get_logger
logger = get_logger(__name__)

# One tool call from API: id, name, arguments (JSON string or dict)
ToolCallPayload = Dict[str, Any]
# One tool definition for API: name, description, parameters (JSON Schema)
ToolDefPayload = Dict[str, Any]


@dataclass
class LLMUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass
class _ProviderSpec:
    """Spec for an OpenAI-compatible HTTP provider. Add an entry here to support a new provider."""

    default_base_url: Optional[str] = None  # None = must come from config/env
    default_model: str = ""
    api_key_env: str = "MW4AGENT_LLM_API_KEY"
    require_api_key: bool = True
    base_url_required: bool = False  # True = no default_base_url, must set in config/env


# Registry: provider_id -> ProviderSpec. Extend this to add new providers.
_OPENAI_COMPAT_SPECS: Dict[str, _ProviderSpec] = {
    "openai": _ProviderSpec(
        default_base_url="https://api.openai.com",
        default_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        require_api_key=True,
    ),
    "deepseek": _ProviderSpec(
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        require_api_key=True,
    ),
    "vllm": _ProviderSpec(
        default_base_url=None,
        default_model="",
        api_key_env="MW4AGENT_LLM_API_KEY",
        require_api_key=False,
        base_url_required=True,
    ),
    "aliyun-bailian": _ProviderSpec(
        default_base_url=None,
        default_model="",
        api_key_env="MW4AGENT_LLM_API_KEY",
        require_api_key=False,
        base_url_required=True,
    ),
}


def _load_llm_config() -> Dict[str, Any]:
    """Load LLM config from the default config store (~/.mw4agent/mw4agent.json, section \"llm\")."""
    try:
        mgr = get_default_config_manager()
        cfg = mgr.read_config("llm", default={})
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _first_non_empty_str(*candidates: Optional[Any]) -> str:
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return ""


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _extract_text_and_reasoning_from_message(
    msg: Dict[str, Any],
    *,
    choice: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Extract visible content and reasoning from OpenAI-compatible payload.

    Covers common OpenAI-compatible variants and Qwen-specific ``reasoning_content``.
    """
    visible_parts: List[str] = []
    reasoning_parts: List[str] = []

    content = msg.get("content")
    if isinstance(content, str):
        visible_parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                visible_parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            itype = str(item.get("type") or "").strip().lower()
            txt = _as_text(
                item.get("reasoning_content")
                or item.get("text")
                or item.get("content")
            )
            if itype in ("reasoning", "thinking", "reasoning_content"):
                if txt.strip():
                    reasoning_parts.append(txt)
                continue
            if txt.strip():
                visible_parts.append(txt)

    top_reason = msg.get("reasoning_content") or msg.get("reasoning") or msg.get("thinking")
    top_reason_text = _as_text(top_reason).strip()
    if top_reason_text:
        reasoning_parts.append(top_reason_text)
    if isinstance(choice, dict):
        choice_reason = _as_text(
            choice.get("reasoning_content")
            or choice.get("reasoning")
            or choice.get("thinking")
        ).strip()
        if choice_reason:
            reasoning_parts.append(choice_reason)

    visible = "\n".join(p for p in visible_parts if _as_text(p).strip()).strip()
    reasoning = "\n".join(p for p in reasoning_parts if _as_text(p).strip()).strip()
    return visible, reasoning


def _merge_reasoning_into_content(content: str, reasoning: str) -> str:
    c = (content or "").strip()
    r = (reasoning or "").strip()
    if not r:
        return c
    if "<think" in c.lower():
        return c
    if c:
        return f"<think>{r}</think>\n{c}"
    return f"<think>{r}</think>"


def _load_agent_llm_overrides(agent_id: Optional[str]) -> Dict[str, Any]:
    """Per-agent llm fragment from ~/.mw4agent/agents/<agentId>/agent.json (key \"llm\")."""
    try:
        from ..agents.agent_manager import AgentManager
        from ..config.paths import normalize_agent_id

        aid = normalize_agent_id(agent_id)
        cfg = AgentManager().get(aid)
        if cfg is None or not cfg.llm:
            return {}
        return dict(cfg.llm)
    except Exception:
        return {}


def _normalize_thinking_level(raw: Optional[Any]) -> str:
    s = str(raw or "").strip().lower()
    if s in ("", "off", "false", "0", "none"):
        return "off"
    if s in ("on", "true", "1"):
        return "medium"
    if s in ("minimal", "low", "medium", "high", "xhigh", "adaptive"):
        return s
    return "off"


def _resolve_reasoning_effort_for_provider(provider: str, thinking_level: str) -> Optional[str]:
    lvl = _normalize_thinking_level(thinking_level)
    if lvl == "off":
        return None
    p = (provider or "").strip().lower()
    if p in ("openai", "deepseek"):
        if lvl in ("minimal", "low"):
            return "low"
        if lvl in ("high", "xhigh"):
            return "high"
        return "medium"
    if p in ("vllm", "aliyun-bailian"):
        if lvl in ("minimal", "low", "medium", "high"):
            return lvl
        if lvl == "xhigh":
            return "high"
        return "medium"
    return None


def _thinking_extra_body(provider: str, thinking_level: str) -> Dict[str, Any]:
    effort = _resolve_reasoning_effort_for_provider(provider, thinking_level)
    if not effort:
        return {}
    p = (provider or "").strip().lower()
    if p in ("openai", "deepseek"):
        return {"reasoning_effort": effort}
    if p in ("vllm", "aliyun-bailian"):
        return {"reasoning": {"effort": effort}}
    return {}


def _resolve_llm_settings(
    params: AgentRunParams,
) -> Tuple[str, str, Optional[str], Optional[str], str]:
    """Resolve provider, model, base_url, api_key for this run.

    Precedence per field (first non-empty wins):
    params.* → agent.json ``llm`` → global ``llm`` → environment (MW4AGENT_LLM_*).
    """
    g = _load_llm_config()
    if not isinstance(g, dict):
        g = {}
    a = _load_agent_llm_overrides(params.agent_id)

    provider = (
        _first_non_empty_str(
            params.provider,
            a.get("provider"),
            g.get("provider"),
            os.getenv("MW4AGENT_LLM_PROVIDER"),
        )
        or "echo"
    ).strip().lower()

    model = _first_non_empty_str(
        params.model,
        a.get("model"),
        a.get("model_id"),
        g.get("model"),
        g.get("model_id"),
        os.getenv("MW4AGENT_LLM_MODEL"),
    )

    base_url_s = _first_non_empty_str(
        a.get("base_url"),
        g.get("base_url"),
        os.getenv("MW4AGENT_LLM_BASE_URL"),
    )
    base_url: Optional[str] = base_url_s if base_url_s else None

    api_key_s = _first_non_empty_str(
        a.get("api_key"),
        g.get("api_key"),
        os.getenv("MW4AGENT_LLM_API_KEY"),
    )
    api_key: Optional[str] = api_key_s if api_key_s else None

    thinking_level = _normalize_thinking_level(
        _first_non_empty_str(
            params.thinking_level,
            a.get("thinking_level"),
            a.get("thinkingLevel"),
            g.get("thinking_level"),
            g.get("thinkingLevel"),
            os.getenv("MW4AGENT_LLM_THINKING_LEVEL"),
        )
    )

    return provider, model, base_url, api_key, thinking_level


def _call_openai_chat(
    prompt: str,
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    model: str,
    api_key: str,
    base_url: str,
    extra_body: Optional[Dict[str, Any]] = None,
    timeout_s: float = 30.0,
) -> Tuple[str, LLMUsage]:
    """Call an OpenAI-compatible Chat Completions API (minimal subset)."""
    base = base_url.rstrip("/")
    # Avoid double /v1 when user sets base_url to https://api.example.com/v1
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resolved_messages: List[Dict[str, Any]]
    if messages and isinstance(messages, list) and len(messages) > 0:
        resolved_messages = messages
    else:
        resolved_messages = [{"role": "user", "content": prompt}]

    body = {"model": model, "messages": resolved_messages, "temperature": 0.2}
    if isinstance(extra_body, dict) and extra_body:
        body.update(extra_body)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    obj = json.loads(raw)
    choice = obj.get("choices", [{}])[0] or {}
    msg = choice.get("message", {}) or {}
    text, reasoning = _extract_text_and_reasoning_from_message(msg, choice=choice)
    text = _merge_reasoning_into_content(text, reasoning)
    usage_obj = obj.get("usage") or {}
    usage = LLMUsage(
        input_tokens=usage_obj.get("prompt_tokens"),
        output_tokens=usage_obj.get("completion_tokens"),
        total_tokens=usage_obj.get("total_tokens"),
    )
    return text or "", usage


def _tools_to_openai_format(tool_definitions: List[ToolDefPayload]) -> List[Dict[str, Any]]:
    """Convert registry-style tool defs (name, description, parameters) to OpenAI tools array."""
    out = []
    for t in tool_definitions:
        name = t.get("name") or ""
        desc = t.get("description") or ""
        params = t.get("parameters")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": params,
            },
        })
    return out


def _call_openai_chat_with_tools(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    *,
    model: str,
    api_key: str,
    base_url: str,
    extra_body: Optional[Dict[str, Any]] = None,
    timeout_s: float = 60.0,
) -> Tuple[Optional[str], List[ToolCallPayload], LLMUsage]:
    """Call OpenAI Chat Completions with tools. Returns (content, tool_calls, usage).
    tool_calls items are {id, name, arguments} with arguments already parsed to dict.
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": 0.2,
    }
    if isinstance(extra_body, dict) and extra_body:
        body.update(extra_body)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    logger.debug(f'llm request {url}')
    req = urllib.request.Request(url=url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    obj = json.loads(raw)
    logger.debug(f'llm response {obj}')
    choice = obj.get("choices", [{}])[0] or {}
    msg = choice.get("message") or {}
    content, reasoning = _extract_text_and_reasoning_from_message(msg, choice=choice)
    content = _merge_reasoning_into_content(content, reasoning)
    # 部分 API 将 tool_calls 放在 choice 下而非 choice.message 下，兼容两种格式
    raw_tool_calls = msg.get("tool_calls") or choice.get("tool_calls") or []
    tool_calls: List[ToolCallPayload] = []
    for tc in raw_tool_calls:
        if not isinstance(tc, dict):
            continue
        tid = tc.get("id") or ""
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        args_raw = fn.get("arguments")
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        tool_calls.append({"id": tid, "name": name, "arguments": args})
    if tool_calls:
        logger.info(
            "llm returned %d tool_calls: %s",
            len(tool_calls),
            [t.get("name") for t in tool_calls],
        )
    usage_obj = obj.get("usage") or {}
    usage = LLMUsage(
        input_tokens=usage_obj.get("prompt_tokens"),
        output_tokens=usage_obj.get("completion_tokens"),
        total_tokens=usage_obj.get("total_tokens"),
    )
    return (content, tool_calls, usage)


def generate_reply_with_tools(
    params: AgentRunParams,
    messages: List[Dict[str, Any]],
    tool_definitions: List[ToolDefPayload],
) -> Tuple[Optional[str], List[ToolCallPayload], str, str, LLMUsage]:
    """One LLM round with tools. Returns (content, tool_calls, provider, model, usage).
    When provider is echo or tools unsupported, returns (reply_text, [], provider, model, usage).
    """
    provider, model, cfg_base_url, cfg_api_key, thinking_level = _resolve_llm_settings(params)

    if provider in ("", "echo", "debug"):
        default_model = "gpt-4o-mini"
        reply = f"Agent (echo) reply: {params.message}"
        return reply, [], "echo", model or default_model, LLMUsage()

    spec = _OPENAI_COMPAT_SPECS.get(provider)
    if spec and not model:
        model = spec.default_model or ""
    if spec is None:
        reply = f"Agent (unknown-provider:{provider}) reply: {params.message}"
        return reply, [], provider or "echo", model or "gpt-4o-mini", LLMUsage()

    base_url = (cfg_base_url or "").strip() or (spec.default_base_url or "")
    if spec.base_url_required and not base_url:
        reply = f"Agent (echo:no-base-url:{provider}) reply: {params.message}"
        return reply, [], "echo", model, LLMUsage()
    api_key = (cfg_api_key or os.getenv(spec.api_key_env, "").strip() or "")
    if spec.require_api_key and not api_key:
        reply = f"Agent (echo:no-api-key:{provider}) reply: {params.message}"
        return reply, [], "echo", model, LLMUsage()

    tools_openai = _tools_to_openai_format(tool_definitions)
    if not tools_openai:
        reply, usage = _call_openai_chat(
            params.message,
            model=model or spec.default_model or "gpt-4o-mini",
            api_key=api_key or "none",
            base_url=base_url,
            extra_body=_thinking_extra_body(provider, thinking_level),
        )
        return reply, [], provider, model or spec.default_model, usage

    try:
        content, tool_calls, usage = _call_openai_chat_with_tools(
            messages,
            tools_openai,
            model=model or spec.default_model or "gpt-4o-mini",
            api_key=api_key or "none",
            base_url=base_url,
            extra_body=_thinking_extra_body(provider, thinking_level),
        )
        return content, tool_calls, provider, model or spec.default_model, usage
    except Exception as e:
        fallback = f"Agent ({provider}-error) reply: {params.message}\n\n[error: {e}]"
        return fallback, [], provider, model, LLMUsage()


def generate_reply(params: AgentRunParams, *, messages: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, str, str, LLMUsage]:
    """Generate a reply for a single turn.

    Returns:
        reply_text, provider, model, usage
    """
    provider, model, cfg_base_url, cfg_api_key, thinking_level = _resolve_llm_settings(params)

    # Echo backend (default, local only)
    if provider in ("", "echo", "debug"):
        default_model = "gpt-4o-mini"  # for display only
        reply = f"Agent (echo) reply: {params.message}"
        return reply, "echo", model or default_model, LLMUsage()

    # Resolve model default from provider spec if registered
    spec = _OPENAI_COMPAT_SPECS.get(provider)
    if spec and not model:
        model = spec.default_model or ""

    # Unknown provider → echo
    if spec is None:
        reply = f"Agent (unknown-provider:{provider}) reply: {params.message}"
        return reply, provider or "echo", model or "gpt-4o-mini", LLMUsage()

    # Resolve base_url: merged config > spec default
    base_url = (cfg_base_url or "").strip() or spec.default_base_url or ""
    if spec.base_url_required and not base_url:
        reply = f"Agent (echo:no-base-url:{provider}) reply: {params.message}"
        return reply, "echo", model, LLMUsage()

    # Resolve api_key: merged config > provider env
    api_key = (cfg_api_key or os.getenv(spec.api_key_env, "").strip() or "")
    if spec.require_api_key and not api_key:
        reply = f"Agent (echo:no-api-key:{provider}) reply: {params.message}"
        return reply, "echo", model, LLMUsage()

    prompt = params.message
    if params.extra_system_prompt:
        prompt = params.extra_system_prompt.strip() + "\n\n" + prompt

    try:
        text, usage = _call_openai_chat(
            prompt,
            messages=messages,
            model=model or spec.default_model or "gpt-4o-mini",
            api_key=api_key or "none",
            base_url=base_url,
            extra_body=_thinking_extra_body(provider, thinking_level),
        )
        return text or "", provider, model or spec.default_model, usage
    except Exception as e:
        fallback = f"Agent ({provider}-error) reply: {params.message}\n\n[error: {e}]"
        return fallback, provider, model, LLMUsage()


def list_providers() -> Tuple[str, ...]:
    """Return registered OpenAI-compatible provider ids (excluding echo)."""
    return tuple(_OPENAI_COMPAT_SPECS.keys())
