"""Register gateway CLI commands"""

import click
import json as jsonlib
from typing import Optional
from ..context import ProgramContext
from ...gateway.client import call_rpc


def register_gateway_cli(program: click.Group, ctx: ProgramContext) -> None:
    """Register gateway commands - similar to registerGatewayCli in OpenClaw"""
    
    @program.group("gateway", help="Run, inspect, and query the WebSocket Gateway")
    @click.pass_context
    def gateway(ctx: click.Context):
        """Gateway command group"""
        pass

    @gateway.command("run", help="Run the WebSocket Gateway (foreground)")
    @click.option("--port", type=int, default=18790, help="Gateway port")
    @click.option("--bind", default="127.0.0.1", help="Bind address")
    @click.option("--force", is_flag=True, help="Kill existing gateway on port")
    @click.option("--dev", is_flag=True, help="Dev profile")
    @click.option(
        "--session-file",
        default="",
        show_default=False,
        help="Optional session store file (omit to use per-agent stores under ~/.mw4agent/agents/<agentId>/sessions/)",
    )
    @click.option("--node-token", help="Token required for node connections (or set GATEWAY_NODE_TOKEN); omit to allow unauthenticated nodes (dev)")
    @click.pass_context
    def gateway_run(ctx: click.Context, port: int, bind: str, force: bool, dev: bool, session_file: str, node_token: Optional[str]):
        """Run the gateway"""
        click.echo(f"Running gateway on http://{bind}:{port}")
        if dev:
            click.echo("Dev profile enabled")
        if force:
            click.echo("Force mode is not implemented (no auto-kill).")
        if node_token:
            click.echo("Node authentication enabled (node token set)")

        from ...gateway.server import create_app  # local import to keep CLI light

        try:
            import uvicorn
        except Exception as e:
            raise click.ClickException(f"uvicorn not available: {e}")

        app = create_app(session_file=session_file.strip(), node_token=node_token)
        uvicorn.run(app, host=bind, port=port, log_level="info")

    @gateway.command("status", help="Show gateway service status + probe the Gateway")
    @click.option("--url", help="Gateway base URL (http://host:port)")
    @click.option("--token", help="Gateway token")
    @click.option("--timeout", type=int, default=3000, help="Timeout in ms")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def gateway_status(ctx: click.Context, url: Optional[str], token: Optional[str], timeout: int, json_output: bool):
        """Show gateway status"""
        base_url = url or "http://127.0.0.1:18790"
        try:
            res = call_rpc(base_url=base_url, method="health", params={}, timeout_ms=timeout)
            reachable = bool(res.get("ok") is True)
            status = "ok" if reachable else "error"
        except Exception as e:
            reachable = False
            status = f"error: {e}"
            res = {"ok": False, "error": {"message": str(e)}}

        if json_output:
            click.echo(
                jsonlib.dumps(
                    {"url": base_url, "reachable": reachable, "status": status, "health": res}, indent=2
                )
            )
        else:
            click.echo("Gateway Status")
            click.echo(f"  URL: {base_url}")
            click.echo(f"  Reachable: {reachable}")
            click.echo(f"  Status: {status}")

    @gateway.command("call", help="Call a Gateway method")
    @click.argument("method", required=True)
    @click.option("--params", default="{}", help="JSON object string for params")
    @click.option("--url", help="Gateway base URL (http://host:port)")
    @click.option("--token", help="Gateway token")
    @click.option("--timeout", type=int, default=30000, help="Timeout in ms")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def gateway_call(
        ctx: click.Context,
        method: str,
        params: str,
        url: Optional[str],
        token: Optional[str],
        timeout: int,
        json_output: bool,
    ):
        """Call a gateway RPC method"""
        try:
            params_obj = jsonlib.loads(params)
        except jsonlib.JSONDecodeError:
            click.echo(f"Error: Invalid JSON in --params: {params}", err=True)
            ctx.exit(1)

        base_url = url or "http://127.0.0.1:18790"
        res = call_rpc(base_url=base_url, method=method, params=params_obj, timeout_ms=timeout)
        if json_output:
            click.echo(jsonlib.dumps(res, indent=2))
        else:
            click.echo(jsonlib.dumps(res, indent=2))

    @gateway.command("health", help="Fetch Gateway health")
    @click.option("--url", help="Gateway base URL (http://host:port)")
    @click.option("--token", help="Gateway token")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    @click.pass_context
    def gateway_health(ctx: click.Context, url: Optional[str], token: Optional[str], json_output: bool):
        """Fetch gateway health"""
        base_url = url or "http://127.0.0.1:18790"
        res = call_rpc(base_url=base_url, method="health", params={}, timeout_ms=3000)
        if json_output:
            click.echo(jsonlib.dumps(res, indent=2))
        else:
            click.echo(jsonlib.dumps(res, indent=2))

    @gateway.command("discover", help="Discover gateways via Bonjour (local + wide-area if configured)")
    @click.option("--timeout", type=int, default=2000, help="Per-command timeout in ms")
    @click.option("--json", is_flag=True, help="Output JSON")
    @click.pass_context
    def gateway_discover(ctx: click.Context, timeout: int, json: bool):
        """Discover gateways"""
        if json:
            result = {
                "timeout_ms": timeout,
                "domains": ["local."],
                "count": 0,
                "beacons": [],
            }
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo("Gateway Discovery")
            click.echo(f"  Found 0 gateway(s) · domains: local.")
            click.echo("  (Discovery not implemented yet)")

    @gateway.command("probe", help="Show gateway reachability + discovery + health + status summary")
    @click.option("--url", help="Explicit Gateway WebSocket URL")
    @click.option("--token", help="Gateway token")
    @click.option("--timeout", type=int, default=3000, help="Overall probe budget in ms")
    @click.option("--json", is_flag=True, help="Output JSON")
    @click.pass_context
    def gateway_probe(
        ctx: click.Context,
        url: Optional[str],
        token: Optional[str],
        timeout: int,
        json: bool,
    ):
        """Probe gateway"""
        if json:
            result = {
                "reachable": False,
                "url": url or "ws://127.0.0.1:18790",
                "status": "unknown",
            }
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo("Gateway Probe")
            click.echo(f"  URL: {url or 'ws://127.0.0.1:18790'}")
            click.echo("  Status: Unknown (not implemented)")
            # TODO: Implement actual probe
