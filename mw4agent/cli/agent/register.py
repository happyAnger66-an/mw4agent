"""Register agent CLI commands (Gateway RPC + tools-assisted runs)."""

import asyncio
import json as jsonlib
import uuid
from dataclasses import asdict
from typing import Optional

import click

from ..context import ProgramContext
from ...agents.agent_manager import AgentManager
from ...config.paths import normalize_agent_id
from ...gateway.client import call_rpc
from ...agents.tools import GatewayLsTool


def register_agent_cli(program: click.Group, ctx: ProgramContext) -> None:
    """Register agent commands - similar精神 to OpenClaw `agent` CLI."""

    @program.group("agent", help="Run agents via the Gateway")
    @click.pass_context
    def agent_group(click_ctx: click.Context) -> None:
        """Agent command group."""
        pass

    @agent_group.command("create", help="Create a new agent (agent_dir + workspace_dir)")
    @click.argument("agent_id", nargs=1)
    @click.option("--agent-dir", default="", help="Override agent_dir (default: ~/.mw4agent/agents/<id>)")
    @click.option(
        "--workspace-dir",
        default="",
        help="Override workspace_dir (default: <agent_dir>/workspace)",
    )
    def agent_create(agent_id: str, agent_dir: str, workspace_dir: str) -> None:
        mgr = AgentManager()
        cfg = mgr.get_or_create(
            agent_id,
            agent_dir=agent_dir.strip() or None,
            workspace_dir=workspace_dir.strip() or None,
        )
        click.echo(jsonlib.dumps({"ok": True, "agent": asdict(cfg)}, ensure_ascii=False, indent=2))

    @agent_group.command("list", help="List agents under ~/.mw4agent/agents")
    def agent_list() -> None:
        mgr = AgentManager()
        mgr.ensure_main()
        click.echo(jsonlib.dumps({"agents": mgr.list_agents()}, ensure_ascii=False, indent=2))

    @agent_group.command("show", help="Show agent config")
    @click.argument("agent_id", nargs=1, required=False)
    def agent_show(agent_id: Optional[str] = None) -> None:
        mgr = AgentManager()
        aid = (agent_id or "").strip() or "main"
        cfg = mgr.get(aid) or mgr.get_or_create(aid)
        payload = asdict(cfg)
        if payload.get("llm") and isinstance(payload["llm"], dict):
            redacted = dict(payload["llm"])
            if redacted.get("api_key"):
                redacted["api_key"] = "********"
            payload["llm"] = redacted
        click.echo(jsonlib.dumps({"agent": payload}, ensure_ascii=False, indent=2))

    @agent_group.command(
        "set-llm",
        help="Set per-agent LLM overrides in agent.json (merged over global mw4agent.json llm)",
    )
    @click.argument("agent_id", nargs=1)
    @click.option("--provider", default="", help="LLM provider id (e.g. openai, deepseek, echo)")
    @click.option("--model-id", "model_id", default="", help="Model id / model_id")
    @click.option("--base-url", default="", help="API base URL (OpenAI-compatible)")
    @click.option("--api-key", default="", help="API key (stored in agent.json; omit to leave unchanged)")
    @click.option("--clear", is_flag=True, help="Remove per-agent llm overrides")
    def agent_set_llm(
        agent_id: str,
        provider: str,
        model_id: str,
        base_url: str,
        api_key: str,
        clear: bool,
    ) -> None:
        mgr = AgentManager()
        aid = (agent_id or "").strip() or "main"
        cfg = mgr.get_or_create(aid)
        if clear:
            cfg.llm = None
            mgr.save(cfg)
            click.echo(jsonlib.dumps({"ok": True, "agentId": aid, "llm": None}, ensure_ascii=False, indent=2))
            return
        llm = dict(cfg.llm or {})
        if provider.strip():
            llm["provider"] = provider.strip()
        if model_id.strip():
            llm["model"] = model_id.strip()
        if base_url.strip():
            llm["base_url"] = base_url.strip()
        if api_key.strip():
            llm["api_key"] = api_key.strip()
        if not llm:
            raise click.UsageError("Provide at least one of --provider, --model-id, --base-url, --api-key, or --clear")
        cfg.llm = llm
        mgr.save(cfg)
        out = dict(llm)
        if out.get("api_key"):
            out["api_key"] = "********"
        click.echo(jsonlib.dumps({"ok": True, "agentId": aid, "llm": out}, ensure_ascii=False, indent=2))

    @agent_group.command(
        "del",
        help="Delete an agent: remove ~/.mw4agent/agents/<agent-id>/ (sessions, workspace, agent.json)",
    )
    @click.argument("agent_id", nargs=1)
    @click.option(
        "--force",
        is_flag=True,
        help="Allow deleting the 'main' agent (dangerous; removes default agent state)",
    )
    @click.pass_context
    def agent_del(click_ctx: click.Context, agent_id: str, force: bool) -> None:
        mgr = AgentManager()
        raw = (agent_id or "").strip()
        if not raw:
            click.echo("agent_id is required", err=True)
            click_ctx.exit(2)
        aid = normalize_agent_id(raw)
        try:
            removed = mgr.delete(aid, allow_main=force)
        except ValueError as e:
            click.echo(str(e), err=True)
            click_ctx.exit(1)
        if not removed:
            payload = {"ok": False, "error": "not_found", "message": f"agent directory does not exist: {aid}"}
            click.echo(jsonlib.dumps(payload, ensure_ascii=False, indent=2), err=True)
            click_ctx.exit(1)
        click.echo(
            jsonlib.dumps(
                {"ok": True, "deleted": True, "agentId": aid},
                ensure_ascii=False,
                indent=2,
            )
        )

    @agent_group.command("run", help="Run one agent turn via Gateway RPC (optionally with a tool)")
    @click.option("-m", "--message", required=True, help="User message to send to the agent")
    @click.option("--url", help="Gateway base URL (http://host:port)")
    @click.option("--session-key", default="cli:default", show_default=True, help="Session key")
    @click.option("--session-id", default="", show_default=False, help="Session id (optional; omit to let gateway manage)")
    @click.option("--agent-id", default="main", show_default=True, help="Target agent id")
    @click.option(
        "--with-gateway-ls",
        is_flag=True,
        help="Call the gateway_ls agent tool first and inject its result into the system prompt",
    )
    @click.option(
        "--ls-path",
        default=".",
        show_default=True,
        help="Path argument for gateway_ls when --with-gateway-ls is set",
    )
    @click.option("--timeout", type=int, default=30000, show_default=True, help="agent.wait timeout (ms)")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def agent_run(
        click_ctx: click.Context,
        message: str,
        url: Optional[str],
        session_key: str,
        session_id: str,
        agent_id: str,
        with_gateway_ls: bool,
        ls_path: str,
        timeout: int,
        json_output: bool,
    ) -> None:
        """Trigger one LLM run via Gateway RPC, optionally using the gateway_ls tool."""

        base_url = url or "http://127.0.0.1:18790"
        extra_system_prompt: Optional[str] = None

        if with_gateway_ls:
            tool = GatewayLsTool()

            async def _run_tool() -> str:
                result = await tool.execute(
                    tool_call_id="cli-gateway-ls",
                    params={"path": ls_path},
                    context={"gateway_base_url": base_url},
                )
                if not result.success:
                    return f"gateway_ls(path={ls_path}) failed: {result.error or result.result!r}"
                return f"gateway_ls(path={ls_path}) result: {result.result!r}"

            try:
                tool_summary = asyncio.run(_run_tool())
            except Exception as e:  # pragma: no cover - defensive
                tool_summary = f"gateway_ls(path={ls_path}) raised error: {e}"
            extra_system_prompt = (
                "You are an assistant that has already executed a filesystem listing tool.\n"
                f"{tool_summary}\n"
                "Use this information when answering the user."
            )

        idem = str(uuid.uuid4())
        agent_params = {
            "message": message,
            "sessionKey": session_key,
            "agentId": agent_id.strip() or "main",
            "idempotencyKey": idem,
        }
        if session_id.strip():
            agent_params["sessionId"] = session_id.strip()
        if extra_system_prompt:
            agent_params["extraSystemPrompt"] = extra_system_prompt

        # 1) Fire agent run
        start_res = call_rpc(base_url=base_url, method="agent", params=agent_params, timeout_ms=timeout)
        if start_res.get("ok") is not True:
            if json_output:
                click.echo(jsonlib.dumps(start_res, indent=2), err=True)
            else:
                click.echo(f"Agent start failed: {start_res}", err=True)
            click_ctx.exit(1)

        run_id = str(start_res.get("runId") or (start_res.get("payload") or {}).get("runId") or "")
        if not run_id:
            if json_output:
                click.echo(jsonlib.dumps(start_res, indent=2), err=True)
            else:
                click.echo("Agent start response missing runId", err=True)
            click_ctx.exit(1)

        # 2) Wait for completion
        wait_res = call_rpc(
            base_url=base_url,
            method="agent.wait",
            params={"runId": run_id, "timeoutMs": timeout},
            timeout_ms=timeout + 1000,
        )

        if json_output:
            click.echo(
                jsonlib.dumps(
                    {
                        "start": start_res,
                        "wait": wait_res,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        payload = wait_res.get("payload") or {}
        status = payload.get("status") or "unknown"
        click.echo(f"RunId: {run_id}")
        click.echo(f"Status: {status}")
        if "startedAt" in payload:
            click.echo(f"StartedAt: {payload.get('startedAt')}")
        if "endedAt" in payload:
            click.echo(f"EndedAt: {payload.get('endedAt')}")

