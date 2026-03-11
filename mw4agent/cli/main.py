"""Main CLI entry point - similar to run-main.ts in OpenClaw"""

import sys
import click
from typing import Optional, List
from .context import ProgramContext, create_program_context
from .registry import get_registry, CommandEntry
from .gateway import register_gateway_cli
from .channels.register import register_channels_cli
from .. import __version__


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

    # Create program context
    ctx = create_program_context(__version__)
    
    # Build program
    program = build_program(ctx)
    
    # Get primary command for lazy loading
    primary = get_primary_command(argv)
    
    # Register commands (lazy load if primary command specified)
    register_commands(program, ctx, primary_command=primary)
    
    # Execute
    try:
        program()
    except click.Abort:
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
