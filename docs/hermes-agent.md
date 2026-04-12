# Hermes Agent 代码库说明

本文档基于本地 **`hermes-agent`** 仓库（Nous Research 开源项目）的阅读整理，便于在 **mw4agent** 文档体系中快速理解其职责与架构。权威文档与更新以官方站点为准。

- 官方文档：<https://hermes-agent.nousresearch.com/docs/>
- 上游仓库：<https://github.com/NousResearch/hermes-agent>

---

## 1. 项目定位

**Hermes Agent** 是一套 **Python 实现的、带工具调用的对话型智能体运行时**，强调：

- **自改进闭环**：从任务中生成/改进 *Skills*（与 [agentskills.io](https://agentskills.io) 生态兼容）、持久化记忆、跨会话检索（含 SQLite FTS5 会话搜索与摘要）。
- **多入口一致体验**：同一套 **斜杠命令注册表** 驱动 **终端 TUI** 与 **消息网关**（Telegram、Discord、Slack、WhatsApp、Signal 等）。
- **多模型与多环境**：OpenAI 兼容 API、Anthropic、OpenRouter、各家云厂商等；终端执行后端可本地 / Docker / SSH / Modal / Daytona 等。
- **调度与扩展**：内置 **cron** 定时任务；**MCP** 扩展工具；**子代理（delegate）** 并行任务；可选 **Honcho** 做用户建模等。

一句话：**它是可独立部署的「CLI + 网关 + 工具生态 + 记忆/技能」一体化 Agent 产品**，与笔记本解耦，适合 VPS / Serverless。

---

## 2. 与 mw4agent / OpenClaw 的关系

- **mw4agent（Orbit）** 是另一套以网关 + 多 Agent 编排为核心的实现（TypeScript/Python 混合），文档见本仓库 `docs/architecture/` 等。
- **Hermes** 提供 **`hermes claw migrate`**，可从 **`~/.openclaw`** 导入人设、记忆、技能、消息配置与部分密钥等（README 与官方文档有清单）。
- 二者 **不是同一代码库**：若在架构上对比，可将 Hermes 视为 **全功能单体 Agent 产品**；mw4agent 更侧重 **网关 RPC、多 Agent 编排、桌面与插件边界** 等自有设计。

---

## 3. 技术栈与安装形态

- **语言**：Python **≥ 3.11**
- **包名**：`hermes-agent`（`pyproject.toml` 中版本随上游发布变化）
- **核心依赖**：`openai`、`anthropic`、`httpx`、`rich`、`prompt_toolkit`、`pydantic`、`pyyaml` 等；工具侧含检索/爬虫/TTS 等可选依赖。
- **入口脚本**（`pyproject.toml`）：
  - **`hermes`** → `hermes_cli.main:main`（用户主 CLI）
  - **`hermes-agent`** → `run_agent:main`（偏运行器/脚本入口）
  - **`hermes-acp`** → ACP 适配（编辑器协议）

**可选 extras**（节选）：`messaging`、`cron`、`voice`、`mcp`、`honcho`、`modal`、`daytona`、`feishu`、`dingtalk`、`rl` 等；完整列表见上游 `pyproject.toml` 的 `[project.optional-dependencies]`。

---

## 4. 仓库结构（与代码职责）

以下对应上游 **`AGENTS.md`** 中的结构说明，便于从文件名跳到实现。

| 路径 | 作用 |
|------|------|
| `run_agent.py` | **`AIAgent`**：核心对话循环（同步）、消息格式、与模型 API 交互、工具调用循环、记忆/上下文/压缩等编排入口。 |
| `model_tools.py` | 工具发现、schema 聚合、**`handle_function_call`** 分发。 |
| `toolsets.py` | 工具集定义（如 `_HERMES_CORE_TOOLS`），控制默认启用集合。 |
| `tools/` | 具体工具实现；**`registry.py`** 为注册中心，各工具 `register()`。含终端、文件、网页、浏览器、代码执行、**delegate**、**MCP**、`environments/` 多后端等。 |
| `cli.py` | **`HermesCLI`**：交互式终端 UI（Rich + prompt_toolkit）、与 `AIAgent` 协作。 |
| `hermes_cli/` | 子命令、配置、`commands.py` 中 **`COMMAND_REGISTRY`**（斜杠命令单一数据源）、setup、皮肤引擎、模型切换等。 |
| `agent/` | 提示词拼装、上下文压缩、缓存、模型元数据、展示组件、技能相关命令辅助等。 |
| `hermes_state.py` | **SessionDB**（SQLite，含 FTS5 等会话能力）。 |
| `gateway/` | 消息网关：**`run.py`** 主循环、各平台适配器、`session.py` 会话持久化。 |
| `cron/` | 定时任务调度与任务定义。 |
| `acp_adapter/` | ACP 服务端，对接 VS Code / Zed / JetBrains 等。 |
| `batch_runner.py` / `environments/` 等 | 批处理轨迹、RL 相关环境（可选子模块）。 |

**依赖方向（概念上）**：`tools/registry` ← 各 `tools/*` ← `model_tools` ← `run_agent` / `cli` / `batch_runner` / `environments`。

---

## 5. 核心运行逻辑

### 5.1 对话循环（`AIAgent`）

- 对外常见接口：**`chat()`**（简版返回字符串）、**`run_conversation()`**（返回含最终回复与消息列表等结构）。
- 循环要点：在迭代/预算限制内调用 **Chat Completions 风格 API**；若返回 **tool_calls**，则经 **`handle_function_call`** 执行工具，将 **tool** 角色消息写回上下文，直至模型不再调用工具或达到上限。
- 消息一般为 OpenAI 兼容的 **system / user / assistant / tool** 结构；部分模型会把推理内容放在扩展字段（如 `reasoning`）。

### 5.2 工具系统

- 新工具典型三步（见上游 `AGENTS.md`）：在 **`tools/your_tool.py`** 中 `registry.register(...)`；在 **`model_tools._discover_tools()`** 中增加 import；在 **`toolsets.py`** 中纳入某 toolset。
- 部分「Agent 层」工具（如 todo、memory）可能在 **`run_agent.py`** 内先于通用 `handle_function_call` 拦截处理。

### 5.3 斜杠命令

- **`hermes_cli/commands.py`** 的 **`CommandDef` / `COMMAND_REGISTRY`** 为 **唯一注册源**。
- CLI、Gateway、Telegram 菜单、Slack 子命令、自动补全等均从该注册表派生，减少分叉。

### 5.4 网关（`gateway/run.py`）

- 启动各消息平台适配器，接收用户消息，复用与 CLI 一致的命令解析与 Agent 调用路径（具体异步与会话键策略见该文件）。
- 入口可通过 **`hermes gateway`** 子命令或模块方式运行（见文件头注释）。

---

## 6. 配置与数据目录

- 用户配置：**`~/.hermes/config.yaml`**
- 密钥与环境：**`~/.hermes/.env`**
- 可通过 **`HERMES_HOME`**（及 CLI 的 profile 机制）切换配置根目录，便于多环境隔离。

---

## 7. 用户常用命令（摘自上游 README）

| 场景 | 命令 |
|------|------|
| 交互对话 | `hermes` |
| 选择模型 | `hermes model` |
| 工具开关 | `hermes tools` |
| 单项配置 | `hermes config set` |
| 消息网关 | `hermes gateway` |
| 向导 | `hermes setup` |
| 从 OpenClaw 迁移 | `hermes claw migrate` |
| 自检 | `hermes doctor` |

---

## 8. 小结

| 维度 | 说明 |
|------|------|
| **是什么** | 独立安装的 Python Agent 产品：CLI TUI + 可选消息网关 + 丰富工具与记忆/技能闭环。 |
| **核心代码** | `run_agent.py`（循环）、`model_tools.py`（工具分发）、`tools/`（实现）、`hermes_cli/`（CLI 与子命令）、`gateway/`（消息侧）。 |
| **与 mw4agent** | 不同项目；Hermes 提供从 OpenClaw 配置迁移路径，便于用户切换生态。 |
| **深入阅读** | 上游 **`AGENTS.md`**（开发者指南）、**[官方 Architecture 文档](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)**。 |

---

*文档生成说明：基于仓库内 `README.md`、`AGENTS.md`、`pyproject.toml` 及若干入口文件归纳；若上游目录或行为变更，请以 `hermes-agent` 仓库当前版本为准。*
