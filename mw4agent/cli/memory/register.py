"""Register memory CLI: memory status | search | get."""

from __future__ import annotations

import json
import os
from typing import Optional

import click

from ..context import ProgramContext
from ... import memory as memory_mod


def _workspace_dir(ctx: click.Context) -> str:
    # Subcommands get parent context from group where --workspace was set
    obj = (ctx.obj or (ctx.parent.obj if ctx.parent else None) or {})
    return obj.get("workspace_dir") or os.getcwd()


def register_memory_cli(program: click.Group, ctx: ProgramContext) -> None:
    """Register memory command group (OpenClaw-style memory status/search/get)."""

    @program.group("memory", help="Search, inspect memory files (MEMORY.md, memory/*.md)")
    @click.option(
        "--workspace",
        "workspace_dir",
        type=click.Path(exists=True, file_okay=False, dir_okay=True),
        default=None,
        help="Workspace directory (default: current directory)",
    )
    @click.pass_context
    def memory_group(click_ctx: click.Context, workspace_dir: Optional[str]) -> None:
        click_ctx.ensure_object(dict)
        click_ctx.obj["workspace_dir"] = os.path.abspath(workspace_dir or os.getcwd())

    @memory_group.command("status", help="Show memory files and index status")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def memory_status(click_ctx: click.Context, json_output: bool) -> None:
        wd = _workspace_dir(click_ctx)
        files = memory_mod.list_memory_files(wd)
        status = {
            "workspace": wd,
            "provider": "file",
            "mode": "keyword",
            "files": files,
            "file_count": len(files),
        }
        if json_output:
            click.echo(json.dumps(status, indent=2, ensure_ascii=False))
            return
        click.echo(f"Workspace: {wd}")
        click.echo(f"Provider: file (keyword search)")
        click.echo(f"Memory files: {len(files)}")
        for f in files:
            click.echo(f"  - {f}")

    @memory_group.command("search", help="Search memory files")
    @click.argument("query", required=False)
    @click.option("--query", "query_opt", help="Search query (alternative to positional)")
    @click.option("--max-results", type=int, default=10, help="Max results")
    @click.option("--min-score", type=float, default=0.0, help="Minimum score")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def memory_search(
        click_ctx: click.Context,
        query: Optional[str],
        query_opt: Optional[str],
        max_results: int,
        min_score: float,
        json_output: bool,
    ) -> None:
        q = query_opt or query
        if not (q or "").strip():
            click.echo("Missing query. Use: memory search <query> or --query <text>", err=True)
            click_ctx.exit(1)
        wd = _workspace_dir(click_ctx)
        results = memory_mod.search(q, wd, max_results=max_results, min_score=min_score)
        if json_output:
            click.echo(
                json.dumps(
                    {
                        "results": [
                            {
                                "path": r.path,
                                "startLine": r.start_line,
                                "endLine": r.end_line,
                                "score": r.score,
                                "snippet": r.snippet,
                            }
                            for r in results
                        ]
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        if not results:
            click.echo("No matches.")
            return
        for r in results:
            click.echo(f"{r.score:.3f} {r.path}:{r.start_line}-{r.end_line}")
            click.echo(f"  {r.snippet}")
            click.echo()

    @memory_group.command("get", help="Read a memory file (or slice by from/lines)")
    @click.argument("path", required=True)
    @click.option("--from", "from_line", type=int, default=None, help="1-based start line")
    @click.option("--lines", type=int, default=None, help="Max lines to return")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def memory_get(
        click_ctx: click.Context,
        path: str,
        from_line: Optional[int],
        lines: Optional[int],
        json_output: bool,
    ) -> None:
        wd = _workspace_dir(click_ctx)
        r = memory_mod.read_file(wd, path, from_line=from_line, lines=lines)
        if json_output:
            click.echo(
                json.dumps(
                    {"path": r.path, "text": r.text, "missing": r.missing},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        if r.missing:
            click.echo(f"File not found or not readable: {path}", err=True)
            click_ctx.exit(1)
        click.echo(r.text)
