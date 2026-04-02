# Claude Code 记忆系统（产品行为归纳）

本文档归纳 **Claude Code** 在「跨会话持久上下文」上的设计，供 MW4Agent 借鉴。说明如下边界：

- **GitHub 上的 [anthropics/claude-code](https://github.com/anthropics/claude-code)** 以 **插件与文档** 为主，**不含** 完整 CLI 源码树（见 [`docs/claude-code/agent.md`](agent.md)）。
- **完整实现**可在 **Claude Code 产品源码树**（例如本地目录 `ClaudeCode/`，含 `memdir/`、`utils/claudemd.ts`、`main.tsx` 等）中阅读；下文 **§源码级实现** 摘录自该类树中的模块，路径均相对于 **该源码仓库根目录**。
- 用户可见行为仍以 **官方文档** [How Claude remembers your project](https://code.claude.com/docs/en/memory) 为准；源码与文档冲突时以 **当前检出的源码** 为工程真值。

---

## 两类「Claude Code」仓库：插件-only 与完整源码

### A. GitHub `anthropics/claude-code`（插件与市场）

| 内容 | 说明 |
|------|------|
| `CHANGELOG.md` | 产品变更（与发行版二进制一致时可对照）。 |
| `plugins/**` | 官方插件；`plugins/plugin-dev` 下有 Hook 技能与 `SessionStart` / `PreCompact` 等文档。 |
| 无 `memdir/`、`utils/claudemd.ts` | **不能**在此树中阅读 MEMORY.md 截断或 CLAUDE.md 解析实现。 |

### B. 完整 CLI 源码树（本地如 `ClaudeCode/`）

与记忆/指令直接相关的顶层目录/文件包括：`memdir/`（自动记忆入口、路径、类型与扫描）、`utils/claudemd.ts`（CLAUDE.md / rules 的发现、`@include`、与 MEMORY 注入衔接）、`utils/memoryFileDetection.ts`、`utils/hooks.ts`（`InstructionsLoaded`）、`utils/permissions/yoloClassifier.ts`（权限分类器中的 CLAUDE.md 包装）等。

---

## 源码级实现（摘自 Claude Code 源码树）

### `utils/claudemd.ts`：指令加载顺序与 `@include`

文件头注释约定（实现与之一致）：

1. **Managed**：如 `/etc/claude-code/CLAUDE.md`（全局托管）。
2. **User**：`~/.claude/CLAUDE.md`。
3. **Project**：自 CWD **向上** 遍历，每层可含 `CLAUDE.md`、`.claude/CLAUDE.md`、`.claude/rules/*.md`。
4. **Local**：各项目根下的 `CLAUDE.local.md`。

**优先级**：注释写明按 **与模型注意力相反的顺序** 加载——**越靠近当前目录、越晚载入的文件优先级越高**（「latest files are highest priority」）。被 `@include` 的文件作为 **独立条目** 插在包含文件 **之前**；**循环引用**用已处理集合防护；**最大深度** `MAX_INCLUDE_DEPTH = 5`（与官方文档「约 5 层」一致）。`@` 语法支持 `@path`、`@./relative`、`@~/...`、`@/absolute`；仅在 **Markdown 叶子文本节点** 展开（**代码块内不展开**）。

常量 **`MAX_MEMORY_CHARACTER_COUNT = 40000`**：作为「单份记忆文件」的推荐/告警上限（如 doctor 大文件提示），与下文 **`MEMORY.md` 开局截断**（200 行 + 25KB）是 **不同机制**。

### `memdir/memdir.ts`：`MEMORY.md` 开局截断

- 入口文件名：`ENTRYPOINT_NAME = 'MEMORY.md'`。
- **行上限** `MAX_ENTRYPOINT_LINES = 200`；**字节上限** `MAX_ENTRYPOINT_BYTES = 25_000`。
- `truncateEntrypointContent`：**先按行截断**，再按字节截断；字节截断在 **上一处换行符** 处截断，避免半行；超限则追加 **WARNING** 说明原因（并提示索引行尽量 **~200 字符以内**、细节放到主题文件）。

### `memdir/paths.ts`：是否启用自动记忆与目录解析

**`isAutoMemoryEnabled()`** 判定顺序（注释摘要）：

1. `CLAUDE_CODE_DISABLE_AUTO_MEMORY` 为真 → 关。
2. 显式假 → 开。
3. `CLAUDE_CODE_SIMPLE`（`--bare`）→ 关（与「精简系统提示、去掉 memory 段」一致）。
4. **CCR 远程**且无 `CLAUDE_CODE_REMOTE_MEMORY_DIR` → 关（无持久存储则不启用）。
5. `settings.autoMemoryEnabled`（含项目级 opt-out）。
6. 默认 **开**。

**`getAutoMemPath()`**（自动记忆根目录，注释与实现）：

1. **`CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`**：整目录覆盖（Cowork 等场景）。
2. **`autoMemoryDirectory`**：仅来自 **policy / flag / local / user** 等 **可信 settings 源**；**故意不包含 `projectSettings`（`.claude/settings.json` 提交进仓库的那份）**，防止恶意仓库把记忆目录指到 `~/.ssh` 等敏感路径并利用写入白名单。
3. 默认：`getMemoryBaseDir()`（`CLAUDE_CODE_REMOTE_MEMORY_DIR` 或 `~/.claude`）下的 `projects/<sanitize(规范 git 根)>/memory/`；规范根由 **`findCanonicalGitRoot`** 得到，**同一仓库多 worktree 共用** 同一记忆目录。

### `memdir/memoryTypes.ts`：记忆类型（写入侧约束）

自动记忆提示中定义 **`MEMORY_TYPES`**：`user`、`feedback`、`project`、`reference`。注释说明：应保存 **无法从当前项目状态直接推出** 的上下文；代码模式、架构、git、目录结构等 **应通过 grep/git/CLAUDE.md 获得，不应写成 memory**。

### `memdir/memoryScan.ts`：主题 `.md` 文件列表上限

扫描记忆目录下除 `MEMORY.md` 外的 `.md` 主题文件时，**最多保留 200 个**（`MAX_MEMORY_FILES`），按 **mtime** 新近优先（供上下文头部列表等用途）。

### `utils/memoryFileDetection.ts`：路径归类

自动记忆路径检测时 **排除** 用户托管指令：`CLAUDE.md`、`CLAUDE.local.md`、`.claude/rules/`（与「自动记忆」分离）。

### `utils/hooks.ts`：`InstructionsLoaded`

与 **CHANGELOG** 一致：在 **CLAUDE.md 或 rule 文件** 被加载进上下文时执行 `executeInstructionsLoadedHooks`；支持 **懒加载**（触及某文件后触发嵌套 CLAUDE.md/rules 时再加载）。

### GitHub 插件仓库仍可核对的内容

| 类别 | 路径（相对 `anthropics/claude-code` 根） | 说明 |
|------|------------------------------------------|------|
| 变更记录 | `CHANGELOG.md` | mtime、HTML 注释、`PostCompact`、`InstructionsLoaded`、worktree 重复修复等。 |
| 插件 | `plugins/README.md`、`plugins/plugin-dev/skills/hook-development/` | SessionStart、`PreCompact` 模式、示例脚本。 |
| Agent 约定 | `plugins/pr-review-toolkit/agents/*.md` 等 | 将 **CLAUDE.md** 作为项目规范真源。 |

### CHANGELOG 中可对照的要点（节选）

- **自动记忆**：记忆文件 **mtime**；**`autoMemoryDirectory`**；**`/context`** 诊断建议。
- **CLAUDE.md**：`<!-- ... -->` 在 **自动注入** 时隐藏。
- **压缩**：**`PostCompact`**；**`InstructionsLoaded`** 与懒加载。
- **LSP**：`memory context` 构建 **不全文读文件**。
- **插件文档**：`validate-hook-schema.sh` 等列出的 Hook 事件可能 **略旧于** CHANGELOG（新事件以 CHANGELOG / 源码为准）。

---

## 1. 总体模型：两类互补记忆

每次会话开始时上下文窗口是「干净」的；**跨会话延续**主要靠两条线：

| 维度 | CLAUDE.md（及规则） | Auto memory |
|------|---------------------|-------------|
| **谁维护** | 用户/团队（手写或 `/init` 辅助） | Claude 在对话中自动写入 |
| **内容** | 指令、规范、工作流、架构约定 | 从纠错与协作中沉淀的「学习」：构建命令、调试心得、偏好等 |
| **作用** | **指导行为**（仍属上下文，非硬编码策略） | **补充事实与习惯**，减少重复说明 |
| **典型范围** | 项目 / 用户目录 / 组织托管策略 | **按工作区（通常与 git 仓库绑定）** |

二者都会在会话早期进入上下文；官方强调：**写得越具体、越短，遵从度越好**。

---

## 2. CLAUDE.md 体系

### 2.1 文件是什么

- 普通 Markdown，由用户在固定路径放置，**每次会话开始读取**。
- 与「自动记忆」区分：这是 **人写的持久指令**，不是模型自己记的笔记。

### 2.2 常见位置与优先级（由近到远、由窄到宽）

官方文档列出的思路包括（不完全列举）：

- **项目**：`./CLAUDE.md` 或 `./.claude/CLAUDE.md`（可版本化，团队共享）。
- **用户全局**：`~/.claude/CLAUDE.md`（个人偏好）。
- **组织托管**：如 macOS `/Library/Application Support/ClaudeCode/CLAUDE.md`、Linux `/etc/claude-code/CLAUDE.md`、Windows `Program Files` 下路径等——用于 IT 统一下发的策略说明。

**加载逻辑（概念）**：与源码 `utils/claudemd.ts` 头注释一致——自 CWD **向上** 遍历；每层检查 `CLAUDE.md`、`.claude/CLAUDE.md`、`.claude/rules/*.md`；**离当前目录越近的层，指令优先级越高**。子目录内嵌套的 CLAUDE.md/rules 还可 **懒加载**（见 `InstructionsLoaded` / hooks 注释）。

### 2.3 与 AGENTS.md 的关系

- Claude Code **默认读的是 `CLAUDE.md`**，不是 `AGENTS.md`。
- 若仓库已有 `AGENTS.md`（给其他 Agent 用），官方建议：在 `CLAUDE.md` 里用 **`@`** 将其 **include** 进来，避免两份真源（源码中称为 **Memory `@include`** 机制，语法见 `utils/claudemd.ts`）。

### 2.4 `@include` 其它文件

- 语法：`@path`、`@./relative`、`@~/...`、`@/absolute`；无前缀的 `@path` 视为相对路径。
- **被包含文件**作为 **单独条目** 出现在 **包含文件之前**；**递归深度上限 5**（`MAX_INCLUDE_DEPTH`，与官方文档一致）。
- **仅在叶子文本节点** 展开；**代码块 / 代码串内不展开**；循环引用会被跳过；缺失文件 **静默忽略**。

### 2.5 `.claude/rules/`：模块化与路径作用域

- 在 `.claude/rules/` 下放多个 `.md`，**一主题一文件**，可分子目录。
- 无 frontmatter 的规则：与会话启动时加载的主说明类似优先级。
- **路径作用域**：可在 YAML frontmatter 里用 `paths`（glob）声明 **仅当处理匹配文件时** 再注入，减少全局上下文膨胀。
- **用户级** `~/.claude/rules/`：全项目通用；与项目规则并存时，**项目规则优先**（文档描述为 project 覆盖 user）。

### 2.6 大文件与维护

- 建议单文件 **目标控制在约 200 行以内**；过长会占上下文且降低「被遵守」的稳定性。
- 超大说明应拆成 **`@include`** 或拆成 `rules` 多文件。
- 源码中对「单份记忆类文件」另有 **约 40000 字符** 的推荐上限常量（`MAX_MEMORY_CHARACTER_COUNT`，用于告警/医生提示），与 **`MEMORY.md` 开局 200 行/25KB** 不是同一道截断逻辑。
- HTML 块注释 `<!-- ... -->` 在注入前会被剥掉，便于人类备注又不耗 token（代码块内注释保留）。

### 2.7 单仓排除

- 设置 **`claudeMdExcludes`**（glob，对绝对路径匹配）可跳过无关祖先目录下的 `CLAUDE.md`/rules（单仓多团队场景）。
- **组织托管的 CLAUDE.md** 文档说明为 **不可被排除**，保证策略始终生效。

### 2.8 与「系统提示」的关系（调试向）

- 官方说明：CLAUDE.md 内容是以 **用户消息等形式进入对话语境**，**不是**模型厂商固定 system prompt 本身；若需要「每次必在 system 层」，需 CLI 的 **`--append-system-prompt`** 等（更适合脚本/自动化）。
- **`InstructionsLoaded` Hook**（见 `CHANGELOG.md` 版本 **2.1.69** 与 `utils/hooks.ts`）：在 **CLAUDE.md** 或 **`.claude/rules/*.md` 被加载进上下文时** 触发，用于观测「实际注入了哪些指令」、排查路径规则与懒加载；**不等价于**自动记忆文件的写入事件。
- **YOLO 分类器**路径（`utils/permissions/yoloClassifier.ts`）：会把缓存的 CLAUDE.md 正文包在 **`<user_claude_md>`** 标签内注入分类请求，并注明其为 **用户配置**；若未取到用户上下文则与主会话一致 **不带 CLAUDE.md**。

---

## 3. Auto memory（自动记忆）

### 3.1 定位

- 让 Claude **在协作过程中自己写笔记**，跨会话复用：构建命令、踩坑记录、架构片段、风格偏好等。
- **不是每轮都写**；由模型判断「是否值得留给未来」。

### 3.2 版本与开关

- 文档要求：**Claude Code v2.1.59+** 才具备 auto memory（以 `claude --version` 为准）。
- 默认开启；可通过 **`/memory` 界面**、设置项 **`autoMemoryEnabled`**、环境变量 **`CLAUDE_CODE_DISABLE_AUTO_MEMORY`** 关闭。
- 源码 **`memdir/paths.ts`** 中另有：**`CLAUDE_CODE_SIMPLE`（`--bare`）** 会关闭 auto memory 相关能力；**远程 CCR** 若未设置 **`CLAUDE_CODE_REMOTE_MEMORY_DIR`** 也会关闭（无持久目录则不做自动记忆）。

### 3.3 存储位置与共享范围

- 默认在 **`~/.claude/projects/<规范 git 根路径的 sanitize 串>/memory/`**（`getAutoMemPath()`）；**同一仓库多 worktree 共用** 同一目录（`findCanonicalGitRoot`）。
- **`autoMemoryDirectory`**：仅 **policy / flag / local / user** 等可信源；**不包含提交进仓库的 `projectSettings`**，理由见源码注释（防止指向 `~/.ssh` 等并利用写入白名单）。
- **`CLAUDE_COWORK_MEMORY_PATH_OVERRIDE`**：环境变量 **整目录覆盖**（Cowork 等）；与 **`hasAutoMemPathOverride()`** 协作决定是否注入记忆提示等。

### 3.4 文件形态

典型布局：

```text
~/.claude/projects/<project>/memory/
├── MEMORY.md       # 索引入口，会话开始会读其中一段
├── debugging.md    # 示例：主题拆文件
└── ...
```

- **`MEMORY.md`**：作为目录的 **索引与摘要**；Claude 会话中会持续读写该目录。
- **主题文件**：细节可放独立 md，**默认不在会话开头整包加载**，需要时用常规读文件工具按需打开。

### 3.5 启动时加载限额（与 CLAUDE.md 不同）

- 与 **`memdir/memdir.ts`** 中 **`truncateEntrypointContent`** 一致：**先按 200 行**，再按 **25_000 字节**；字节截断在换行处切分；超限追加 **WARNING**。
- **CLAUDE.md** 使用另一套「大文件」阈值（如 **40000 字符** 告警），**不是** MEMORY 入口的 200/25K 逻辑。

### 3.5.1 主题文件与扫描上限

- 除 `MEMORY.md` 外的 `.md` 主题文件在扫描列表中 **最多 200 个**（`memdir/memoryScan.ts` 的 `MAX_MEMORY_FILES`），按 **mtime** 新近优先。

### 3.6 审计与编辑

- 会话内 **`/memory`**：可浏览已加载的 CLAUDE.md/rules、开关 auto memory、打开记忆目录、在编辑器中打开文件。
- 记忆文件为 **纯 Markdown**，人可直接删改。

### 3.7 子 Agent

- 官方文档指向 **Subagent** 可维护 **独立 auto memory**（详见 [Subagent 与 persistent memory](https://code.claude.com/en/sub-agents#enable-persistent-memory)）。

### 3.8 变更日志中的产品细节（辅助理解）

与 auto memory、上下文诊断相关的 **CHANGELOG** 条目已集中在 **§「两类仓库」** 末尾；完整历史见 [`CHANGELOG.md`](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)。**实现级细节**以 **§源码级实现** 为准。

### 3.9（可选）Kairos 模式下的按日日志

- 源码 **`getAutoMemDailyLogPath`**（`memdir/paths.ts`）：可在 `memory/logs/YYYY/MM/YYYY-MM-DD.md` 形态 **按日追加**，与「先写日志再由夜间流程蒸馏到 `MEMORY.md`」的注释描述一致（特性门控，见 `feature('KAIROS')` 相关代码路径）。

---

## 4. 与「会话内上下文」的边界

- **Compact（`/compact`）**：官方说明 **CLAUDE.md 在压缩后会从磁盘重新注入**；若某条要求只出现在聊天里而未写入 `CLAUDE.md`，压缩后可能「像丢失」——应写入文件才能跨压缩/跨会话稳定存在。

---

## 5. 对 MW4Agent 的可借鉴点（抽象）

1. **双源**：**人写规范**（CLAUDE.md / rules）与 **机写沉淀**（MEMORY.md 索引 + 主题文件）分离职责。
2. **加载分层**：全局/祖先/按需子目录；规则可按 **路径 glob** 懒加载。
3. **硬限额**：对「自动记忆索引」类文件使用 **行数/字节上限**，强迫索引短、细节外置。
4. **可审计**：记忆落盘为明文 Markdown，支持 `/memory` 类入口编辑与关闭。
5. **安全与配置分层**：敏感重定向（如记忆目录）限制设置来源，避免项目级劫持。
6. **与指令注入方式区分**：「进上下文」不等于「等同 system prompt」——需要严格保证时走单独机制（CLI flag / 网关策略等）。
7. **扩展钩子**：**SessionStart** 注入会话级变量或摘要、**PreCompact/PostCompact** 围绕「压缩」前后做保留或收尾、**InstructionsLoaded** 对齐「哪些规范文件已进入模型上下文」——本地 `plugin-dev` 技能与 `CHANGELOG` 给出了 **可组合** 的工程模式（MW4Agent 可用网关事件或 hook 类比）。
8. **生态约定**：官方插件里的 Agent 提示词普遍假设存在 **`CLAUDE.md` 级真源**；若 MW4Agent 暴露「项目规范」文件，命名与导入方式应 **一眼可迁移**（例如 `@` 导入或等价机制）。

---

## 6. 参考链接

- [How Claude remembers your project（Memory 官方文档）](https://code.claude.com/docs/en/memory)
- [Documentation index（llms.txt）](https://code.claude.com/docs/llms.txt)
- [Skills](https://code.claude.com/en/skills)
- [Settings](https://code.claude.com/en/settings)
- [Manage sessions](https://code.claude.com/en/sessions)
- [Subagents · persistent memory](https://code.claude.com/en/sub-agents#enable-persistent-memory)
