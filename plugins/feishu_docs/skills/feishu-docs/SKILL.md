---
name: feishu-docs
description: |
  MW4Agent feishu-docs plugin: feishu_fetch_doc, feishu_create_doc, feishu_update_doc.
  Requires UAT for Feishu MCP. In bot chat: `/mw4auth` or `飞书授权` (card); or `mw4agent feishu authorize`; or FEISHU_MCP_UAT / channels.feishu.mcp_user_access_token. Bot app_secret is not sufficient.
---

# Feishu docs (feishu-docs plugin)

## Setup

1. Add parent directory `plugins` to `MW4AGENT_PLUGIN_DIR` or `plugins.plugin_dirs`.
2. User token: `/mw4auth` or `飞书授权` in Feishu chat, or `mw4agent feishu authorize`, or env `FEISHU_MCP_UAT` / config `channels.feishu.mcp_user_access_token`.
3. Optional: `FEISHU_MCP_ENDPOINT`, `FEISHU_MCP_BEARER_TOKEN`.

## Tools

| Tool | MCP |
|------|-----|
| feishu_fetch_doc | fetch-doc |
| feishu_create_doc | create-doc |
| feishu_update_doc | update-doc |

See `plugins/feishu_docs/README.md` and feishu-openclaw-plugin `src/tools/mcp/doc/*.js`.
