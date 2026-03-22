"""Register `channels` CLI commands (OpenClaw-inspired subcli style)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import click

from ...agents.runner.runner import AgentRunner
from ...agents.session import MultiAgentSessionManager, SessionManager
from ...agents.agent_manager import AgentManager
from ...channels.dispatcher import ChannelDispatcher, ChannelRuntime
from ...channels.plugins.console import ConsoleChannel
from ...channels.plugins.telegram import TelegramChannel
from ...channels.plugins.webhook import WebhookChannel
from ...channels.feishu_accounts import list_feishu_accounts
from ...channels.plugins.feishu import FeishuChannel
from ...channels.registry import get_channel_registry
from ...config.root import get_root_config_path, read_root_config, write_root_config


def _merge_feishu_channel(
    cfg: Dict[str, Any],
    *,
    app_id: str,
    app_secret: str,
    connection_mode: str,
    account: str = "default",
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge channels.feishu into root config.

    - account=default 且尚无 accounts：写入顶层 app_id/app_secret（兼容旧版）。
    - 其它 account 或已有 accounts：写入 feishu.accounts.<account>；必要时把旧顶层凭证迁入 accounts.default。
    """
    next_cfg = dict(cfg)
    channels = dict(next_cfg.get("channels") or {})
    feishu = dict(channels.get("feishu") or {})
    acct = (account or "default").strip() or "default"
    aid = (agent_id or "").strip() or None

    if acct != "default" and "accounts" not in feishu and (
        (feishu.get("app_id") or "").strip() or (feishu.get("app_secret") or "").strip()
    ):
        accounts0: Dict[str, Any] = {}
        accounts0["default"] = {
            "app_id": str(feishu.get("app_id") or "").strip(),
            "app_secret": str(feishu.get("app_secret") or "").strip(),
            "connection_mode": str(feishu.get("connection_mode") or "webhook").strip().lower()
            or "webhook",
        }
        if (feishu.get("agent_id") or "").strip():
            accounts0["default"]["agent_id"] = str(feishu.get("agent_id")).strip()
        feishu["accounts"] = accounts0
        for k in ("app_id", "app_secret", "connection_mode", "agent_id"):
            feishu.pop(k, None)

    if acct == "default" and not feishu.get("accounts"):
        feishu["app_id"] = app_id
        feishu["app_secret"] = app_secret
        feishu["connection_mode"] = connection_mode
        if aid:
            feishu["agent_id"] = aid
    else:
        accounts = dict(feishu.get("accounts") or {})
        entry = dict(accounts.get(acct) or {})
        entry["app_id"] = app_id
        entry["app_secret"] = app_secret
        entry["connection_mode"] = connection_mode
        if aid:
            entry["agent_id"] = aid
        accounts[acct] = entry
        feishu["accounts"] = accounts

    channels["feishu"] = feishu
    next_cfg["channels"] = channels
    return next_cfg


def register_channels_cli(program: click.Group, _ctx) -> None:
    @program.group(name="channels", help="Channel adapters and monitors")
    def channels_group() -> None:
        pass

    @channels_group.group(name="console", help="Console channel (stdin/stdout)")
    def console_group() -> None:
        pass

    @console_group.command(name="run", help="Run the console channel monitor")
    @click.option("--agent-id", default="main", show_default=True, help="Agent id (multi-agent mode)")
    @click.option("--session-file", default="", show_default=False, help="Legacy: single session store path")
    @click.option(
        "--gateway-url",
        default="",
        envvar="MW4AGENT_GATEWAY_URL",
        help="If set, channel calls agent via Gateway RPC instead of direct; e.g. http://127.0.0.1:18790",
    )
    def run_console(agent_id: str, session_file: str, gateway_url: str) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("console"):
                registry.register_plugin(ConsoleChannel())

            if session_file and session_file.strip():
                session_manager = SessionManager(session_file.strip())
            else:
                session_manager = MultiAgentSessionManager(agent_manager=AgentManager())
            runner = AgentRunner(session_manager)
            gateway_base_url = gateway_url.strip() or None
            runtime = ChannelRuntime(
                session_manager=session_manager,
                agent_runner=runner,
                gateway_base_url=gateway_base_url,
            )
            dispatcher = ChannelDispatcher(runtime)
            await dispatcher.run_channel("console")

        asyncio.run(_run())

    @channels_group.group(name="telegram", help="Telegram bot channel")
    def telegram_group() -> None:
        pass

    @telegram_group.command(name="run", help="Run the Telegram bot channel (long polling)")
    @click.option("--agent-id", default="main", show_default=True, help="Agent id (multi-agent mode)")
    @click.option("--session-file", default="", show_default=False, help="Legacy: single session store path")
    @click.option("--bot-token", envvar="TELEGRAM_BOT_TOKEN", help="Telegram bot token (env TELEGRAM_BOT_TOKEN)")
    @click.option(
        "--gateway-url",
        default="",
        envvar="MW4AGENT_GATEWAY_URL",
        help="If set, channel calls agent via Gateway RPC; e.g. http://127.0.0.1:18790",
    )
    def run_telegram(agent_id: str, session_file: str, bot_token: str | None, gateway_url: str) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("telegram"):
                registry.register_plugin(TelegramChannel(bot_token=bot_token))

            if session_file and session_file.strip():
                session_manager = SessionManager(session_file.strip())
            else:
                session_manager = MultiAgentSessionManager(agent_manager=AgentManager())
            runner = AgentRunner(session_manager)
            gateway_base_url = gateway_url.strip() or None
            runtime = ChannelRuntime(
                session_manager=session_manager,
                agent_runner=runner,
                gateway_base_url=gateway_base_url,
            )
            dispatcher = ChannelDispatcher(runtime)
            await dispatcher.run_channel("telegram")

        asyncio.run(_run())

    @channels_group.group(name="webhook", help="Generic HTTP webhook channel")
    def webhook_group() -> None:
        pass

    @webhook_group.command(name="run", help="Run the HTTP webhook channel server")
    @click.option("--agent-id", default="main", show_default=True, help="Agent id (multi-agent mode)")
    @click.option("--session-file", default="", show_default=False, help="Legacy: single session store path")
    @click.option("--host", default="0.0.0.0", show_default=True, help="Webhook server host")
    @click.option("--port", default=8080, show_default=True, type=int, help="Webhook server port")
    @click.option("--path", default="/webhook", show_default=True, help="Webhook path")
    @click.option(
        "--gateway-url",
        default="",
        envvar="MW4AGENT_GATEWAY_URL",
        help="If set, channel calls agent via Gateway RPC; e.g. http://127.0.0.1:18790",
    )
    def run_webhook(agent_id: str, session_file: str, host: str, port: int, path: str, gateway_url: str) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("webhook"):
                registry.register_plugin(WebhookChannel(host=host, port=port, path=path))

            if session_file and session_file.strip():
                session_manager = SessionManager(session_file.strip())
            else:
                session_manager = MultiAgentSessionManager(agent_manager=AgentManager())
            runner = AgentRunner(session_manager)
            gateway_base_url = gateway_url.strip() or None
            runtime = ChannelRuntime(
                session_manager=session_manager,
                agent_runner=runner,
                gateway_base_url=gateway_base_url,
            )
            dispatcher = ChannelDispatcher(runtime)
            await dispatcher.run_channel("webhook")

        asyncio.run(_run())

    @channels_group.group(name="feishu", help="Feishu/Lark channel (webhook-based)")
    def feishu_group() -> None:
        pass

    @feishu_group.command(
        name="add",
        help="Add or update Feishu app credentials in ~/.mw4agent/mw4agent.json (channels.feishu)",
    )
    @click.option(
        "--app-id",
        envvar="FEISHU_APP_ID",
        default=None,
        help="Feishu / Lark App ID (or env FEISHU_APP_ID)",
    )
    @click.option(
        "--app-secret",
        envvar="FEISHU_APP_SECRET",
        default=None,
        help="Feishu App Secret (or env FEISHU_APP_SECRET)",
    )
    @click.option(
        "--connection-mode",
        type=click.Choice(["webhook", "websocket"], case_sensitive=False),
        default=None,
        help="webhook or websocket; default keeps existing value or webhook",
    )
    @click.option(
        "--account",
        default="default",
        show_default=True,
        help="Logical Feishu app name; non-default uses channels.feishu.accounts.<name>",
    )
    @click.option(
        "--agent-id",
        default=None,
        help="Agent id bound to this Feishu app (Gateway / dispatcher routing)",
    )
    @click.option("--json", "json_output", is_flag=True, help="Print JSON summary (secret redacted)")
    def feishu_add(
        app_id: Optional[str],
        app_secret: Optional[str],
        connection_mode: Optional[str],
        account: str,
        agent_id: Optional[str],
        json_output: bool,
    ) -> None:
        """Persist Feishu app_id and app_secret for Gateway / channels feishu run."""
        aid = (app_id or "").strip() or None
        sec = (app_secret or "").strip() or None
        if not aid:
            aid = click.prompt("Feishu App ID").strip()
        if not sec:
            sec = click.prompt("Feishu App Secret", hide_input=True).strip()
        if not aid or not sec:
            raise click.UsageError("app_id and app_secret are required")

        current = read_root_config()
        existing = (current.get("channels") or {}).get("feishu") or {}
        mode = connection_mode.strip().lower() if connection_mode else None
        if mode is None:
            prev = str(existing.get("connection_mode") or "webhook").strip().lower()
            mode = prev if prev in ("webhook", "websocket") else "webhook"

        updated = _merge_feishu_channel(
            current,
            app_id=aid,
            app_secret=sec,
            connection_mode=mode,
            account=account.strip() or "default",
            agent_id=agent_id,
        )
        write_root_config(updated)

        path = get_root_config_path()
        acct = account.strip() or "default"
        if json_output:
            summary: Dict[str, Any] = {
                "ok": True,
                "configPath": str(path),
                "account": acct,
                "feishu": {
                    "app_id": aid,
                    "app_secret": "********",
                    "connection_mode": mode,
                },
            }
            if agent_id:
                summary["feishu"]["agent_id"] = str(agent_id).strip()
            click.echo(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            click.echo(f"Feishu channel saved to {path}")
            click.echo(f"  account: {acct}")
            click.echo(f"  app_id: {aid}")
            click.echo("  app_secret: ********")
            click.echo(f"  connection_mode: {mode}")
            if agent_id:
                click.echo(f"  agent_id: {str(agent_id).strip()}")

    @feishu_group.command(name="run", help="Run the Feishu channel server (webhook or websocket)")
    @click.option("--agent-id", default="main", show_default=True, help="Agent id (multi-agent mode)")
    @click.option("--session-file", default="", show_default=False, help="Legacy: single session store path")
    @click.option("--host", default="0.0.0.0", show_default=True, help="Feishu webhook server host")
    @click.option("--port", default=8081, show_default=True, type=int, help="Feishu webhook server port")
    @click.option("--path", default="/feishu/webhook", show_default=True, help="Feishu webhook path")
    @click.option(
        "--mode",
        type=click.Choice(["webhook", "websocket"], case_sensitive=False),
        default="webhook",
        show_default=True,
        help="Feishu connection mode (webhook or websocket)",
    )
    @click.option(
        "--gateway-url",
        default="",
        envvar="MW4AGENT_GATEWAY_URL",
        help="If set, channel calls agent via Gateway RPC; e.g. http://127.0.0.1:18790",
    )
    def run_feishu(
        agent_id: str,
        session_file: str,
        host: str,
        port: int,
        path: str,
        mode: str,
        gateway_url: str,
    ) -> None:
        async def _run() -> None:
            import os

            registry = get_channel_registry()
            current = read_root_config()
            feishu_section = (current.get("channels") or {}).get("feishu") or {}
            accs = list_feishu_accounts(
                feishu_section,
                env_app_id=os.getenv("FEISHU_APP_ID", "").strip(),
                env_app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            )

            if session_file and session_file.strip():
                session_manager = SessionManager(session_file.strip())
            else:
                session_manager = MultiAgentSessionManager(agent_manager=AgentManager())
            runner = AgentRunner(session_manager)
            gateway_base_url = gateway_url.strip() or None
            runtime = ChannelRuntime(
                session_manager=session_manager,
                agent_runner=runner,
                gateway_base_url=gateway_base_url,
            )

            if not accs:
                if not registry.get_plugin("feishu"):
                    registry.register_plugin(
                        FeishuChannel(
                            host=host,
                            port=port,
                            path=path,
                            connection_mode=mode.lower(),  # type: ignore[arg-type]
                        )
                    )
                dispatcher = ChannelDispatcher(runtime, registry=registry)
                await dispatcher.run_channel("feishu")
                return

            for acc in accs:
                pid = acc.plugin_channel_id
                if registry.get_plugin(pid):
                    continue
                registry.register_plugin(
                    FeishuChannel(
                        host=host,
                        port=port,
                        feishu_account=acc,
                    )
                )
            dispatcher = ChannelDispatcher(runtime, registry=registry)
            plugins = [registry.get_plugin(a.plugin_channel_id) for a in accs]
            plugins = [p for p in plugins if p is not None]
            if not plugins:
                raise RuntimeError("No Feishu plugins registered from config")

            modes = {p.connection_mode for p in plugins}
            if len(modes) > 1:
                raise click.UsageError(
                    "配置文件中含多种 Feishu connection_mode（webhook 与 websocket 混用）；请分进程启动或使用 Gateway。"
                )

            if plugins[0].connection_mode == "webhook":
                from fastapi import FastAPI
                import uvicorn

                app = FastAPI(title="MW4Agent Feishu (multi)")
                for p in plugins:
                    app.include_router(p.get_webhook_router(on_inbound=dispatcher.dispatch_inbound))
                config = uvicorn.Config(app, host=host, port=port, log_level="info")
                server = uvicorn.Server(config)
                await server.serve()
            else:
                await asyncio.gather(
                    *[p.run_monitor(on_inbound=dispatcher.dispatch_inbound) for p in plugins]
                )

        asyncio.run(_run())

