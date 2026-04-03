"""Redact secret fields in config payloads returned over RPC; merge on save."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict

# Shown to clients instead of real secrets; must match desktop "leave unchanged" semantics.
REDACTED_SECRET_PLACEHOLDER = "********"

# Normalized key names (underscores stripped, lowercased) treated as secrets at any object depth.
_SECRET_KEY_NORMS = frozenset(
    {
        "apikey",
        "appsecret",
        "mcpuseraccesstoken",
        "useraccesstoken",
        "mcpuat",
        "clientsecret",
        "refreshtoken",
        "accesstoken",
        "bearertoken",
    }
)


def _norm_field_name(name: str) -> str:
    return str(name).replace("_", "").lower()


def is_secret_field_name(name: str) -> bool:
    return _norm_field_name(name) in _SECRET_KEY_NORMS


def is_redacted_placeholder(value: str) -> bool:
    s = str(value).strip()
    if not s:
        return False
    if s == REDACTED_SECRET_PLACEHOLDER:
        return True
    return bool(re.fullmatch(r"\*+", s))


def redact_secrets(value: Any) -> Any:
    """Deep-copy and replace non-empty secret string values with REDACTED_SECRET_PLACEHOLDER."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if is_secret_field_name(str(k)):
                if isinstance(v, str) and v.strip():
                    out[k] = REDACTED_SECRET_PLACEHOLDER
                elif v not in (None, "", {}):
                    out[k] = REDACTED_SECRET_PLACEHOLDER
                else:
                    out[k] = copy.deepcopy(v)
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(value, list):
        return [redact_secrets(x) for x in value]
    return copy.deepcopy(value)


def merge_preserve_redacted_secrets(old: Any, new: Any) -> Any:
    """Merge section dicts: placeholder secret strings keep ``old`` values; structure follows ``new``."""
    if isinstance(new, dict):
        old_d = old if isinstance(old, dict) else {}
        out: Dict[str, Any] = {}
        for k, v in new.items():
            osub = old_d.get(k)
            if isinstance(v, dict):
                out[k] = merge_preserve_redacted_secrets(osub if isinstance(osub, dict) else {}, v)
                continue
            if isinstance(v, str) and is_redacted_placeholder(v):
                if isinstance(osub, str) and osub.strip():
                    out[k] = osub
                # No prior value: omit key so we do not persist a literal placeholder.
                continue
            out[k] = copy.deepcopy(v)
        return out
    if isinstance(new, list):
        old_l = old if isinstance(old, list) else []
        return [
            merge_preserve_redacted_secrets(old_l[i] if i < len(old_l) else None, item)
            for i, item in enumerate(new)
        ]
    return copy.deepcopy(new)
