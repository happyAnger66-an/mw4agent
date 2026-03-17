"""Register `channels` CLI commands (OpenClaw-inspired subcli style)."""

from __future__ import annotations

import asyncio
import click

from ...agents.runner.runner import AgentRunner
from ...agents.session import MultiAgentSessionManager, SessionManager
from ...agents.agent_manager import AgentManager
from ...channels.dispatcher import ChannelDispatcher, ChannelRuntime
from ...channels.plugins.console import ConsoleChannel
from ...channels.plugins.telegram import TelegramChannel
from ...channels.plugins.webhook import WebhookChannel
from ...channels.plugins.feishu import FeishuChannel
from ...channels.registry import get_channel_registry


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
            registry = get_channel_registry()
            if not registry.get_plugin("feishu"):
                registry.register_plugin(
                    FeishuChannel(
                        host=host,
                        port=port,
                        path=path,
                        connection_mode=mode.lower(),  # type: ignore[arg-type]
                    )
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
            dispatcher = ChannelDispatcher(runtime)
            await dispatcher.run_channel("feishu")

        asyncio.run(_run())

