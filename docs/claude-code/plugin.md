# Claude Code 插件代码架构与开发流程

本文档基于 [anthropics/claude-code](https://github.com/anthropics/claude-code) 仓库中的插件体系与 plugin-dev 工具包，总结**插件的代码架构**（发现、激活、组件格式）与**插件开发流程**（从需求到发布），供 MW4Agent 等项目的扩展机制设计参考。

---

## 1. 插件代码架构

### 1.1 整体模型

- **清单驱动**：每个插件由 `.claude-plugin/plugin.json` 定义元数据与组件路径。
- **约定目录 + 自动发现**：commands、agents、skills、hooks、MCP 按约定路径扫描，无需在清单中逐个声明文件。
- **路径可配置**：清单中的 `commands`、`agents`、`hooks`、`mcpServers` 可指向自定义目录或文件，与默认目录**叠加**加载，而非替换。
- **可移植路径**：插件内所有脚本与资源引用使用环境变量 `${CLAUDE_PLUGIN_ROOT}`，由运行时在加载插件时注入。

### 1.2 发现与激活生命周期

**发现阶段**（Claude Code 启动时）：

1. 扫描已启用插件的 `.claude-plugin/plugin.json`。
2. 按默认路径与清单中的自定义路径发现组件：
   - commands：`commands/` 及自定义路径下的 `.md`
   - agents：`agents/` 及自定义路径下的 `.md`
   - skills：`skills/<name>/SKILL.md`
   - hooks：`hooks/hooks.json` 或清单内 `hooks` 字段
   - MCP：`.mcp.json` 或清单内 `mcpServers`
3. 解析 YAML frontmatter、JSON 配置，完成注册。
4. 初始化：启动 MCP 进程、注册钩子。

**激活阶段**（运行时）：

| 组件   | 触发方式 |
|--------|----------|
| Commands | 用户输入斜杠命令 → 查找对应 .md → 将内容作为给 Claude 的指令执行 |
| Agents   | 任务到达 → 根据 description 与 \<example\> 匹配 → 选定子 Agent 执行 |
| Skills   | 任务上下文与 skill 的 description 匹配 → 加载对应 SKILL.md（及 references/examples） |
| Hooks    | 事件发生（PreToolUse、Stop 等）→ 按 matcher 匹配 → 执行 command 或 prompt |
| MCP      | 工具调用命中 MCP 能力 → 请求转发到对应 MCP 服务 |

### 1.3 组件文件格式与职责

**Commands（`commands/*.md`）**

- **格式**：Markdown + YAML frontmatter。
- **要点**：内容是**给 Claude 的指令**，不是给用户的说明。用户执行 `/xxx` 时，该文件内容作为系统/任务指令注入。
- **常用 frontmatter**：`description`、`argument-hint`、`allowed-tools`（如 `Bash(git add:*)`）、`model`。
- **正文**：可内联 `!` 执行 shell 获取上下文（如 `!git status`、`!git diff HEAD`），供 Claude 据此执行。

**Agents（`agents/*.md`）**

- **格式**：Markdown + YAML frontmatter + 大段 system prompt。
- **要点**：用于**自主多步任务**；何时被选用由 `description` 与多个 `<example>` 块决定。
- **description**：必须写清“何时使用此 agent”，并包含若干：
  - `<example>`：Context / user / assistant / `<commentary>`（为何触发）。
- **其他 frontmatter**：`name`、`model`（如 sonnet）、`color`、`tools`（数组）。
- **正文**：Agent 的系统提示（角色、步骤、输出格式等）。

**Skills（`skills/<name>/SKILL.md`）**

- **格式**：Markdown + YAML frontmatter（`name`、`description`、`version`）。
- **要点**：**按描述自动激活**；description 用第三人称写“何时使用该 skill”，并包含明确触发短语。
- **渐进式披露**：SKILL.md 保持精简（约 1.5k–2k 词），细节放在 `references/`、`examples/`、`scripts/`，按需加载。
- **同一 skill 目录**下可放 references、examples、scripts 等子目录，供 Claude 引用。

**Hooks（`hooks/hooks.json` 或清单内 `hooks`）**

- **插件专用格式**：顶层为 `{ "description": "...", "hooks": { "EventName": [ ... ] } }`。
- **每条钩子**：`matcher`（如 `Edit|Write|MultiEdit`）+ `hooks` 数组，每项为：
  - `type: "command"`：执行脚本，`command` 中使用 `${CLAUDE_PLUGIN_ROOT}`。
  - `type: "prompt"`：由 LLM 根据 prompt 与上下文做审批/否决（推荐复杂逻辑）。
- **事件**：PreToolUse、PostToolUse、Stop、SubagentStop、SessionStart、SessionEnd、UserPromptSubmit、PreCompact、PostCompact、Notification 等。
- **执行顺序**：事件触发 → 按 matcher 过滤 → 依次执行 command 或 prompt；可配置 timeout。

**MCP（`.mcp.json` 或清单内 `mcpServers`）**

- **配置**：每个 server 指定 `command`、`args`、`env`；args 中可用 `${CLAUDE_PLUGIN_ROOT}`。
- **类型**：stdio（本地进程）、SSE（托管/OAuth）、HTTP、WebSocket 等。
- **作用**：插件启用时由 Claude Code 拉起对应进程，将其提供的工具暴露给 Commands/Agents 使用。

### 1.4 清单（plugin.json）与路径规则

- **必填**：`name`（kebab-case，唯一）。
- **推荐**：`version`、`description`、`author`、`homepage`、`repository`、`license`、`keywords`。
- **组件路径**：`commands`、`agents`、`hooks`、`mcpServers` 可为字符串或数组，值均为相对插件根的路径，且必须以 `./` 开头；不可使用绝对路径或 `../`。
- **校验**：清单需合法 JSON；name 符合格式；路径存在且可读；自定义路径与默认路径会合并，同名组件冲突会报错。

---

## 2. 插件开发流程

官方提供的**结构化开发流程**由 plugin-dev 插件的 **`/plugin-dev:create-plugin`** 命令实现，分为 8 个阶段，从需求澄清到文档与发布准备。

### 2.1 八阶段总览

| 阶段 | 目标 | 产出 |
|------|------|------|
| 1. Discovery | 明确插件要解决的问题与用户 | 插件目的与目标用户陈述 |
| 2. Component Planning | 确定需要哪些类型的组件 | 组件计划表（Skills/Commands/Agents/Hooks/MCP 数量与用途） |
| 3. Detailed Design | 细化每个组件的规格并消除歧义 | 各组件的详细规格与澄清问题的答案 |
| 4. Plugin Structure Creation | 创建目录与清单 | 插件根目录、.claude-plugin/plugin.json、README 等 |
| 5. Component Implementation | 按规格实现各组件 | 所有 commands/agents/skills/hooks/MCP 等 |
| 6. Validation & Quality Check | 校验清单与组件质量 | 校验报告与修复项 |
| 7. Testing & Verification | 在 Claude Code 中实测 | 安装方式与自测清单 |
| 8. Documentation & Next Steps | 完善文档与发布准备 | README、marketplace 描述、后续步骤 |

流程中强调：**在关键节点等待用户确认**（目的确认、组件计划确认、详细设计确认、校验后是否修复、测试方式选择）；**按需加载 Skill**（plugin-structure、command-development、agent-development、hook-development、mcp-integration、plugin-settings 等）；**使用专用 Agent**（agent-creator、plugin-validator、skill-reviewer）辅助生成与校验。

### 2.2 各阶段要点

**Phase 1 – Discovery**  
- 若用户已给出描述，先总结理解并判断插件类型（integration / workflow / analysis / toolkit 等）。  
- 若不清楚，则提问：解决什么问题、谁在什么场景用、希望做什么、有无可参考插件。  
- 输出一句话目的陈述，并获用户确认。

**Phase 2 – Component Planning**  
- **必须先加载 plugin-structure skill**，再根据需求决定：  
  - Skills：是否需要领域知识（如 Hook API、MCP 模式）？  
  - Commands：是否有用户主动触发的动作？  
  - Agents：是否有可独立完成的子任务？  
  - Hooks：是否需要事件驱动（校验、通知等）？  
  - MCP：是否接入外部服务？  
  - Settings：是否需要 per-project 配置（.local.md）？  
- 输出组件计划表（类型、数量、用途），并获用户确认。

**Phase 3 – Detailed Design**  
- 对每种组件列出未决点（Skills 的触发与深度、Commands 的参数与工具、Agents 的触发方式与输出、Hooks 的事件与类型、MCP 的认证与工具、Settings 的字段与默认值）。  
- 以结构化问题形式一次性交给用户，**等用户回答后再进入实现**。  
- 避免跳过此阶段，以减少返工。

**Phase 4 – Plugin Structure Creation**  
- 确定 kebab-case 插件名与创建位置（当前目录 / 上级新目录 / 自定义路径）。  
- 用 Bash 创建目录：`.claude-plugin/`、`commands/`、`agents/`、`skills/`、`hooks/`（按需）。  
- 编写 `plugin.json`（name、version、description、author 等）、README 模板、.gitignore（如 `.claude/*.local.md`）。  
- 可选：在新目录下初始化 git。

**Phase 5 – Component Implementation**  
- **每实现一类组件前，先加载对应 Skill**：  
  - Skills → skill-development；Commands → command-development；Agents → agent-development；Hooks → hook-development；MCP → mcp-integration；Settings → plugin-settings。  
- **Skills**：为每个 skill 建目录与 SKILL.md，正文精简，细节放 references/examples；可用 skill-reviewer 校验。  
- **Commands**：写 .md 与 frontmatter，指令面向 Claude，限制 allowed-tools。  
- **Agents**：用 agent-creator 生成 description + examples + systemPrompt，再写 .md；用 validate-agent.sh 校验。  
- **Hooks**：写 hooks.json，优先 prompt 型复杂逻辑；脚本路径用 `${CLAUDE_PLUGIN_ROOT}`；用 validate-hook-schema.sh、test-hook.sh 校验。  
- **MCP**：写 .mcp.json，注明 env 与启动方式；在 README 中写依赖与配置。  
- **Settings**：按 plugin-settings 模式做 .local.md 模板与解析方式，并加入 .gitignore。  
- 用 TodoWrite 跟踪进度。

**Phase 6 – Validation & Quality Check**  
- 使用 **plugin-validator** 做整体校验（清单、结构、命名、安全等）。  
- 修复所有 critical 问题；酌情处理 warning。  
- 若有 Skills，用 **skill-reviewer** 检查描述与渐进式披露。  
- 若有 Agents，检查 `<example>` 与触发条件，并跑 validate-agent.sh。  
- 若有 Hooks，跑 validate-hook-schema.sh、test-hook.sh，确认 `${CLAUDE_PLUGIN_ROOT}` 使用正确。  
- 向用户汇报结果并询问：先修问题还是直接进入测试。

**Phase 7 – Testing & Verification**  
- 给出本地安装方式，例如：`cc --plugin-dir /path/to/plugin-name` 或复制到 `.claude-plugin/`。  
- 提供自测清单：Skills 是否在触发短语下加载、Commands 是否出现在 `/help` 且可执行、Agents 是否在预期场景触发、Hooks 是否在对应事件触发、MCP 是否连接、Settings 是否生效。  
- 建议用 `claude --debug` 观察 Hook 执行；用 `/mcp` 检查 MCP 与工具。  
- 询问用户是否需要逐步带着测，还是自行测试。

**Phase 8 – Documentation & Next Steps**  
- 检查 README：概述、功能、安装、前置条件、用法；MCP 需写环境变量；Hooks 需写触发条件；Settings 需写配置方式。  
- 若计划发布：指导填写 marketplace 描述与分类。  
- 输出总结：插件名、目的、已创建组件与文件数、关键文件说明、后续建议（迭代、发布、测试策略）。

### 2.3 开发时的技能与工具

- **按阶段加载的 Skill**：  
  - Phase 2：plugin-structure  
  - Phase 5：skill-development、command-development、agent-development、hook-development、mcp-integration、plugin-settings（按需）  
- **专用 Agent**：agent-creator（生成 agent 描述与 prompt）、plugin-validator（整体校验）、skill-reviewer（技能质量）。  
- **校验脚本**（多位于 plugin-dev 的 skills 下）：  
  - validate-agent.sh：校验 agent .md  
  - validate-hook-schema.sh：校验 hooks.json  
  - test-hook.sh：用样例输入测 hook  
  - parse-frontmatter.sh、validate-settings.sh：Settings 相关  

### 2.4 原则与注意事项

- **命令是给 Claude 的**：Command 正文是“去做某事”的指令，不是“将要对用户做某事”的说明。  
- **Skill 渐进式披露**：核心 SKILL.md 精简，细节进 references/examples，避免一次性加载过长。  
- **路径可移植**：所有脚本与配置中的路径使用 `${CLAUDE_PLUGIN_ROOT}`，禁止写死绝对路径。  
- **安全**：Hook 中做输入校验；MCP 用 HTTPS/WSS；凭证用环境变量。  
- **命名**：kebab-case；agent/command 名称清晰、可区分。  
- **质量门**：每个组件符合 plugin-dev 的既有模式，并通过对应校验脚本与人工抽查。

---

## 3. 参考来源

- 插件结构约定与清单：`plugins/plugin-dev/skills/plugin-structure/SKILL.md`、`references/manifest-reference.md`、`references/component-patterns.md`  
- 插件创建流程：`plugins/plugin-dev/commands/create-plugin.md`  
- plugin-dev 总览与 7 大 Skill：`plugins/plugin-dev/README.md`  
- 命令开发：`plugins/plugin-dev/skills/command-development/SKILL.md`  
- Agent 开发：`plugins/plugin-dev/skills/agent-development/SKILL.md`  
- Hook 开发：`plugins/plugin-dev/skills/hook-development/SKILL.md`  
- 官方插件列表与结构说明：`plugins/README.md`  

---

## 4. 与 MW4Agent 的对照（简要）

| 维度 | Claude Code 插件 | MW4Agent |
|------|------------------|----------|
| 扩展单元 | 插件（含 commands/agents/skills/hooks/MCP） | 工具注册表、技能、通道、LLM 配置 |
| 清单 | .claude-plugin/plugin.json | 无统一插件清单；工具/技能在代码或配置中注册 |
| 命令 | 斜杠命令 .md，内容即给 Agent 的指令 | 无斜杠命令；由 Gateway RPC / 通道消息驱动 |
| 子 Agent | agents/*.md，按 description+example 触发 | 单 Runner，可扩展多 Agent 编排 |
| 技能 | skills/*/SKILL.md，按描述自动加载 | 技能提示词或外部技能描述，由 Runner 注入 |
| 钩子 | 多阶段事件 + matcher + command/prompt | 无等价钩子；可考虑在 Runner/工具执行前后加中间件 |
| 开发流程 | 8 阶段 + plugin-dev Skills/Agents/脚本 | 代码直接扩展 + 文档（如 docs/manuals/cli.md） |

Claude Code 的插件架构与 8 阶段开发流程，对 MW4Agent 设计“可插拔技能/工具/策略”和“从需求到上线的开发规范”具有参考价值；可根据 MW4Agent 的网关与多通道场景做裁剪与映射。
