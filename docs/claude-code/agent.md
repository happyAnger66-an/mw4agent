# Claude Code 功能与架构分析

本文档基于 [anthropics/claude-code](https://github.com/anthropics/claude-code) 仓库，对 Claude Code 的定位、功能与插件架构做归纳，供 MW4Agent 等智能体/网关项目参考。

---

## 1. 定位与功能

**Claude Code** 是 Anthropic 推出的 **Agentic 编程工具**，主要特点：

- **运行位置**：终端、IDE（如 VS Code 插件）、或 GitHub 上 @claude 提及。
- **核心能力**：理解代码库、用自然语言执行任务，包括：
  - 执行日常开发任务（读文件、改代码、跑命令）
  - 解释复杂代码
  - 处理 Git 工作流（提交、推送、PR 等）
- **交互方式**：用户用自然语言或斜杠命令（如 `/commit`、`/code-review`）驱动；Claude 可调用多种工具（读文件、Bash、MCP 等），并在对话中持续执行多步任务。
- **分发形态**：CLI 通过官方安装脚本 / Homebrew / WinGet 等安装，**运行时本体不在此开源仓库**；本仓库主要提供 **插件生态与插件开发文档**。

与本仓库的关系：**本仓库 = 官方插件集合 + 插件开发技能（Skills）+ 仓库维护脚本**，不是 Claude Code 运行时源码。

---

## 2. 仓库内容概览

### 2.1 插件目录 `plugins/`

存放多款官方插件，用于扩展 Claude Code 的斜杠命令、专用 Agent、钩子和 MCP 工具。典型插件包括：

| 插件 | 作用 |
|------|------|
| **code-review** | 多 Agent 并行做 PR 代码审查，带置信度过滤 |
| **commit-commands** | Git 工作流：`/commit`、`/commit-push-pr`、`/clean_gone` |
| **feature-dev** | 功能开发流程：`/feature-dev` + code-explorer / code-architect / code-reviewer |
| **pr-review-toolkit** | PR 审查工具集：评论、测试、错误处理、类型设计、代码简化等专项 Agent |
| **hookify** | 用自然语言配置“规则”，自动生成 Hook，约束对话行为（如禁止 `rm -rf`、限制 console.log） |
| **plugin-dev** | 插件开发套件：`/plugin-dev:create-plugin`、Agent（agent-creator、plugin-validator、skill-reviewer）、多份 Skill（插件结构、命令、Agent、Hook、MCP、设置等） |
| **security-guidance** | 安全提醒 Hook：在 Edit/Write 等操作前检查 9 类风险（命令注入、XSS、eval、pickle 等） |
| **ralph-wiggum** | 自循环迭代：`/ralph-loop`、`/cancel-ralph`，Stop Hook 拦截退出以继续迭代 |
| **frontend-design** / **learning-output-style** / **explanatory-output-style** | 前端设计 Skill、学习/解释型输出风格（Hook/SessionStart） |

### 2.2 脚本 `scripts/`

仓库级自动化，与“使用 Claude Code”无直接关系：

- `sweep.ts`、`backfill-duplicate-comments.ts`、`issue-lifecycle.ts`、`lifecycle-comment.ts` 等：Issues/PR 的自动化或运营脚本。

---

## 3. 插件架构

插件采用 **约定目录 + 清单驱动**，由 Claude Code 运行时自动发现并加载。

### 3.1 目录结构

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json          # 必需：插件清单
├── commands/                # 斜杠命令（.md，YAML frontmatter）
├── agents/                  # 专用子 Agent（.md）
├── skills/                  # Agent Skills
│   └── skill-name/
│       └── SKILL.md         # 每个 Skill 必需
├── hooks/
│   └── hooks.json           # 事件钩子配置
├── .mcp.json                # MCP 服务定义（可选）
└── scripts/                 # 脚本与工具
```

- **清单**：必须放在 `.claude-plugin/plugin.json`，包含 `name`（必填）、`version`、`description`、以及可选的 `commands`/`agents`/`hooks`/`mcpServers` 路径。
- **路径**：均相对插件根目录，以 `./` 开头；支持为 commands/agents 指定多个目录，与默认目录**叠加**加载。
- **可移植路径**：插件内引用脚本或资源时使用环境变量 `${CLAUDE_PLUGIN_ROOT}`，避免写死绝对路径。

### 3.2 组件类型

- **Commands（命令）**  
  - 位置：`commands/*.md`。  
  - 格式：Markdown + YAML frontmatter（如 `name`、`description`、`allowed-tools`）。  
  - 作用：注册为斜杠命令（如 `/commit`）；正文为给 Claude 的说明与上下文（可内联 `!` 执行 shell 获取 git status、diff 等）。

- **Agents（子 Agent）**  
  - 位置：`agents/*.md`。  
  - 格式：Markdown + frontmatter（`name`、`description`、`tools`、`model`、`color` 等）。  
  - 作用：专用于某一类任务（如代码审查、测试生成）；可由用户显式调用，或由 Claude 按任务上下文自动选择。

- **Skills（技能）**  
  - 位置：`skills/<skill-name>/SKILL.md`。  
  - 格式：Markdown + frontmatter（`name`、`description`、`version`），描述“何时使用该技能”。  
  - 作用：根据任务描述**自动激活**，为 Claude 提供领域指引；可配 `references/`、`examples/`、`scripts/` 等辅助材料。

- **Hooks（钩子）**  
  - 配置：`hooks/hooks.json` 或清单内 `hooks` 字段。  
  - 事件：如 `PreToolUse`、`PostToolUse`、`Stop`、`SubagentStop`、`SessionStart`、`SessionEnd`、`UserPromptSubmit`、`PreCompact`、`PostCompact`、`Notification` 等。  
  - 行为：按事件 + matcher（如工具名 `Edit|Write`）执行 `command` 或 `prompt`；用于权限校验、安全提醒、会话前后注入上下文等。

- **MCP Servers**  
  - 配置：`.mcp.json` 或清单内 `mcpServers`。  
  - 作用：定义外部 MCP 服务（command/args/env），由 Claude Code 在启用插件时拉起，供工具调用。

### 3.3 清单示例（plugin.json）

```json
{
  "name": "security-guidance",
  "version": "1.0.0",
  "description": "Security reminder hook that warns...",
  "author": { "name": "...", "email": "..." },
  "commands": "./commands",
  "agents": "./agents",
  "hooks": "./hooks/hooks.json"
}
```

路径可为字符串或数组，均相对插件根目录；未写时使用默认目录（如 `./commands`、`./agents`）。

### 3.4 钩子配置示例（hooks.json）

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py"
          }
        ]
      }
    ]
  }
}
```

即：在调用 Edit/Write/MultiEdit 前执行指定脚本；类型除 `command` 外还可为 `prompt`（由 Claude 根据提示做审批/否决）。

---

## 4. 与 MW4Agent 的对照（简要）

| 维度 | Claude Code | MW4Agent |
|------|-------------|----------|
| 定位 | 面向开发者的编码 Agent（终端/IDE/GitHub） | 面向网关与多通道的 Agent 运行与编排 |
| 扩展方式 | 插件（commands / agents / skills / hooks / MCP） | 内置 tools（read/write/gateway）、skills、通道、LLM 配置 |
| 工具模型 | 内置 + MCP + 插件内 Bash/脚本 | 注册表 + workspace 限定 + owner_only 标记 |
| 事件/钩子 | 会话与工具前后等多阶段 Hook | 事件流（lifecycle/assistant/tool）经 Gateway 广播 |
| 多 Agent | 主 Agent + 子 Agent（agents/*.md） | 单 Runner，可扩展为多 Agent 编排 |

Claude Code 的插件架构（清单、命令、专用 Agent、按描述激活的 Skill、事件钩子）对 MW4Agent 的“技能/工具/会话策略”设计有参考价值；MW4Agent 侧重服务端网关与多端接入，与 Claude Code 的“本机/IDE 编码助手”场景互补。

---

## 5. 参考链接

- Claude Code 官方文档：<https://code.claude.com/docs/en/overview>
- 插件文档：<https://docs.claude.com/en/docs/claude-code/plugins>
- 本仓库插件列表与结构说明：`plugins/README.md`
- 插件结构 Skill（目录、清单、组件）：`plugins/plugin-dev/skills/plugin-structure/SKILL.md`
- 清单字段参考：`plugins/plugin-dev/skills/plugin-structure/references/manifest-reference.md`
- 钩子模式与事件：`plugins/plugin-dev/skills/hook-development/references/patterns.md`
