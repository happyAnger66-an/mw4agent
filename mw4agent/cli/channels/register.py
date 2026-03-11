"""Register `channels` CLI commands (OpenClaw-inspired subcli style)."""

from __future__ import annotations

import asyncio
import click

from ...agents.runner.runner import AgentRunner
from ...agents.session.manager import SessionManager
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
    @click.option("--session-file", default="mw4agent.sessions.json", show_default=True)
    def run_console(session_file: str) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("console"):
                registry.register_plugin(ConsoleChannel())

            session_manager = SessionManager(session_file)
            runner = AgentRunner(session_manager)
            dispatcher = ChannelDispatcher(ChannelRuntime(session_manager=session_manager, agent_runner=runner))
            await dispatcher.run_channel("console")

        asyncio.run(_run())

    @channels_group.group(name="telegram", help="Telegram bot channel")
    def telegram_group() -> None:
        pass

    @telegram_group.command(name="run", help="Run the Telegram bot channel (long polling)")
    @click.option("--session-file", default="mw4agent.sessions.json", show_default=True)
    @click.option("--bot-token", envvar="TELEGRAM_BOT_TOKEN", help="Telegram bot token (env TELEGRAM_BOT_TOKEN)")
    def run_telegram(session_file: str, bot_token: str | None) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("telegram"):
                registry.register_plugin(TelegramChannel(bot_token=bot_token))

            session_manager = SessionManager(session_file)
            runner = AgentRunner(session_manager)
            dispatcher = ChannelDispatcher(ChannelRuntime(session_manager=session_manager, agent_runner=runner))
            await dispatcher.run_channel("telegram")

        asyncio.run(_run())

    @channels_group.group(name="webhook", help="Generic HTTP webhook channel")
    def webhook_group() -> None:
        pass

    @webhook_group.command(name="run", help="Run the HTTP webhook channel server")
    @click.option("--session-file", default="mw4agent.sessions.json", show_default=True)
    @click.option("--host", default="0.0.0.0", show_default=True, help="Webhook server host")
    @click.option("--port", default=8080, show_default=True, type=int, help="Webhook server port")
    @click.option("--path", default="/webhook", show_default=True, help="Webhook path")
    def run_webhook(session_file: str, host: str, port: int, path: str) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("webhook"):
                registry.register_plugin(WebhookChannel(host=host, port=port, path=path))

            session_manager = SessionManager(session_file)
            runner = AgentRunner(session_manager)
            dispatcher = ChannelDispatcher(ChannelRuntime(session_manager=session_manager, agent_runner=runner))
            await dispatcher.run_channel("webhook")

        asyncio.run(_run())

    @channels_group.group(name="feishu", help="Feishu/Lark channel (webhook-based)")
    def feishu_group() -> None:
        pass

    @feishu_group.command(name="run-webhook", help="Run the Feishu webhook channel server")
    @click.option("--session-file", default="mw4agent.sessions.json", show_default=True)
    @click.option("--host", default="0.0.0.0", show_default=True, help="Feishu webhook server host")
    @click.option("--port", default=8081, show_default=True, type=int, help="Feishu webhook server port")
    @click.option("--path", default="/feishu/webhook", show_default=True, help="Feishu webhook path")
    def run_feishu_webhook(session_file: str, host: str, port: int, path: str) -> None:
        async def _run() -> None:
            registry = get_channel_registry()
            if not registry.get_plugin("feishu"):
                registry.register_plugin(FeishuChannel(host=host, port=port, path=path))

            session_manager = SessionManager(session_file)
            runner = AgentRunner(session_manager)
            dispatcher = ChannelDispatcher(ChannelRuntime(session_manager=session_manager, agent_runner=runner))
            await dispatcher.run_channel("feishu")

        asyncio.run(_run())

