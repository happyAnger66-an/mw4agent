"""Resolve multiple Feishu app accounts from channels.feishu configuration.

Supports:
- Legacy single app: channels.feishu.app_id + app_secret (+ optional agent_id, webhook_path)
- Multiple apps: channels.feishu.accounts.<name> = { app_id, app_secret, agent_id?, webhook_path?, ... }

Each account merges shared keys from the parent feishu object (excluding "accounts").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping


def _strip_feishu_base(feishu: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in feishu.items() if k != "accounts"}


def _merge_account(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base)
    for k, v in override.items():
        if v is not None and v != "":
            out[k] = v
    return out


def _has_credentials(d: Mapping[str, Any]) -> bool:
    aid = str(d.get("app_id") or "").strip()
    sec = str(d.get("app_secret") or "").strip()
    return bool(aid and sec)


def _safe_path_segment(account_key: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", account_key.strip() or "acct")
    return s or "acct"


@dataclass(frozen=True)
class FeishuAccountResolved:
    """One Feishu bot instance to register with the gateway / dispatcher."""

    account_key: str
    """Internal config key (e.g. default, sales)."""

    plugin_channel_id: str
    """Registry / InboundContext.channel id: "feishu" or "feishu:<key>"."""

    app_id: str
    app_secret: str
    connection_mode: str
    webhook_path: str
    agent_id: str
    encrypt_key: str
    verification_token: str
    api_base: str


def _default_webhook_path(account_key: str, *, single_legacy_feishu: bool) -> str:
    if single_legacy_feishu:
        return "/feishu/webhook"
    return f"/feishu/webhook/{_safe_path_segment(account_key)}"


def _resolve_plugin_channel_id(account_key: str, *, n_accounts: int) -> str:
    """Single default account keeps bare 'feishu' for backward compatibility."""
    if n_accounts == 1 and account_key == "default":
        return "feishu"
    return f"feishu:{account_key}"


def _connection_mode(raw: Any) -> str:
    m = str(raw or "webhook").strip().lower()
    return m if m in ("webhook", "websocket") else "webhook"


def list_feishu_accounts(
    feishu_section: Mapping[str, Any] | None,
    *,
    env_app_id: str = "",
    env_app_secret: str = "",
) -> List[FeishuAccountResolved]:
    """Parse channels.feishu (and optional env) into a list of concrete accounts."""
    feishu = dict(feishu_section or {})
    base = _strip_feishu_base(feishu)
    accounts_map = feishu.get("accounts")

    rows: List[tuple[str, Dict[str, Any]]] = []

    if isinstance(accounts_map, dict) and accounts_map:
        for key in sorted(accounts_map.keys(), key=lambda x: str(x)):
            sub = accounts_map.get(key)
            if not isinstance(sub, dict):
                continue
            merged = _merge_account(base, sub)
            if _has_credentials(merged):
                rows.append((str(key), merged))
    elif _has_credentials(base):
        rows.append(("default", dict(base)))

    # Env-only fallback (no config rows): same as legacy gateway behavior
    if not rows:
        eid = (env_app_id or "").strip()
        esec = (env_app_secret or "").strip()
        if eid and esec:
            merged = dict(base)
            merged["app_id"] = eid
            merged["app_secret"] = esec
            rows.append(("default", merged))

    n = len(rows)
    out: List[FeishuAccountResolved] = []
    for account_key, merged in rows:
        mode = _connection_mode(merged.get("connection_mode"))
        path_raw = str(merged.get("webhook_path") or merged.get("path") or "").strip()
        if path_raw:
            wp = path_raw if path_raw.startswith("/") else f"/{path_raw}"
        else:
            wp = _default_webhook_path(account_key, single_legacy_feishu=(n == 1 and account_key == "default"))
        agent = str(merged.get("agent_id") or merged.get("agentId") or "main").strip() or "main"
        enc = str(merged.get("encrypt_key") or merged.get("encryptKey") or "").strip()
        vtok = str(merged.get("verification_token") or merged.get("verificationToken") or "").strip()
        api_base = str(merged.get("api_base") or merged.get("apiBase") or "").strip()
        if not api_base:
            import os

            api_base = (os.getenv("FEISHU_API_BASE") or "https://open.feishu.cn/open-apis").strip()

        out.append(
            FeishuAccountResolved(
                account_key=account_key,
                plugin_channel_id=_resolve_plugin_channel_id(account_key, n_accounts=n),
                app_id=str(merged.get("app_id") or "").strip(),
                app_secret=str(merged.get("app_secret") or "").strip(),
                connection_mode=mode,
                webhook_path=wp,
                agent_id=agent,
                encrypt_key=enc,
                verification_token=vtok,
                api_base=api_base,
            )
        )
    return out
