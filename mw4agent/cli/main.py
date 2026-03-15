"""Main CLI entry point - similar to run-main.ts in OpenClaw"""

import sys
import click
from typing import Optional, List
from .context import ProgramContext, create_program_context
from .registry import get_registry, CommandEntry
from .gateway import register_gateway_cli
from .node_host import register_node_host_cli
from .channels.register import register_channels_cli
from .agent.register import register_agent_cli
from .configuration import register_configuration_cli
from .dashboard import register_dashboard_cli
from .memory import register_memory_cli
from .. import __version__
from ..log import setup_logging


def get_primary_command(argv: List[str]) -> Optional[str]:
    """Extract primary command from argv"""
    if len(argv) < 2:
        return None
    
    # Skip program name and root options
    i = 1
    while i < len(argv):
        arg = argv[i]
        # Skip root options
        if arg in ["--help", "-h", "--version", "-V", "--dev", "--profile"]:
            if arg in ["--profile"] and i + 1 < len(argv):
                i += 2  # Skip option value
            else:
                i += 1
            continue
        if arg.startswith("--"):
            i += 1
            continue
        # Found command
        return arg
        i += 1
    
    return None


def build_program(ctx: ProgramContext) -> click.Group:
    """Build the CLI program - similar to buildProgram in OpenClaw"""
    
    @click.group()
    @click.version_option(version=ctx.program_version)
    @click.option("--dev", is_flag=True, help="Dev profile")
    @click.option("--profile", help="Use a named profile")
    @click.pass_context
    def cli(click_ctx: click.Context, dev: bool, profile: Optional[str]):
        """MW4Agent CLI"""
        click_ctx.ensure_object(dict)
        click_ctx.obj["dev"] = dev
        click_ctx.obj["profile"] = profile
    
    return cli


def register_core_commands(program: click.Group, ctx: ProgramContext) -> None:
    """Register core commands"""
    # Register gateway command
    gateway_entry = CommandEntry(
        commands=[
            {
                "name": "gateway",
                "description": "Run, inspect, and query the WebSocket Gateway",
                "has_subcommands": True,
            }
        ],
        register=register_gateway_cli,
    )
    get_registry().register_entry(gateway_entry)

    node_host_entry = CommandEntry(
        commands=[
            {
                "name": "node-host",
                "description": "Run as an OpenClaw-compatible node (connect to Gateway, execute node.invoke)",
                "has_subcommands": True,
            }
        ],
        register=register_node_host_cli,
    )
    get_registry().register_entry(node_host_entry)

    agent_entry = CommandEntry(
        commands=[
            {
                "name": "agent",
                "description": "Run agents via the Gateway",
                "has_subcommands": True,
            }
        ],
        register=register_agent_cli,
    )
    get_registry().register_entry(agent_entry)

    channels_entry = CommandEntry(
        commands=[
            {
                "name": "channels",
                "description": "Channel adapters and monitors",
                "has_subcommands": True,
            }
        ],
        register=register_channels_cli,
    )
    get_registry().register_entry(channels_entry)

    configuration_entry = CommandEntry(
        commands=[
            {
                "name": "configuration",
                "description": "Configure MW4Agent (LLM provider/model, channels, skills, etc.)",
                "has_subcommands": True,
            }
        ],
        register=register_configuration_cli,
    )
    get_registry().register_entry(configuration_entry)

    dashboard_entry = CommandEntry(
        commands=[
            {
                "name": "dashboard",
                "description": "Open the MW4Agent web dashboard",
                "has_subcommands": False,
            }
        ],
        register=register_dashboard_cli,
    )
    get_registry().register_entry(dashboard_entry)

    memory_entry = CommandEntry(
        commands=[
            {
                "name": "memory",
                "description": "Search, inspect memory files (MEMORY.md, memory/*.md)",
                "has_subcommands": True,
            }
        ],
        register=register_memory_cli,
    )
    get_registry().register_entry(memory_entry)


def register_commands(
    program: click.Group,
    ctx: ProgramContext,
    primary_command: Optional[str] = None,
) -> None:
    """Register commands to the program"""
    register_core_commands(program, ctx)
    get_registry().register_commands(program, ctx, primary_command)


def main(argv: Optional[List[str]] = None) -> None:
    """
    Main CLI entry point - similar to runCli in OpenClaw
    
    Args:
        argv: Command line arguments (defaults to sys.argv)
    """
    if argv is None:
        argv = sys.argv

    # Async logging (console / file / log host via env); non-blocking
    setup_logging()

    # Create program context
    ctx = create_program_context(__version__)
    
    # Build program
    program = build_program(ctx)
    
    # Get primary command for lazy loading
    primary = get_primary_command(argv)
    
    # Register commands (lazy load if primary command specified)
    register_commands(program, ctx, primary_command=primary)
    
    # Execute (pass argv so tests can inject args; otherwise click uses sys.argv)
    try:
        if argv is not None:
            program(args=argv[1:], prog_name=argv[0] if argv else None)
        else:
            program()
    except click.Abort:
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
