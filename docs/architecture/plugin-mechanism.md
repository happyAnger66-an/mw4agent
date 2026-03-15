# MW4Agent 插件机制设计与实现方案

本文档描述在 MW4Agent 中实现**类似 Claude Code 的插件机制**的设计与分阶段实现思路，用于扩展 Agent 能力（工具、技能、可选钩子），同时与现有 ToolRegistry、SkillManager、Runner 对接。

---

## 1. 目标与范围

**目标**

- 支持以「插件」为单位扩展 Agent：一个插件可同时提供 **Tools**、**Skills**（提示/知识）、以及可选的 **Hooks**（工具调用前后、会话生命周期）。
- 插件**发现**通过目录扫描或配置指定，清单驱动（manifest），与 Claude Code 的 .claude-plugin/plugin.json 思路对齐，便于后续兼容或迁移。
- 不改变现有内置工具、技能、Runner 的对外接口；插件在启动时或首次使用时**注册**到现有 Registry / Manager。

**非目标（首版）**

- 不实现「斜杠命令」（MW4Agent 无终端交互）；若需要可后续通过 Gateway RPC 或通道语义映射。
- 不实现 MCP 协议集成；插件内工具以 Python 类注册到 ToolRegistry 为主。
- 不做进程级沙箱；插件代码与主进程同进程运行，视为受信扩展。

---

## 2. 现状与扩展点

| 扩展点 | 现状 | 插件可接入方式 |
|--------|------|----------------|
| **Tools** | ToolRegistry 单例，builtin 在 `agents/tools/__init__.py` 里 register | 插件在加载时调用 `get_tool_registry().register(tool)` |
| **Skills** | SkillManager 从 `~/.mw4agent/skills/` 读 JSON/MD，`build_skill_snapshot()` 拼 prompt | 插件提供额外 skills 目录或通过「插件 skill 源」注入到 snapshot |
| **Runner** | 无钩子；直接取 registry 与 skill snapshot | 可选：Runner 支持 pre_tool/post_tool/session_start 等钩子，插件注册回调 |
| **CLI** | CommandEntry + register_commands | 可选：插件通过 entry_points 或 manifest 声明 CLI 子命令 |

首版优先实现 **Tools + Skills** 的插件扩展；Hooks 与 CLI 作为第二阶段。

---

## 3. 插件形态与清单

**插件根目录**：一个目录，内含清单文件，例如：

```
my-plugin/
├── plugin.json          # 或 plugin.yaml
├── tools.py             # 可选：定义并注册工具
├── skills/              # 可选：插件自带技能（SKILL.md 或 .md/.json）
│   └── my-skill/
│       └── SKILL.md
└── README.md
```

**清单格式（plugin.json）**

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "description": "Short description",
  "tools_module": "tools",
  "skills_dir": "skills"
}
```

- **name**（必填）：kebab-case，唯一，用于日志与冲突检测。
- **version**：语义化版本，便于排查与文档。
- **description**：简短说明。
- **tools_module**：相对插件根的 Python 模块名（如 `tools` 表示 `my-plugin/tools.py` 或 `my-plugin/tools/__init__.py`），该模块需提供 `register_tools(registry)` 或 `register(registry)`，由加载器调用并传入 `get_tool_registry()`。
- **skills_dir**：相对插件根的目录，其下为与现有 SkillManager 兼容的 skill 结构（`<name>.json`、`<name>.md` 或 `<name>/SKILL.md`）；加载时将该目录加入「插件技能源」列表，build_skill_snapshot 时合并。

可选字段（后续可扩展）：

- **hooks_module**：实现 `register_hooks(hook_registry)` 的模块（第二阶段）。
- **enabled**：是否启用（可由配置覆盖）。

**路径与可移植性**

- 清单与 `tools_module`、`skills_dir` 均为相对**插件根目录**。
- 加载时会将插件根目录加入 `sys.path` 或使用 `importlib.util.spec_from_file_location` 加载 `tools_module`，并在调用 `register_tools(registry)` 时注入 `plugin_root`（环境变量或参数），便于插件内脚本、资源使用绝对路径（如 `${PLUGIN_ROOT}` 或 `os.environ.get("MW4AGENT_PLUGIN_ROOT")`）。

---

## 4. 发现与加载

**发现**

- **插件目录**：通过环境变量 `MW4AGENT_PLUGIN_DIR`（多个路径用 `:` 或 `,` 分隔）或配置文件（如 `~/.mw4agent/config.json` 中 `plugin_dirs: []`）指定。
- 每个指定目录下：
  - 若该目录本身包含 `plugin.json`，则视为**单个插件根**；
  - 否则扫描其子目录，每个包含 `plugin.json` 的子目录视为一个插件根。
- 发现结果：`List[Tuple[Path, Dict]]`（插件根路径 + 解析后的清单）。

**加载顺序**

1. 解析所有插件清单，按 name 去重（后加载覆盖先加载或报错，可配置）。
2. 按顺序对每个插件：
   - 若存在 `tools_module`：动态加载该模块并调用 `register_tools(get_tool_registry())`（或 `register(registry)`），传入 `plugin_root`。
   - 若存在 `skills_dir`：将 `plugin_root / skills_dir` 加入「插件技能目录」列表。
3. 内置工具仍由 `agents/tools/__init__.py` 先注册；插件在之后执行，因此若插件工具与内置同名，可配置为覆盖或报错（推荐报错，避免静默覆盖）。

**触发时机**

- **方案 A（推荐）**：在 Gateway 或 Runner 进程启动时（如 `create_app()` 或 `AgentRunner` 首次使用前）调用一次 `load_plugins()`，之后不再扫描。简单、可预测。
- **方案 B**：懒加载——首次需要 tool_registry / skill_snapshot 时再加载插件；需注意线程/进程安全与重复加载。

---

## 5. Skills 与 build_skill_snapshot 的对接

当前 `build_skill_snapshot()` 仅使用 `get_default_skill_manager().read_all_skills()`，即单一 `skills_dir`。

**方案一：多目录 SkillManager**

- 扩展 `SkillManager`，支持构造时接受 `extra_dirs: List[Path]`；`list_skills()` / `read_all_skills()` 先查主目录，再查 extra_dirs，同名时主目录优先（或插件 skill 带前缀，如 `plugin_name/skill_name`）。
- 插件加载时，将每个插件的 `skills_dir` 加入 SkillManager 的 extra_dirs。需要 SkillManager 在应用启动时已创建并传入 extra_dirs，或在加载插件后能设置 extra_dirs（例如 `set_plugin_skill_dirs(List[Path])`）。

**方案二：独立「插件技能聚合器」**

- 新增 `PluginSkillSource`：维护 `List[Path]`（各插件的 skills_dir），提供 `read_all_skills() -> Dict[str, Dict]`，与 SkillManager 的目录约定一致（`<name>.json`、`<name>.md`、`<name>/SKILL.md`）。
- `build_skill_snapshot()` 改为：先取 `get_default_skill_manager().read_all_skills()`，再取 `get_plugin_skill_source().read_all_skills()`，合并（主 skills 优先或插件带前缀），再生成 prompt。这样不动 SkillManager 内部实现，只扩展 snapshot 的输入源。

**推荐**：方案二，对现有 SkillManager 侵入更小，且插件技能与用户全局技能分离清晰。

---

## 6. 工具注册约定

插件模块需提供可调用的注册函数，例如：

```python
# my_plugin/tools.py
from mw4agent.agents.tools import get_tool_registry
from mw4agent.agents.tools.base import AgentTool, ToolResult

class MyTool(AgentTool):
    def __init__(self):
        super().__init__(name="my_tool", description="...", parameters={...})

    async def execute(self, tool_call_id, params, context=None):
        ...

def register_tools(registry=None):
    if registry is None:
        registry = get_tool_registry()
    registry.register(MyTool())
```

加载器逻辑伪代码：

```python
def _load_plugin_tools(plugin_root: Path, tools_module_name: str) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        tools_module_name,
        plugin_root / f"{tools_module_name}.py",
        submodule_search_locations=[str(plugin_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    registry = get_tool_registry()
    if hasattr(mod, "register_tools"):
        mod.register_tools(registry)
    elif hasattr(mod, "register"):
        mod.register(registry)
```

---

## 7. 可选：Hooks（第二阶段）

若需「工具调用前/后」「会话开始/结束」等能力，可引入轻量钩子：

- **Hook 类型**：`pre_tool`、`post_tool`、`session_start`、`session_end`（与 Claude Code 对齐部分语义）。
- **注册**：插件 manifest 中 `hooks_module`，模块提供 `register_hooks(registry: HookRegistry)`；HookRegistry 提供 `on_pre_tool(callable)` 等。
- **Runner 对接**：在 `execute_tool` 前调用 `hook_registry.emit("pre_tool", tool_name, params, context)`；在 `run()` 开始/结束时调用 `session_start` / `session_end`。钩子为同步或 async，按顺序执行；若返回 False 或抛出，可中止本次工具调用或会话（策略可配置）。

首版可不实现 Hooks，仅在设计中预留 hooks_module 与 HookRegistry 接口。

---

## 8. 配置与安全

- **启用/禁用**：通过配置或环境变量（如 `MW4AGENT_PLUGINS_ENABLED=my-plugin,other`）限制只加载列出的插件；未列出的不加载。
- **冲突**：工具名与已有（含内置）冲突时，建议报错并拒绝加载该插件，避免静默覆盖。
- **权限**：插件与主进程同进程，具备相同权限；文档中明确「插件为受信扩展，请仅加载可信来源」。
- **依赖**：插件若依赖第三方库，需在插件目录内注明（如 requirements.txt），由部署方自行安装；或支持在 manifest 中声明 `dependencies: []`，由 CLI 提示安装（后续增强）。

---

## 9. 实现步骤建议

**阶段 1：发现 + 清单 + Tools**（已实现）

1. 新增 `mw4agent/plugin/` 包：  
   - `loader.py`：扫描 `MW4AGENT_PLUGIN_DIR`（环境变量，多路径用 `:` 或 `,` 分隔）或显式传入 `plugin_dirs`，解析 `plugin.json`，返回 `List[PluginInfo]`。  
   - 对每个插件，若存在 `tools_module`，使用 `importlib.util.spec_from_file_location` 动态加载并调用 `register_tools(registry)` 或 `register(registry)`；调用前设置 `MW4AGENT_PLUGIN_ROOT` 环境变量。  
2. 在 Gateway `create_app()` 开头调用 `load_plugins()`，仅做工具注册。  
3. 单元测试：`tests/test_plugin_loader.py`；最小示例插件：`tests/fixtures/plugins/echo_plugin/`（含 `plugin.json` 与 `tools.py` 注册 `echo` 工具）。

**阶段 2：Skills 合并**（已实现）

4. 新增 `PluginSkillSource`（`mw4agent/plugin/loader.py`）：维护 `_dirs: List[Path]`，提供 `add_dir(path)`、`read_all_skills()`；单目录内与 SkillManager 相同约定（`*.json`、`*.md`、`<name>/SKILL.md`），读取为明文 JSON 或 `parse_skill_markdown`。  
5. `load_plugins()` 中：对每个带 `skills_dir` 的插件调用 `get_plugin_skill_source().add_dir(plugin.root / skills_dir)`。  
6. 修改 `build_skill_snapshot()`：先取默认 SkillManager 的 `read_all_skills()`，再取 `get_plugin_skill_source().read_all_skills()`，合并时**主 skills 优先**（同名保留默认），再生成 prompt。  
7. 测试：`tests/fixtures/plugins/skill_plugin/`（含 `skills_dir: "skills"`、`skills/hello/SKILL.md`）；`test_plugin_skill_source_read_all_skills`、`test_load_plugins_adds_skills_dir`、`test_build_skill_snapshot_merges_plugin_skills`。

**阶段 3：配置与文档**（已实现）

7. 在根配置 `~/.mw4agent/mw4agent.json` 的 **plugins** 段中支持 `plugin_dirs`（路径列表）、`plugins_enabled`（可选，插件名白名单）。发现时：未设置 `MW4AGENT_PLUGIN_DIR` 则使用配置中的 `plugin_dirs`；加载时若配置了 `plugins_enabled` 则仅加载名单内插件。  
8. 文档：**[docs/architecture/plugins.md](plugins.md)** 说明插件目录结构、清单字段、`register_tools` 约定、skills_dir 约定、配置项及安全注意点；[docs/manuals/cli.md](../manuals/cli.md) 中增加「插件」小节并链接至 plugins.md。

**阶段 4（可选）：Hooks**

9. 定义 `HookRegistry` 与事件名；Runner 在 `execute_tool` 前后及 run 起止调用钩子。  
10. 插件 manifest 支持 `hooks_module`，加载时调用 `register_hooks(hook_registry)`。

---

## 10. 示例：最小插件

**目录**

```
my-agent-tools/
├── plugin.json
└── tools.py
```

**plugin.json**

```json
{
  "name": "my-agent-tools",
  "version": "0.1.0",
  "description": "Example tools for MW4Agent",
  "tools_module": "tools"
}
```

**tools.py**

```python
from mw4agent.agents.tools import get_tool_registry
from mw4agent.agents.tools.base import AgentTool, ToolResult

class EchoTool(AgentTool):
    def __init__(self):
        super().__init__(
            name="echo",
            description="Echo back the message.",
            parameters={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        )

    async def execute(self, tool_call_id, params, context=None):
        msg = (params or {}).get("message", "")
        return ToolResult(success=True, result={"echo": msg})

def register_tools(registry=None):
    (registry or get_tool_registry()).register(EchoTool())
```

**使用**

- 设置 `MW4AGENT_PLUGIN_DIR=/path/to/my-agent-tools`（或将该目录放到一个扫描目录下）。  
- 启动 Gateway；Runner 调用 `get_tool_definitions()` 时会包含 `echo`，LLM 可发起对 `echo` 的调用。

---

## 11. 与 Claude Code 的对应关系

| Claude Code 插件 | MW4Agent 插件（本方案） |
|-----------------|-------------------------|
| .claude-plugin/plugin.json | plugin.json（插件根目录） |
| commands/ | 暂无；可后续用 RPC/通道语义映射 |
| agents/ | 暂无；可视为多配置/多 Runner 的扩展点 |
| skills/*/SKILL.md | skills_dir 下相同约定 |
| hooks/hooks.json | 预留 hooks_module（第二阶段） |
| MCP | 未纳入；工具以 Python 类注册为主 |
| ${CLAUDE_PLUGIN_ROOT} | 通过参数或 MW4AGENT_PLUGIN_ROOT 传入插件根 |

本方案实现后，MW4Agent 即可通过「插件目录 + 清单 + tools_module + skills_dir」扩展 Agent 的工具与技能，并在后续按需增加 Hooks 与配置策略，逐步逼近 Claude Code 的插件能力子集。
