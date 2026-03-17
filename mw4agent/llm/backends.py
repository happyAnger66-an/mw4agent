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


def _call_openai_chat(
    prompt: str,
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    model: str,
    api_key: str,
    base_url: str,
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
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    obj = json.loads(raw)
    text = (
        obj.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
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
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    logger.debug(f'llm request {url}')
    req = urllib.request.Request(url=url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    obj = json.loads(raw)
    logger.debug(f'llm response {obj}')
    choice = obj.get("choices", [{}])[0] or {}
    msg = choice.get("message") or {}
    content = msg.get("content")
    if content is not None and not isinstance(content, str):
        content = str(content)
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
    cfg = _load_llm_config()
    cfg_provider = ""
    cfg_model = ""
    cfg_base_url: Optional[str] = None
    cfg_api_key: Optional[str] = None
    if isinstance(cfg, dict):
        cfg_provider = str(cfg.get("provider") or "").strip().lower()
        cfg_model = str(cfg.get("model") or cfg.get("model_id") or "").strip()
        raw_base = str(cfg.get("base_url") or "").strip()
        cfg_base_url = raw_base or None
        raw_key = str(cfg.get("api_key") or "").strip()
        cfg_api_key = raw_key or None

    provider = (
        params.provider
        or os.getenv("MW4AGENT_LLM_PROVIDER")
        or cfg_provider
        or "echo"
    ).strip().lower()
    model = (
        params.model
        or os.getenv("MW4AGENT_LLM_MODEL")
        or cfg_model
    ).strip()

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

    base_url = cfg_base_url or os.getenv("MW4AGENT_LLM_BASE_URL", "").strip() or (spec.default_base_url or "")
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
        )
        return reply, [], provider, model or spec.default_model, usage

    try:
        content, tool_calls, usage = _call_openai_chat_with_tools(
            messages,
            tools_openai,
            model=model or spec.default_model or "gpt-4o-mini",
            api_key=api_key or "none",
            base_url=base_url,
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
    cfg = _load_llm_config()
    cfg_provider = ""
    cfg_model = ""
    cfg_base_url: Optional[str] = None
    cfg_api_key: Optional[str] = None
    if isinstance(cfg, dict):
        cfg_provider = str(cfg.get("provider") or "").strip().lower()
        cfg_model = str(cfg.get("model") or cfg.get("model_id") or "").strip()
        raw_base = str(cfg.get("base_url") or "").strip()
        cfg_base_url = raw_base or None
        raw_key = str(cfg.get("api_key") or "").strip()
        cfg_api_key = raw_key or None

    provider = (
        params.provider
        or os.getenv("MW4AGENT_LLM_PROVIDER")
        or cfg_provider
        or "echo"
    ).strip().lower()
    model = (
        params.model
        or os.getenv("MW4AGENT_LLM_MODEL")
        or cfg_model
    ).strip()

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

    # Resolve base_url: config > env > spec default
    base_url = cfg_base_url or os.getenv("MW4AGENT_LLM_BASE_URL", "").strip() or spec.default_base_url or ""
    if spec.base_url_required and not base_url:
        reply = f"Agent (echo:no-base-url:{provider}) reply: {params.message}"
        return reply, "echo", model, LLMUsage()

    # Resolve api_key: config > env
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
        )
        return text or "", provider, model or spec.default_model, usage
    except Exception as e:
        fallback = f"Agent ({provider}-error) reply: {params.message}\n\n[error: {e}]"
        return fallback, provider, model, LLMUsage()


def list_providers() -> Tuple[str, ...]:
    """Return registered OpenAI-compatible provider ids (excluding echo)."""
    return tuple(_OPENAI_COMPAT_SPECS.keys())
