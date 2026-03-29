"""Feishu cloud document tools via official Lark MCP.

与 feishu-openclaw-plugin 中 MCP 文档工具（fetch-doc / create-doc / update-doc）使用同一协议：
HTTP `tools/call` + 请求头 `X-Lark-MCP-UAT`。

UAT 来源优先级：环境变量 FEISHU_MCP_UAT 等 → mw4agent.json 明文字段 →
`mw4agent feishu authorize` 写入的 ~/.mw4agent/feishu_oauth.json（含 refresh 自动续期）。
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

import httpx

from mw4agent.agents.tools.base import AgentTool, ToolResult
from mw4agent.log import get_logger

logger = get_logger(__name__)

DEFAULT_MCP_ENDPOINT = "https://mcp.feishu.cn/mcp"


def _is_record(v: Any) -> bool:
    return isinstance(v, dict)


def unwrap_json_rpc_result(v: Any) -> Any:
    if not _is_record(v):
        return v
    has_jsonrpc = isinstance(v.get("jsonrpc"), str)
    has_result = "result" in v
    has_error = "error" in v
    if has_jsonrpc and (has_result or has_error):
        if has_error:
            err = v.get("error")
            if _is_record(err) and isinstance(err.get("message"), str):
                raise RuntimeError(str(err["message"]))
            raise RuntimeError("MCP returned error without message")
        return unwrap_json_rpc_result(v["result"])
    if not has_jsonrpc and "id" not in v and has_result and not has_error:
        return unwrap_json_rpc_result(v["result"])
    return v


def resolve_mcp_endpoint() -> str:
    u = (
        os.environ.get("FEISHU_MCP_ENDPOINT", "").strip()
        or os.environ.get("LARK_MCP_ENDPOINT", "").strip()
    )
    return u or DEFAULT_MCP_ENDPOINT


def resolve_mcp_bearer() -> Optional[str]:
    t = (
        os.environ.get("FEISHU_MCP_BEARER_TOKEN", "").strip()
        or os.environ.get("FEISHU_MCP_TOKEN", "").strip()
    )
    if not t:
        return None
    low = t.lower()
    return t if low.startswith("bearer ") else f"Bearer {t}"


def _resolve_feishu_app_for_oauth_store() -> tuple[Optional[str], Optional[str], str]:
    """Return (app_id, app_secret, brand) for reading ~/.mw4agent/feishu_oauth.json."""
    try:
        from mw4agent.channels.feishu_accounts import list_feishu_accounts
        from mw4agent.config.root import read_root_section

        ch = read_root_section("channels", default={})
        fs = ch.get("feishu") if isinstance(ch, dict) else None
        rows = list_feishu_accounts(
            fs if isinstance(fs, dict) else None,
            env_app_id=os.environ.get("FEISHU_APP_ID", "") or "",
            env_app_secret=os.environ.get("FEISHU_APP_SECRET", "") or "",
        )
        if not rows:
            return None, None, "feishu"
        want = os.environ.get("FEISHU_OAUTH_APP_ID", "").strip()
        brand_env = (os.environ.get("FEISHU_OAUTH_BRAND") or "").strip().lower()
        brand = brand_env if brand_env in ("feishu", "lark") else "feishu"
        for r in rows:
            if want and r.app_id != want:
                continue
            b = brand
            if not brand_env and r.api_base and "larksuite" in (r.api_base or "").lower():
                b = "lark"
            if r.app_id and r.app_secret:
                return r.app_id, r.app_secret, b
        return None, None, brand
    except Exception as e:
        logger.debug("resolve feishu app for oauth store: %s", e)
        return None, None, "feishu"


def _sender_open_id_for_uat(context: Optional[Dict[str, Any]]) -> Optional[str]:
    if not context:
        return None
    ch = str(context.get("channel") or "")
    if ch != "feishu" and not ch.startswith("feishu:"):
        return None
    sid = str(context.get("sender_id") or "").strip()
    return sid or None


def resolve_mcp_uat(context: Optional[Dict[str, Any]] = None) -> Optional[str]:
    for key in ("FEISHU_MCP_UAT", "LARK_MCP_UAT", "FEISHU_USER_ACCESS_TOKEN"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    try:
        from mw4agent.config.root import read_root_section

        ch = read_root_section("channels", default={})
        if isinstance(ch, dict):
            fs = ch.get("feishu")
            if isinstance(fs, dict):
                for k in ("mcp_user_access_token", "user_access_token", "mcp_uat"):
                    tok = str(fs.get(k) or "").strip()
                    if tok:
                        return tok
    except Exception as e:
        logger.debug("resolve_mcp_uat: config read skipped: %s", e)

    try:
        from mw4agent.feishu.user_oauth import get_valid_user_access_token

        app_id, app_secret, brand = _resolve_feishu_app_for_oauth_store()
        if app_id and app_secret:
            open_id = _sender_open_id_for_uat(context)
            tok = get_valid_user_access_token(
                app_id, app_secret, brand=brand, user_open_id=open_id
            )
            if tok:
                return tok
    except Exception as e:
        logger.debug("resolve_mcp_uat: oauth store skipped: %s", e)
    return None


async def call_feishu_mcp(
    mcp_tool_name: str,
    arguments: Dict[str, Any],
    *,
    tool_call_id: str,
    uat: str,
) -> Any:
    endpoint = resolve_mcp_endpoint()
    body: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": tool_call_id,
        "method": "tools/call",
        "params": {"name": mcp_tool_name, "arguments": arguments},
    }
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-Lark-MCP-UAT": uat,
        "X-Lark-MCP-Allowed-Tools": mcp_tool_name,
        "User-Agent": "mw4agent-feishu-docs-plugin/0.1",
    }
    bearer = resolve_mcp_bearer()
    if bearer:
        headers["Authorization"] = bearer

    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(
            endpoint,
            headers=headers,
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        )

    text = r.text
    if not r.is_success:
        raise RuntimeError(f"MCP HTTP {r.status_code}: {text[:4000]}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"MCP non-JSON response: {text[:4000]}")

    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            msg = err.get("message", str(err))
            code = err.get("code", "")
            raise RuntimeError(f"MCP error {code}: {msg}")
        raise RuntimeError(f"MCP error: {err}")

    return unwrap_json_rpc_result(data.get("result"))


def validate_update_doc_params(p: Dict[str, Any]) -> None:
    if str(p.get("task_id") or "").strip():
        return
    if not str(p.get("doc_id") or "").strip():
        raise ValueError("update-doc：未提供 task_id 时必须提供 doc_id")
    mode = p.get("mode")
    need_selection = mode in (
        "replace_range",
        "insert_before",
        "insert_after",
        "delete_range",
    )
    if need_selection:
        e = str(p.get("selection_with_ellipsis") or "").strip()
        t = str(p.get("selection_by_title") or "").strip()
        if bool(e) == bool(t):
            raise ValueError(
                "update-doc：mode 为 replace_range/insert_before/insert_after/delete_range 时，"
                "selection_with_ellipsis 与 selection_by_title 必须二选一"
            )
    need_markdown = mode != "delete_range"
    if need_markdown and not str(p.get("markdown") or "").strip():
        raise ValueError(f"update-doc：mode={mode} 时必须提供 markdown")


def validate_create_doc_params(p: Dict[str, Any]) -> None:
    if str(p.get("task_id") or "").strip():
        return
    if not str(p.get("markdown") or "").strip() or not str(p.get("title") or "").strip():
        raise ValueError("create-doc：未提供 task_id 时，至少需要提供 markdown 和 title")
    flags = [p.get("folder_token"), p.get("wiki_node"), p.get("wiki_space")]
    flags = [x for x in flags if x and str(x).strip()]
    if len(flags) > 1:
        raise ValueError(
            "create-doc：folder_token / wiki_node / wiki_space 三者互斥，请只提供一个"
        )


def _normalize_mcp_tool_result(raw: Any) -> Any:
    if _is_record(raw) and isinstance(raw.get("content"), list):
        return raw
    return {"raw": raw}


def _missing_uat_result() -> ToolResult:
    return ToolResult(
        success=False,
        result={
            "error": "missing_uat",
            "hint": (
                "需用户访问令牌。飞书内可发 /mw4auth 或 飞书授权 完成卡片授权；"
                "或 mw4agent feishu authorize；或 FEISHU_MCP_UAT / channels.feishu.mcp_user_access_token。"
            ),
        },
        error=(
            "feishu MCP: 无可用用户访问令牌。可在飞书对话发送 **/mw4auth** 或 **飞书授权** 获取卡片授权；"
            "或执行: mw4agent feishu authorize；或设置 FEISHU_MCP_UAT / "
            "channels.feishu.mcp_user_access_token。"
        ),
    )


class FeishuFetchDocTool(AgentTool):
    def __init__(self) -> None:
        super().__init__(
            name="feishu_fetch_doc",
            description=(
                "获取飞书云文档内容（标题与 Markdown），飞书 MCP fetch-doc。"
                "需用户访问令牌 FEISHU_MCP_UAT。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "文档 ID 或 URL",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "字符偏移，分页",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "最大返回字符数",
                    },
                },
                "required": ["doc_id"],
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        uat = resolve_mcp_uat(context)
        if not uat:
            return _missing_uat_result()
        p = params or {}
        args: Dict[str, Any] = {"doc_id": p.get("doc_id", "")}
        if p.get("offset") is not None:
            args["offset"] = int(p["offset"])
        if p.get("limit") is not None:
            args["limit"] = int(p["limit"])
        tc = tool_call_id or str(uuid.uuid4())
        try:
            raw = await call_feishu_mcp("fetch-doc", args, tool_call_id=tc, uat=uat)
            return ToolResult(success=True, result=_normalize_mcp_tool_result(raw))
        except Exception as e:
            logger.warning("feishu_fetch_doc failed: %s", e)
            return ToolResult(success=False, result={"error": str(e)}, error=str(e))


class FeishuCreateDocTool(AgentTool):
    def __init__(self) -> None:
        super().__init__(
            name="feishu_create_doc",
            description=(
                "用 Markdown 创建飞书云文档（MCP create-doc）。"
                "可选 folder_token、wiki_node、wiki_space 之一；task_id 查询异步任务。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "markdown": {"type": "string"},
                    "title": {"type": "string"},
                    "folder_token": {"type": "string"},
                    "wiki_node": {"type": "string"},
                    "wiki_space": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": [],
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        uat = resolve_mcp_uat(context)
        if not uat:
            return _missing_uat_result()
        p = dict(params or {})
        try:
            validate_create_doc_params(p)
        except ValueError as e:
            return ToolResult(success=False, result={"error": str(e)}, error=str(e))
        args = {k: v for k, v in p.items() if v is not None and v != ""}
        tc = tool_call_id or str(uuid.uuid4())
        try:
            raw = await call_feishu_mcp("create-doc", args, tool_call_id=tc, uat=uat)
            return ToolResult(success=True, result=_normalize_mcp_tool_result(raw))
        except Exception as e:
            logger.warning("feishu_create_doc failed: %s", e)
            return ToolResult(success=False, result={"error": str(e)}, error=str(e))


class FeishuUpdateDocTool(AgentTool):
    def __init__(self) -> None:
        super().__init__(
            name="feishu_update_doc",
            description=(
                "更新飞书云文档：overwrite/append/replace_range/replace_all/"
                "insert_before/insert_after/delete_range（MCP update-doc）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "markdown": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": [
                            "overwrite",
                            "append",
                            "replace_range",
                            "replace_all",
                            "insert_before",
                            "insert_after",
                            "delete_range",
                        ],
                    },
                    "selection_with_ellipsis": {"type": "string"},
                    "selection_by_title": {"type": "string"},
                    "new_title": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": ["mode"],
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        uat = resolve_mcp_uat(context)
        if not uat:
            return _missing_uat_result()
        p = dict(params or {})
        try:
            validate_update_doc_params(p)
        except ValueError as e:
            return ToolResult(success=False, result={"error": str(e)}, error=str(e))
        args = {k: v for k, v in p.items() if v is not None and v != ""}
        tc = tool_call_id or str(uuid.uuid4())
        try:
            raw = await call_feishu_mcp("update-doc", args, tool_call_id=tc, uat=uat)
            return ToolResult(success=True, result=_normalize_mcp_tool_result(raw))
        except Exception as e:
            logger.warning("feishu_update_doc failed: %s", e)
            return ToolResult(success=False, result={"error": str(e)}, error=str(e))


def register_tools(registry: Any = None) -> None:
    if registry is None:
        from mw4agent.agents.tools import get_tool_registry

        registry = get_tool_registry()
    for t in (FeishuFetchDocTool(), FeishuCreateDocTool(), FeishuUpdateDocTool()):
        if registry.get_tool(t.name) is None:
            registry.register(t)
