"""Feishu user OAuth store (local file)."""

import json
import os
from pathlib import Path

from mw4agent.feishu import user_oauth as uo


def test_oauth_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    uo.save_user_token_for_app(
        "cli_testapp",
        access_token="at_xxx",
        refresh_token="rt_yyy",
        expires_in=3600,
        scope="offline_access docx:document:readonly",
    )
    p = uo.oauth_store_path()
    assert p.is_file()
    assert (p.stat().st_mode & 0o777) == 0o600
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["by_app_id"]["cli_testapp"]["users"]["__default__"]["access_token"] == "at_xxx"
    rec = uo.get_stored_token_record("cli_testapp")
    assert rec and rec["refresh_token"] == "rt_yyy"
    uo.save_user_token_for_app(
        "cli_testapp",
        access_token="at_ou",
        refresh_token="rt_ou",
        expires_in=60,
        user_open_id="ou_fake",
    )
    raw2 = json.loads(p.read_text(encoding="utf-8"))
    assert raw2["by_app_id"]["cli_testapp"]["users"]["ou_fake"]["access_token"] == "at_ou"
    uo.clear_user_token_for_app("cli_testapp")
    assert uo.get_stored_token_record("cli_testapp") is None
