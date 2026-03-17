"""Configuration CLI for MW4Agent.

Configure LLM provider/model and channels (feishu, console) in ~/.mw4agent/mw4agent.json.
Interactive wizard: choose section (LLM provider / Channels) then fill in values.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from ..config.root import get_root_config_path, read_root_config, write_root_config
from ..llm import list_providers
from ..agents.tools.policy import (
    ToolPolicyConfig,
    resolve_tool_policy_config,
    resolve_effective_policy_for_context,
    filter_tools_by_policy,
)
from ..agents.tools import get_tool_registry

# Supported channel ids for channels configuration
SUPPORTED_CHANNELS = ["feishu", "console"]

# Config section choices in wizard: display name -> section key
CONFIG_SECTION_CHOICES = [
    ("LLM provider", "llm"),
    ("Channels", "channels"),
    ("Continue (skip this time)", "skip"),
    ("Done (exit)", "exit"),
]


def _llm_provider_choices() -> List[str]:
    """Return ordered list of provider ids for LLM (echo + registered HTTP providers)."""
    return ["echo"] + list(list_providers())


def _update_llm_section(
    cfg: Dict[str, Any],
    provider: str,
    model_id: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    next_cfg = dict(cfg)
    llm = dict(next_cfg.get("llm") or {})
    llm["provider"] = provider
    llm["model_id"] = model_id
    if base_url is not None:
        llm["base_url"] = base_url
    if api_key is not None:
        llm["api_key"] = api_key
    next_cfg["llm"] = llm
    return next_cfg


def _update_channels_section(
    cfg: Dict[str, Any],
    channel_id: str,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
    connection_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge one channel's config into root config. Feishu: app_id, app_secret, connection_mode (webhook|websocket)."""
    next_cfg = dict(cfg)
    channels = dict(next_cfg.get("channels") or {})
    channel_cfg = dict(channels.get(channel_id) or {})
    if app_id is not None:
        channel_cfg["app_id"] = app_id
    if app_secret is not None:
        channel_cfg["app_secret"] = app_secret
    if connection_mode is not None and channel_id == "feishu":
        channel_cfg["connection_mode"] = connection_mode
    channels[channel_id] = channel_cfg
    next_cfg["channels"] = channels
    return next_cfg


def _prompt_config_section() -> Optional[str]:
    """Prompt user to select which section to configure (llm or channels). Returns section key or None."""
    try:
        import questionary
    except ImportError:
        return None
    choices = [label for label, _ in CONFIG_SECTION_CHOICES]
    prompt = questionary.select(
        "What do you want to configure? (↑/↓ move, Enter confirm)",
        choices=choices,
        default=choices[0],
    )
    result = prompt.ask()
    if not result:
        return None
    for label, key in CONFIG_SECTION_CHOICES:
        if result == label:
            return key
    return None


def _prompt_provider_list(current_provider: Optional[str]) -> Optional[str]:
    """Show a list of providers; user moves with arrow keys, Space/Enter to select. Returns selected or None if cancelled."""
    try:
        import questionary
    except ImportError:
        return None
    choices = _llm_provider_choices()
    default = (current_provider or "echo").strip().lower()
    if default not in choices:
        default = choices[0]
    prompt = questionary.select(
        "Select LLM provider (↑/↓ move, Enter confirm)",
        choices=choices,
        default=default,
    )
    result = prompt.ask()
    return str(result).strip() if result else None


def _run_llm_config(current: Dict[str, Any]) -> Dict[str, Any]:
    """Run LLM provider/model prompts; return updated config (or current if skipped)."""
    llm = current.get("llm") or {}
    if llm:
        click.echo("Current LLM configuration:")
        click.echo(f"  provider : {llm.get('provider')}")
        click.echo(f"  model_id : {llm.get('model_id')}")
        if llm.get("base_url"):
            click.echo(f"  base_url : {llm.get('base_url')}")
        if llm.get("api_key"):
            click.echo("  api_key : ********")
        click.echo("")

    provider = _prompt_provider_list(str(llm.get("provider") or "").strip() or None)
    if provider is None:
        choices = _llm_provider_choices()
        provider = click.prompt(
            "Select provider",
            type=click.Choice(choices, case_sensitive=False),
            default=str(llm.get("provider") or "echo"),
            show_default=True,
        )
    else:
        provider = provider.lower()

    default_model = str(llm.get("model_id") or "").strip() or "YOUR_MODEL_ID"
    model_id = click.prompt("Model ID", default=default_model, show_default=True)
    default_base_url = str(llm.get("base_url") or "").strip()
    base_url = click.prompt(
        "Base URL (leave empty to use provider default)",
        default=default_base_url,
        show_default=bool(default_base_url),
    ).strip() or None

    existing_api_key = str(llm.get("api_key") or "").strip()
    api_key_prompt_default = "********" if existing_api_key else ""
    api_key_input = click.prompt(
        "API Key (leave empty to keep current / unset)",
        default=api_key_prompt_default,
        show_default=bool(api_key_prompt_default),
    ).strip()
    if api_key_input == "********":
        api_key: Optional[str] = existing_api_key or None
    elif api_key_input:
        api_key = api_key_input
    else:
        api_key = None

    return _update_llm_section(
        current,
        provider.strip(),
        model_id.strip(),
        base_url=base_url,
        api_key=api_key,
    )


def _run_channels_config(current: Dict[str, Any]) -> Dict[str, Any]:
    """Run channels config prompts (which channel, then feishu app_id/app_secret or console). Return updated config."""
    channels = current.get("channels") or {}
    if channels:
        click.echo("Current channels configuration:")
        for cid in SUPPORTED_CHANNELS:
            c = channels.get(cid) or {}
            if cid == "feishu" and (c.get("app_id") or c.get("app_secret")):
                mode = (c.get("connection_mode") or "webhook").strip().lower()
                click.echo(f"  feishu : app_id={c.get('app_id') or '(not set)'}, app_secret={'********' if c.get('app_secret') else '(not set)'}, connection_mode={mode}")
            elif cid == "console":
                click.echo("  console : (built-in, no credentials)")
        click.echo("")

    try:
        import questionary
        channel_result = questionary.select(
            "Which channel to configure? (↑/↓ move, Enter confirm)",
            choices=SUPPORTED_CHANNELS,
            default="feishu",
        ).ask()
    except Exception:
        channel_result = click.prompt(
            "Channel",
            type=click.Choice(SUPPORTED_CHANNELS, case_sensitive=False),
            default="feishu",
            show_default=True,
        )
    if not channel_result:
        return current
    channel_id = str(channel_result).strip().lower()
    if channel_id not in SUPPORTED_CHANNELS:
        click.echo(f"Unknown channel '{channel_id}', skipping.")
        return current

    if channel_id == "console":
        click.echo("Console channel is built-in; no configuration needed.")
        return current

    if channel_id == "feishu":
        feishu_cfg = channels.get("feishu") or {}
        default_app_id = str(feishu_cfg.get("app_id") or "").strip()
        app_id = click.prompt("Feishu App ID", default=default_app_id, show_default=bool(default_app_id))
        existing_secret = str(feishu_cfg.get("app_secret") or "").strip()
        secret_default = "********" if existing_secret else ""
        app_secret_input = click.prompt(
            "Feishu App Secret (leave empty to keep current)",
            default=secret_default,
            show_default=bool(secret_default),
        ).strip()
        app_secret = existing_secret if app_secret_input == "********" else (app_secret_input or None)
        default_mode = str(feishu_cfg.get("connection_mode") or "webhook").strip().lower()
        if default_mode not in ("webhook", "websocket"):
            default_mode = "webhook"
        connection_mode = click.prompt(
            "Connection mode (webhook or websocket)",
            type=click.Choice(["webhook", "websocket"], case_sensitive=False),
            default=default_mode,
            show_default=True,
        )
        return _update_channels_section(
            current, "feishu",
            app_id=app_id or None,
            app_secret=app_secret,
            connection_mode=connection_mode.strip().lower(),
        )

    return current


def _run_interactive_wizard() -> None:
    """Run an interactive configuration wizard: choose section (LLM / Channels) then configure."""
    click.echo("MW4Agent configuration wizard")
    click.echo("")

    current = read_root_config()
    while True:
        section = _prompt_config_section()
        if section is None:
            # Fallback: no questionary or user cancelled → ask with click
            choice_labels = [c[0] for c in CONFIG_SECTION_CHOICES]
            prompt_line = " ".join(f"{i+1}={choice_labels[i]}" for i in range(len(CONFIG_SECTION_CHOICES)))
            idx = click.prompt(
                f"What to configure? ({prompt_line})",
                type=click.IntRange(1, len(CONFIG_SECTION_CHOICES)),
                default=1,
                show_default=True,
            )
            section = CONFIG_SECTION_CHOICES[idx - 1][1]

        if section == "exit":
            break
        if section == "skip":
            click.echo("Skipped. You can configure later.")
        elif section == "llm":
            current = _run_llm_config(current)
            write_root_config(current)
            click.echo(f"LLM configuration saved to {get_root_config_path()}")
        elif section == "channels":
            current = _run_channels_config(current)
            write_root_config(current)
            click.echo(f"Channels configuration saved to {get_root_config_path()}")

        click.echo("")
        if not click.confirm("Configure another section?", default=False):
            break
        click.echo("")


def _tools_cfg_key_for_scope(
    scope: str,
    *,
    channel: Optional[str],
    user_id: Optional[str],
    is_owner: Optional[bool],
) -> tuple[str, Optional[str]]:
    """Return (path_kind, key) for where to store policy under root['tools'].

    path_kind is one of: 'global', 'by_channel', 'by_user', 'by_channel_user'.
    key is the nested key (e.g. 'feishu' or 'owner:local' or 'feishu:ou_xxx').
    """
    s = (scope or "").strip().lower()
    if s in ("global", "root"):
        return "global", None
    if s in ("channel", "by_channel"):
        if not channel:
            raise click.UsageError("--channel is required for scope=by_channel")
        return "by_channel", str(channel).strip()
    if s in ("user", "by_user"):
        if not user_id:
            raise click.UsageError("--user-id is required for scope=by_user")
        prefix = "owner" if is_owner else "user"
        return "by_user", f"{prefix}:{str(user_id).strip()}"
    if s in ("channel-user", "channel_user", "by_channel_user"):
        if not channel or not user_id:
            raise click.UsageError("--channel and --user-id are required for scope=by_channel_user")
        return "by_channel_user", f"{str(channel).strip()}:{str(user_id).strip()}"
    raise click.UsageError(f"Unknown scope: {scope}")


def _merge_policy_dict(
    existing: Dict[str, Any],
    *,
    profile: Optional[str],
    allow: Optional[List[str]],
    deny: Optional[List[str]],
    clear_allow: bool,
    clear_deny: bool,
) -> Dict[str, Any]:
    out = dict(existing or {})
    if profile is not None:
        out["profile"] = str(profile).strip().lower()
    if clear_allow:
        out["allow"] = []
    if clear_deny:
        out["deny"] = []
    if allow is not None:
        cur = out.get("allow")
        if not isinstance(cur, list):
            cur = []
        cur = list(cur)
        for x in allow:
            x = str(x).strip()
            if x and x not in cur:
                cur.append(x)
        out["allow"] = cur
    if deny is not None:
        cur = out.get("deny")
        if not isinstance(cur, list):
            cur = []
        cur = list(cur)
        for x in deny:
            x = str(x).strip()
            if x and x not in cur:
                cur.append(x)
        out["deny"] = cur
    return out


def _prompt_select(title: str, choices: List[str], default: Optional[str] = None) -> str:
    """questionary.select fallback to click.Choice."""
    try:
        import questionary

        if default and default in choices:
            res = questionary.select(title, choices=choices, default=default).ask()
        else:
            res = questionary.select(title, choices=choices).ask()
        if res:
            return str(res)
    except Exception:
        pass
    return click.prompt(title, type=click.Choice(choices, case_sensitive=False), default=default or choices[0])


def _prompt_text(title: str, default: str = "") -> str:
    try:
        import questionary

        res = questionary.text(title, default=default).ask()
        if res is not None:
            return str(res)
    except Exception:
        pass
    return click.prompt(title, default=default, show_default=bool(default))


def _prompt_yesno(title: str, default: bool = False) -> bool:
    try:
        import questionary

        res = questionary.confirm(title, default=default).ask()
        if res is not None:
            return bool(res)
    except Exception:
        pass
    return bool(click.confirm(title, default=default))


def _auth_wizard_set() -> None:
    """Interactive wizard to set tool auth policy (scope -> channel/user -> profile/allow/deny)."""
    click.echo("")
    click.echo("MW4Agent tools auth wizard")
    click.echo("")
    scope = _prompt_select(
        "Select scope",
        choices=["global", "by_channel", "by_user", "by_channel_user"],
        default="by_channel",
    )
    channel = None
    user_id = None
    is_owner = None
    if scope == "by_channel":
        channel = _prompt_text("Channel id (e.g. feishu/telegram/console/webhook)", default="feishu").strip()
    elif scope == "by_user":
        is_owner = _prompt_select("User type", choices=["owner", "user"], default="owner") == "owner"
        user_id = _prompt_text("User id", default="local").strip()
    elif scope == "by_channel_user":
        channel = _prompt_text("Channel id (e.g. feishu/telegram/console/webhook)", default="feishu").strip()
        user_id = _prompt_text("User id", default="").strip()

    profile = _prompt_select("Profile", choices=["minimal", "coding", "full"], default="coding").strip().lower()
    allow_list: List[str] = []
    deny_list: List[str] = []
    if _prompt_yesno("Add allow rules?", default=False):
        while True:
            v = _prompt_text("Allow (tool name or glob, empty to stop)", default="").strip()
            if not v:
                break
            allow_list.append(v)
    if _prompt_yesno("Add deny rules?", default=False):
        while True:
            v = _prompt_text("Deny (tool name or glob, empty to stop)", default="").strip()
            if not v:
                break
            deny_list.append(v)

    fs_workspace_only: Optional[bool] = None
    if scope == "global":
        fs_workspace_only = _prompt_yesno(
            "Restrict filesystem tools to workspace only? (tools.fs.workspaceOnly)",
            default=False,
        )

    current = read_root_config()
    tools = dict(current.get("tools") or {})
    kind, key = _tools_cfg_key_for_scope(scope, channel=channel, user_id=user_id, is_owner=is_owner)
    if kind == "global":
        old = tools
        tools = _merge_policy_dict(old, profile=profile, allow=allow_list or None, deny=deny_list or None, clear_allow=False, clear_deny=False)
        if fs_workspace_only is not None:
            fs = dict(tools.get("fs") or {})
            fs["workspaceOnly"] = bool(fs_workspace_only)
            tools["fs"] = fs
    else:
        bucket = dict(tools.get(kind) or {})
        old = dict(bucket.get(key) or {})
        bucket[key] = _merge_policy_dict(old, profile=profile, allow=allow_list or None, deny=deny_list or None, clear_allow=False, clear_deny=False)
        tools[kind] = bucket
    current["tools"] = tools

    click.echo("")
    click.echo("Preview (tools section):")
    click.echo(json.dumps(current.get("tools") or {}, ensure_ascii=False, indent=2))
    if not click.confirm("Write this configuration?", default=True):
        click.echo("Cancelled.")
        return
    write_root_config(current)
    click.echo(f"Saved to {get_root_config_path()}")


def register_configuration_cli(program: click.Group, _ctx) -> None:
    _provider_choices = _llm_provider_choices()

    @program.group(
        name="configuration",
        help="Configure MW4Agent (LLM, channels, skills, etc.)",
        invoke_without_command=True,
    )
    @click.pass_context
    def configuration_group(ctx: click.Context) -> None:
        # No subcommand → run interactive wizard.
        if ctx.invoked_subcommand is None:
            _run_interactive_wizard()

    @configuration_group.command(name="set-llm", help="Set LLM provider and model id")
    @click.option(
        "--provider",
        type=click.Choice(_provider_choices, case_sensitive=False),
        required=True,
        help="LLM provider: " + ", ".join(_provider_choices),
    )
    @click.option(
        "--model-id",
        required=True,
        help="Model identifier for the selected provider",
    )
    @click.option(
        "--base-url",
        required=False,
        help="Optional base URL for the selected provider (e.g. http://127.0.0.1:8000)",
    )
    @click.option(
        "--api-key",
        required=False,
        help="Optional API key for the selected provider",
    )
    def set_llm(provider: str, model_id: str, base_url: Optional[str], api_key: Optional[str]) -> None:
        """Update LLM config and persist to ~/.mw4agent/mw4agent.json."""
        current = read_root_config()
        normalized_provider = provider.strip()
        updated = _update_llm_section(
            current,
            normalized_provider,
            model_id.strip(),
            base_url.strip() if base_url else None,
            api_key.strip() if api_key else None,
        )
        write_root_config(updated)
        path = get_root_config_path()
        click.echo(f"LLM configuration updated in {path}")

    @configuration_group.command(
        name="set-channels",
        help="Set channels configuration (feishu: app_id, app_secret, connection_mode). Supported: feishu, console.",
    )
    @click.option(
        "--channel",
        type=click.Choice(SUPPORTED_CHANNELS, case_sensitive=False),
        default="feishu",
        help="Channel to configure",
    )
    @click.option("--app-id", required=False, help="Feishu App ID (for channel feishu)")
    @click.option("--app-secret", required=False, help="Feishu App Secret (for channel feishu)")
    @click.option(
        "--connection-mode",
        type=click.Choice(["webhook", "websocket"], case_sensitive=False),
        default=None,
        help="Feishu connection mode: webhook (HTTP callback) or websocket (lark-oapi). Default: keep existing or webhook",
    )
    def set_channels(
        channel: str,
        app_id: Optional[str],
        app_secret: Optional[str],
        connection_mode: Optional[str],
    ) -> None:
        """Update channels config and persist to ~/.mw4agent/mw4agent.json."""
        current = read_root_config()
        kwargs = dict(
            app_id=app_id.strip() if app_id else None,
            app_secret=app_secret.strip() if app_secret else None,
        )
        if channel.strip().lower() == "feishu" and connection_mode is not None:
            kwargs["connection_mode"] = connection_mode.strip().lower()
        updated = _update_channels_section(current, channel.strip().lower(), **kwargs)
        write_root_config(updated)
        click.echo(f"Channels configuration updated in {get_root_config_path()}")

    @configuration_group.command(name="show", help="Show current root configuration")
    @click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        help="Output raw JSON",
    )
    def show(as_json: bool) -> None:
        cfg = read_root_config()
        path = get_root_config_path()
        if as_json:
            click.echo(json.dumps(cfg, ensure_ascii=False, indent=2))
        else:
            click.echo(f"Config file: {path}")
            if not cfg:
                click.echo("No configuration set yet.")
                return
            llm = cfg.get("llm") or {}
            if llm:
                click.echo("LLM configuration:")
                click.echo(f"  provider : {llm.get('provider')}")
                click.echo(f"  model_id : {llm.get('model_id')}")
                if llm.get("base_url"):
                    click.echo(f"  base_url : {llm.get('base_url')}")
                if llm.get("api_key"):
                    click.echo("  api_key : ********")
            else:
                click.echo("LLM configuration: not set")
            channels = cfg.get("channels") or {}
            if channels:
                click.echo("Channels configuration:")
                for cid in SUPPORTED_CHANNELS:
                    c = channels.get(cid) or {}
                    if cid == "feishu":
                        mode = (c.get("connection_mode") or "webhook").strip().lower()
                        click.echo(f"  feishu  : app_id={c.get('app_id') or '(not set)'}, app_secret={'********' if c.get('app_secret') else '(not set)'}, connection_mode={mode}")
                    elif cid == "console":
                        click.echo("  console : (built-in, no credentials)")
                    else:
                        click.echo(f"  {cid}: (configured)" if c else f"  {cid}: (not set)")
            else:
                click.echo("Channels configuration: not set")

    @configuration_group.group(name="auth", help="Configure tool permissions (tools policy) interactively or via commands.")
    def auth_group() -> None:
        return None

    @auth_group.command(name="wizard", help="Interactive wizard for tool permissions (scope -> channel/user -> policy).")
    def auth_wizard() -> None:
        _auth_wizard_set()

    @auth_group.command(name="show", help="Show current tools policy configuration")
    @click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
    def auth_show(as_json: bool) -> None:
        cfg = read_root_config()
        tools = cfg.get("tools") or {}
        if as_json:
            click.echo(json.dumps(tools, ensure_ascii=False, indent=2))
        else:
            click.echo(f"Config file: {get_root_config_path()}")
            click.echo("Tools policy:")
            click.echo(json.dumps(tools, ensure_ascii=False, indent=2))

    @auth_group.command(name="set", help="Set tools policy for a scope (non-interactive).")
    @click.option("--scope", type=click.Choice(["global", "by_channel", "by_user", "by_channel_user"], case_sensitive=False), required=True)
    @click.option("--channel", required=False, help="Channel id (for by_channel / by_channel_user)")
    @click.option("--user-id", required=False, help="User id (for by_user / by_channel_user)")
    @click.option("--owner/--user", "is_owner", default=None, help="For by_user: owner or normal user")
    @click.option("--profile", type=click.Choice(["minimal", "coding", "full"], case_sensitive=False), required=False)
    @click.option("--allow", "allow_list", multiple=True, required=False, help="Allow tool name or glob (repeatable)")
    @click.option("--deny", "deny_list", multiple=True, required=False, help="Deny tool name or glob (repeatable)")
    @click.option("--clear-allow", is_flag=True, default=False, help="Clear allow list before adding")
    @click.option("--clear-deny", is_flag=True, default=False, help="Clear deny list before adding")
    @click.option(
        "--fs-workspace-only/--no-fs-workspace-only",
        "fs_workspace_only",
        default=None,
        help="Global only: set tools.fs.workspaceOnly (restrict read/write paths to workspace).",
    )
    def auth_set(
        scope: str,
        channel: Optional[str],
        user_id: Optional[str],
        is_owner: Optional[bool],
        profile: Optional[str],
        allow_list: tuple[str, ...],
        deny_list: tuple[str, ...],
        clear_allow: bool,
        clear_deny: bool,
        fs_workspace_only: Optional[bool],
    ) -> None:
        current = read_root_config()
        tools = dict(current.get("tools") or {})
        kind, key = _tools_cfg_key_for_scope(scope, channel=channel, user_id=user_id, is_owner=is_owner)
        if kind == "global":
            tools = _merge_policy_dict(
                tools,
                profile=profile,
                allow=list(allow_list) if allow_list else None,
                deny=list(deny_list) if deny_list else None,
                clear_allow=clear_allow,
                clear_deny=clear_deny,
            )
            if fs_workspace_only is not None:
                fs = dict(tools.get("fs") or {})
                fs["workspaceOnly"] = bool(fs_workspace_only)
                tools["fs"] = fs
        else:
            if fs_workspace_only is not None:
                raise click.UsageError("--fs-workspace-only only applies to scope=global")
            bucket = dict(tools.get(kind) or {})
            old = dict(bucket.get(key) or {})
            bucket[key] = _merge_policy_dict(
                old,
                profile=profile,
                allow=list(allow_list) if allow_list else None,
                deny=list(deny_list) if deny_list else None,
                clear_allow=clear_allow,
                clear_deny=clear_deny,
            )
            tools[kind] = bucket
        current["tools"] = tools
        write_root_config(current)
        click.echo(f"Tools policy updated in {get_root_config_path()}")

    @auth_group.command(name="effective", help="Show effective tools policy and allowed tools for a given context.")
    @click.option("--channel", required=True, help="Channel id (e.g. feishu)")
    @click.option("--user-id", required=True, help="User id (e.g. open_id)")
    @click.option("--owner/--no-owner", "is_owner", default=False, help="Whether sender is owner")
    @click.option("--authorized/--no-authorized", "authorized", default=True, help="Whether commands are authorized")
    @click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON")
    def auth_effective(channel: str, user_id: str, is_owner: bool, authorized: bool, as_json: bool) -> None:
        from ..config import get_default_config_manager

        cfg_mgr = get_default_config_manager()
        base = resolve_tool_policy_config(cfg_mgr)
        eff = resolve_effective_policy_for_context(
            cfg_mgr,
            base_policy=base,
            channel=channel,
            user_id=user_id,
            sender_is_owner=is_owner,
            command_authorized=authorized,
        )
        all_tools = get_tool_registry().list_tools()
        allowed = filter_tools_by_policy(all_tools, eff)
        if not is_owner:
            allowed = [t for t in allowed if not t.owner_only]
        payload = {
            "context": {"channel": channel, "user_id": user_id, "owner": is_owner, "authorized": authorized},
            "effectivePolicy": {"profile": eff.profile, "allow": eff.allow, "deny": eff.deny},
            "allowedTools": [t.name for t in allowed],
        }
        if as_json:
            click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
