"""channels.feishu multi-account resolution."""

from __future__ import annotations

from mw4agent.channels.feishu_accounts import list_feishu_accounts


def test_list_feishu_accounts_legacy_flat() -> None:
    rows = list_feishu_accounts(
        {"app_id": "a1", "app_secret": "s1", "connection_mode": "webhook", "agent_id": "main"},
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.account_key == "default"
    assert r.plugin_channel_id == "feishu"
    assert r.app_id == "a1"
    assert r.agent_id == "main"
    assert r.webhook_path == "/feishu/webhook"


def test_list_feishu_accounts_multi_named() -> None:
    rows = list_feishu_accounts(
        {
            "connection_mode": "webhook",
            "accounts": {
                "sales": {"app_id": "id1", "app_secret": "sec1", "agent_id": "agent_sales"},
                "support": {"app_id": "id2", "app_secret": "sec2", "agent_id": "agent_sup"},
            },
        }
    )
    assert len(rows) == 2
    by_k = {x.account_key: x for x in rows}
    assert by_k["sales"].plugin_channel_id == "feishu:sales"
    assert by_k["sales"].webhook_path == "/feishu/webhook/sales"
    assert by_k["sales"].agent_id == "agent_sales"
    assert by_k["support"].plugin_channel_id == "feishu:support"
    assert by_k["support"].agent_id == "agent_sup"


def test_list_feishu_accounts_custom_webhook_path() -> None:
    rows = list_feishu_accounts(
        {
            "accounts": {
                "x": {
                    "app_id": "i",
                    "app_secret": "s",
                    "webhook_path": "/custom/feishu/x",
                }
            }
        }
    )
    assert rows[0].webhook_path == "/custom/feishu/x"


def test_list_feishu_accounts_env_fallback() -> None:
    rows = list_feishu_accounts({}, env_app_id="eid", env_app_secret="esec")
    assert len(rows) == 1
    assert rows[0].account_key == "default"
    assert rows[0].app_id == "eid"
    assert rows[0].plugin_channel_id == "feishu"


def test_accounts_map_single_default_key_uses_bare_feishu() -> None:
    rows = list_feishu_accounts(
        {"accounts": {"default": {"app_id": "a", "app_secret": "b"}}},
    )
    assert len(rows) == 1
    assert rows[0].plugin_channel_id == "feishu"


def test_single_account_named_still_prefixed() -> None:
    """仅一个账号但名称非 default 时，使用 feishu:<name> 以免与 bare feishu 语义混淆。"""
    rows = list_feishu_accounts(
        {"accounts": {"only": {"app_id": "a", "app_secret": "b"}}},
    )
    assert len(rows) == 1
    assert rows[0].plugin_channel_id == "feishu:only"
