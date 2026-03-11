"""Register `channels` CLI commands (OpenClaw-inspired subcli style)."""

from __future__ import annotations

import asyncio
import click

from ...agents.runner.runner import AgentRunner
from ...agents.session.manager import SessionManager
from ...channels.dispatcher import ChannelDispatcher, ChannelRuntime
from ...channels.plugins.console import ConsoleChannel
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

