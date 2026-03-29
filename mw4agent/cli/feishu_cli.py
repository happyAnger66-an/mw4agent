"""CLI: Feishu user OAuth (device flow) for MCP doc tools UAT."""

from __future__ import annotations

import os

import click

from ..channels.feishu_accounts import list_feishu_accounts
from ..config.root import read_root_section
from ..feishu.user_oauth import (
    clear_user_token_for_app,
    get_stored_token_record,
    oauth_store_path,
    request_device_authorization,
    save_user_token_for_app,
)


def _resolve_app_credentials(account: str) -> tuple[str, str, str]:
    feishu = read_root_section("channels", {}).get("feishu")
    rows = list_feishu_accounts(
        feishu if isinstance(feishu, dict) else None,
        env_app_id=os.environ.get("FEISHU_APP_ID", "") or "",
        env_app_secret=os.environ.get("FEISHU_APP_SECRET", "") or "",
    )
    if not rows:
        raise click.ClickException(
            "未找到飞书应用凭证：请在 ~/.mw4agent/mw4agent.json 配置 channels.feishu，"
            "或设置 FEISHU_APP_ID / FEISHU_APP_SECRET"
        )
    key = (account or "default").strip() or "default"
    for r in rows:
        if r.account_key == key:
            if not r.app_id or not r.app_secret:
                raise click.ClickException(f"账号 {key} 缺少 app_id/app_secret")
            return r.app_id, r.app_secret, r.account_key
    raise click.ClickException(f"未知飞书账号 key: {key}（可用: {', '.join(x.account_key for x in rows)}）")


def register_feishu_cli(program: click.Group, ctx) -> None:
    @click.group("feishu")
    def feishu_grp():
        """飞书用户授权与 MCP 文档令牌（UAT）本地存储"""

    @feishu_grp.command("authorize")
    @click.option(
        "--account",
        default="default",
        show_default=True,
        help="channels.feishu.accounts 下的账号键；单账号 legacy 配置用 default",
    )
    @click.option(
        "--brand",
        type=click.Choice(["feishu", "lark"]),
        default="feishu",
        show_default=True,
    )
    @click.option(
        "--scope",
        default=None,
        help="OAuth scope，空格分隔；默认内置文档 MCP 相关 scope + offline_access",
    )
    def authorize_cmd(account: str, brand: str, scope: str | None) -> None:
        """设备授权：浏览器打开验证页，完成后将 UAT/refresh 写入 ~/.mw4agent/feishu_oauth.json"""
        app_id, app_secret, acct = _resolve_app_credentials(account)
        click.echo(f"使用飞书应用: {app_id} (account={acct}, brand={brand})")
        try:
            dev = request_device_authorization(
                app_id=app_id,
                app_secret=app_secret,
                brand=brand,
                scope=scope,
            )
        except Exception as e:
            raise click.ClickException(str(e))
        click.echo("")
        click.echo("请在浏览器中完成授权：")
        click.echo(f"  {dev.get('verification_uri_complete') or dev.get('verification_uri')}")
        click.echo(f"用户码: {dev.get('user_code')}")
        click.echo("")
        click.echo("等待授权中（可 Ctrl+C 取消）…")
        from ..feishu.user_oauth import poll_device_token  # noqa: PLC0415

        result = poll_device_token(
            app_id=app_id,
            app_secret=app_secret,
            device_code=dev["device_code"],
            expires_in=dev["expires_in"],
            interval=dev["interval"],
            brand=brand,
        )
        if not result.get("ok"):
            raise click.ClickException(result.get("message") or result.get("error") or "授权失败")
        save_user_token_for_app(
            app_id,
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token") or "",
            expires_in=result.get("expires_in") or 7200,
            scope=result.get("scope") or "",
        )
        click.echo(f"已保存用户访问令牌到: {oauth_store_path()}")
        click.echo("feishu-docs 插件将自动使用该令牌（与 FEISHU_MCP_UAT 相比，本地文件优先序见插件逻辑）。")

    @feishu_grp.command("oauth-status")
    @click.option("--account", default="default", show_default=True)
    def oauth_status_cmd(account: str) -> None:
        """查看当前已为某应用保存的令牌过期时间（不脱敏打印 token）"""
        app_id, _, acct = _resolve_app_credentials(account)
        rec = get_stored_token_record(app_id)
        click.echo(f"app_id={app_id} account={acct}")
        click.echo(f"store={oauth_store_path()}")
        if not rec:
            click.echo("状态: 无本地令牌，请执行: mw4agent feishu authorize")
            return
        import time

        exp = rec.get("expires_at")
        click.echo(f"refresh_token: {'有' if (rec.get('refresh_token') or '').strip() else '无'}")
        click.echo(f"expires_at: {exp}")
        if isinstance(exp, (int, float)):
            left = float(exp) - time.time()
            click.echo(f"约 {int(left)} 秒后过期（到期前插件会自动 refresh）")

    @feishu_grp.command("revoke-local")
    @click.option("--account", default="default", show_default=True)
    def revoke_local_cmd(account: str) -> None:
        """仅删除本机保存的令牌文件项（不向飞书服务端 revoke）"""
        app_id, _, _ = _resolve_app_credentials(account)
        clear_user_token_for_app(app_id)
        click.echo(f"已清除 app_id={app_id} 的本地 OAuth 记录")

    program.add_command(feishu_grp)
