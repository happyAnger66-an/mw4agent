"""Feishu / Lark user OAuth (device flow) and local UAT persistence for MCP doc tools.

对齐 feishu-openclaw-plugin 的 device-flow + 落盘思路：RFC 8628 Device Authorization，
令牌写入 ~/.mw4agent/feishu_oauth.json（0600），含 refresh_token 时可自动刷新 access_token。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from mw4agent.config.root import get_root_config_dir
from mw4agent.log import get_logger

logger = get_logger(__name__)

STORE_VERSION = 2
USER_DEFAULT_KEY = "__default__"
# 与 feishu-openclaw-plugin MCP 文档工具所需 scope 并集（略宽，减少二次授权）
DEFAULT_DOC_OAUTH_SCOPES = (
    "offline_access "
    "docx:document:readonly docx:document:create docx:document:write_only "
    "wiki:node:read wiki:node:create "
    "board:whiteboard:node:create docs:document.media:upload"
).replace("  ", " ").strip()

REFRESH_MARGIN_SEC = 300


def oauth_store_path() -> Path:
    return get_root_config_dir() / "feishu_oauth.json"


def resolve_oauth_http_endpoints(brand: str) -> Tuple[str, str]:
    b = (brand or "feishu").strip().lower()
    if b == "lark":
        return (
            "https://accounts.larksuite.com/oauth/v1/device_authorization",
            "https://open.larksuite.com/open-apis/authen/v2/oauth/token",
        )
    return (
        "https://accounts.feishu.cn/oauth/v1/device_authorization",
        "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
    )


def _migrate_app_entry(entry: Any) -> Dict[str, Any]:
    """Normalize to { \"users\": { open_id or __default__: token_rec } }."""
    if not isinstance(entry, dict):
        return {"users": {}}
    if isinstance(entry.get("users"), dict):
        return {"users": dict(entry["users"])}
    if entry.get("access_token") or entry.get("refresh_token"):
        return {"users": {USER_DEFAULT_KEY: dict(entry)}}
    return {"users": {}}


def _load_store_raw() -> Dict[str, Any]:
    p = oauth_store_path()
    if not p.is_file():
        return {"version": STORE_VERSION, "by_app_id": {}}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("feishu_oauth: failed to read store: %s", e)
        return {"version": STORE_VERSION, "by_app_id": {}}
    if not isinstance(data, dict):
        return {"version": STORE_VERSION, "by_app_id": {}}
    by_app = data.get("by_app_id")
    if not isinstance(by_app, dict):
        data["by_app_id"] = {}
        by_app = data["by_app_id"]
    for aid, ent in list(by_app.items()):
        by_app[aid] = _migrate_app_entry(ent)
    data["version"] = STORE_VERSION
    return data


def _save_store_raw(data: Dict[str, Any]) -> None:
    p = oauth_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(p)
        os.chmod(p, 0o600)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _user_storage_key(user_open_id: Optional[str]) -> str:
    u = (user_open_id or "").strip()
    return u if u else USER_DEFAULT_KEY


def save_user_token_for_app(
    app_id: str,
    *,
    access_token: str,
    refresh_token: str = "",
    expires_in: int = 7200,
    scope: str = "",
    user_open_id: Optional[str] = None,
) -> None:
    """Persist UAT under app_id + user (飞书 open_id)；CLI 无用户时用 __default__。"""
    aid = (app_id or "").strip()
    if not aid:
        raise ValueError("app_id required")
    ukey = _user_storage_key(user_open_id)
    now = time.time()
    data = _load_store_raw()
    by_app: Dict[str, Any] = data["by_app_id"]  # type: ignore[assignment]
    shell = _migrate_app_entry(by_app.get(aid))
    users: Dict[str, Any] = shell["users"]
    users[ukey] = {
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "expires_at": now + float(expires_in),
        "scope": scope or "",
        "updated_at": now,
    }
    shell["users"] = users
    by_app[aid] = shell
    data["by_app_id"] = by_app
    data["version"] = STORE_VERSION
    _save_store_raw(data)
    logger.info(
        "feishu_oauth: saved UAT app_id=%s user=%s (expires_in=%ss)",
        aid,
        ukey[:8] + "…" if len(ukey) > 8 else ukey,
        expires_in,
    )


def clear_user_token_for_app(app_id: str, user_open_id: Optional[str] = None) -> None:
    aid = (app_id or "").strip()
    if not aid:
        return
    data = _load_store_raw()
    by_app: Dict[str, Any] = data.get("by_app_id") or {}
    if aid not in by_app:
        return
    shell = _migrate_app_entry(by_app.get(aid))
    users: Dict[str, Any] = shell.get("users") or {}
    if user_open_id is None:
        by_app.pop(aid, None)
    else:
        ukey = _user_storage_key(user_open_id)
        users.pop(ukey, None)
        if users:
            shell["users"] = users
            by_app[aid] = shell
        else:
            by_app.pop(aid, None)
    data["by_app_id"] = by_app
    _save_store_raw(data)


def request_device_authorization(
    *,
    app_id: str,
    app_secret: str,
    brand: str = "feishu",
    scope: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    device_url, _ = resolve_oauth_http_endpoints(brand)
    sc = (scope or DEFAULT_DOC_OAUTH_SCOPES).strip()
    if "offline_access" not in sc.split():
        sc = f"{sc} offline_access".strip()
    import base64
    from urllib.parse import urlencode

    basic = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode("ascii")
    payload = urlencode({"client_id": app_id, "scope": sc})
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic}",
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(device_url, headers=headers, content=payload.encode("utf-8"))
    text = r.text
    try:
        j = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"device_authorization: non-JSON {r.status_code}: {text[:500]}")
    if not r.is_success or j.get("error"):
        msg = j.get("error_description") or j.get("error") or text[:200]
        raise RuntimeError(f"device_authorization failed: {msg}")
    return {
        "device_code": j.get("device_code", ""),
        "user_code": j.get("user_code", ""),
        "verification_uri": j.get("verification_uri", ""),
        "verification_uri_complete": j.get("verification_uri_complete") or j.get("verification_uri", ""),
        "expires_in": int(j.get("expires_in") or 240),
        "interval": int(j.get("interval") or 5),
    }


def poll_device_token(
    *,
    app_id: str,
    app_secret: str,
    device_code: str,
    expires_in: int,
    interval: int,
    brand: str = "feishu",
    timeout: float = 30.0,
) -> Dict[str, Any]:
    _, token_url = resolve_oauth_http_endpoints(brand)
    deadline = time.time() + float(expires_in)
    wait = max(1, int(interval))
    from urllib.parse import urlencode

    while time.time() < deadline:
        time.sleep(wait)
        payload = urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": app_id,
                "client_secret": app_secret,
            }
        )
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(
                    token_url,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    content=payload.encode("utf-8"),
                )
            j = r.json()
        except Exception as e:
            logger.debug("feishu_oauth poll error: %s", e)
            continue
        err = j.get("error")
        if not err and j.get("access_token"):
            return {
                "ok": True,
                "access_token": str(j.get("access_token", "")),
                "refresh_token": str(j.get("refresh_token") or ""),
                "expires_in": int(j.get("expires_in") or 7200),
                "scope": str(j.get("scope") or ""),
            }
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            wait += 5
            continue
        if err in ("access_denied", "expired_token", "invalid_grant"):
            return {"ok": False, "error": err, "message": j.get("error_description") or err}
        return {"ok": False, "error": err or "unknown", "message": j.get("error_description") or str(j)}
    return {"ok": False, "error": "expired_token", "message": "授权超时，请重新执行 authorize"}


def refresh_access_token(
    *,
    app_id: str,
    app_secret: str,
    refresh_token: str,
    brand: str = "feishu",
    timeout: float = 30.0,
) -> Optional[Dict[str, Any]]:
    if not refresh_token.strip():
        return None
    _, token_url = resolve_oauth_http_endpoints(brand)
    from urllib.parse import urlencode

    payload = urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token.strip(),
            "client_id": app_id,
            "client_secret": app_secret,
        }
    )
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=payload.encode("utf-8"),
        )
    try:
        j = r.json()
    except Exception:
        return None
    if not j.get("access_token"):
        logger.warning("feishu_oauth refresh failed: %s", j)
        return None
    return {
        "access_token": str(j.get("access_token", "")),
        "refresh_token": str(j.get("refresh_token") or refresh_token),
        "expires_in": int(j.get("expires_in") or 7200),
        "scope": str(j.get("scope") or ""),
    }


def _resolve_token_record_for_user(
    app_id: str, user_open_id: Optional[str]
) -> tuple[Optional[Dict[str, Any]], str]:
    """Return (record, storage_user_key) for refresh/save; may fall back to __default__."""
    aid = (app_id or "").strip()
    if not aid:
        return None, USER_DEFAULT_KEY
    ukey = _user_storage_key(user_open_id)
    data = _load_store_raw()
    shell = _migrate_app_entry((data.get("by_app_id") or {}).get(aid))
    users: Dict[str, Any] = shell.get("users") or {}
    rec = users.get(ukey)
    if isinstance(rec, dict) and (rec.get("access_token") or rec.get("refresh_token")):
        return rec, ukey
    if ukey != USER_DEFAULT_KEY:
        rec2 = users.get(USER_DEFAULT_KEY)
        if isinstance(rec2, dict) and (rec2.get("access_token") or rec2.get("refresh_token")):
            return rec2, USER_DEFAULT_KEY
    return None, ukey


def get_stored_token_record(app_id: str) -> Optional[Dict[str, Any]]:
    """CLI 状态：仅查看 __default__ 用户槽位。"""
    rec, _ = _resolve_token_record_for_user(app_id, None)
    return rec


def get_user_token_record_public(app_id: str, user_open_id: Optional[str]) -> Optional[Dict[str, Any]]:
    rec, _ = _resolve_token_record_for_user(app_id, user_open_id)
    return rec


def get_valid_user_access_token(
    app_id: str,
    app_secret: str,
    *,
    brand: str = "feishu",
    user_open_id: Optional[str] = None,
) -> Optional[str]:
    """Return a non-expired access_token; refresh 后写回同一存储槽位。"""
    aid = (app_id or "").strip()
    sec = (app_secret or "").strip()
    if not aid or not sec:
        return None
    rec, save_as_user = _resolve_token_record_for_user(aid, user_open_id)
    if not rec:
        return None
    at = str(rec.get("access_token") or "").strip()
    rt = str(rec.get("refresh_token") or "").strip()
    exp = rec.get("expires_at")
    try:
        exp_f = float(exp) if exp is not None else 0.0
    except (TypeError, ValueError):
        exp_f = 0.0
    now = time.time()
    if at and now < exp_f - REFRESH_MARGIN_SEC:
        return at
    if rt:
        refreshed = refresh_access_token(
            app_id=aid, app_secret=sec, refresh_token=rt, brand=brand
        )
        if refreshed:
            save_user_token_for_app(
                aid,
                access_token=refreshed["access_token"],
                refresh_token=refreshed["refresh_token"],
                expires_in=refreshed["expires_in"],
                scope=refreshed.get("scope") or "",
                user_open_id=None
                if save_as_user == USER_DEFAULT_KEY
                else save_as_user,
            )
            return refreshed["access_token"]
    if at:
        return at
    return None


def run_device_flow_interactive(
    *,
    app_id: str,
    app_secret: str,
    brand: str = "feishu",
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Run full device authorization + poll; on success saves store. Returns result dict."""
    dev = request_device_authorization(
        app_id=app_id, app_secret=app_secret, brand=brand, scope=scope
    )
    out = poll_device_token(
        app_id=app_id,
        app_secret=app_secret,
        device_code=dev["device_code"],
        expires_in=dev["expires_in"],
        interval=dev["interval"],
        brand=brand,
    )
    if not out.get("ok"):
        return out
    save_user_token_for_app(
        app_id,
        access_token=out["access_token"],
        refresh_token=out.get("refresh_token") or "",
        expires_in=out.get("expires_in") or 7200,
        scope=out.get("scope") or "",
        user_open_id=None,
    )
    return {"ok": True, "app_id": app_id, "scope": out.get("scope")}
