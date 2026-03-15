# MW4Agent 插件使用与配置

本文说明如何通过**插件**扩展 Agent 的工具与技能：插件目录结构、清单格式、配置项及安全注意点。设计与实现细节见 [插件机制设计](./plugin-mechanism.md)。

---

## 1. 插件目录结构

一个插件是一个目录，内含清单文件 `plugin.json`，并可选的工具模块、技能目录：

```
my-plugin/
├── plugin.json          # 必需：插件清单
├── tools.py             # 可选：注册工具（tools_module）
├── skills/              # 可选：插件技能（skills_dir）
│   └── my-skill/
│       └── SKILL.md
└── README.md
```

- **plugin.json**：必填，见下节。
- **tools_module**：对应 `tools.py` 或 `tools/__init__.py`，需提供 `register_tools(registry)` 或 `register(registry)`。
- **skills_dir**：目录内约定与全局技能一致：`<name>.json`、`<name>.md` 或 `<name>/SKILL.md`；会与默认技能合并进 snapshot，**默认技能同名优先**。

---

## 2. 清单格式（plugin.json）

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "description": "Short description",
  "tools_module": "tools",
  "skills_dir": "skills"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| **name** | 是 | kebab-case，唯一，用于日志与冲突检测 |
| version | 否 | 语义化版本 |
| description | 否 | 简短说明 |
| **tools_module** | 否 | 相对插件根的 Python 模块名，该模块需提供 `register_tools(registry)` 或 `register(registry)` |
| **skills_dir** | 否 | 相对插件根的目录，其下为技能文件（同上约定） |

插件加载时，会设置环境变量 **`MW4AGENT_PLUGIN_ROOT`** 为插件根目录，便于模块内引用资源路径。

---

## 3. 工具注册约定（tools_module）

模块需提供以下之一：

- `def register_tools(registry=None): ...`
- `def register(registry=None): ...`

若 `registry` 为 `None`，应使用 `get_tool_registry()`。示例：

```python
from mw4agent.agents.tools import get_tool_registry
from mw4agent.agents.tools.base import AgentTool, ToolResult

class EchoTool(AgentTool):
    def __init__(self):
        super().__init__(name="echo", description="Echo back the message.", parameters={...})

    async def execute(self, tool_call_id, params, context=None):
        return ToolResult(success=True, result={"echo": (params or {}).get("message", "")})

def register_tools(registry=None):
    (registry or get_tool_registry()).register(EchoTool())
```

工具名与已有（含内置）冲突时会抛出 `ValueError`，该插件加载失败。

---

## 4. 配置项

插件发现与启用由**环境变量**或**根配置**控制。根配置文件路径：**`~/.mw4agent/mw4agent.json`**（可通过环境变量 `MW4AGENT_CONFIG_DIR` 改变目录）。

### 4.1 插件目录（plugin_dirs）

- **环境变量**：`MW4AGENT_PLUGIN_DIR`，多个路径用 `:` 或 `,` 分隔。  
  例：`export MW4AGENT_PLUGIN_DIR=/path/to/plugin1:/path/to/parent_of_plugins`
- **配置文件**：在 `~/.mw4agent/mw4agent.json` 中增加 `plugins` 段，使用 `plugin_dirs` 列表：

```json
{
  "plugins": {
    "plugin_dirs": ["/path/to/plugin1", "/path/to/parent_dir"],
    "plugins_enabled": ["echo-plugin", "skill-plugin"]
  }
}
```

若未设置环境变量，则使用配置中的 `plugin_dirs`。每个路径可以是：**单个插件根**（该目录下直接有 `plugin.json`）或**父目录**（其子目录中包含 `plugin.json` 的会被扫描为多个插件）。

### 4.2 启用过滤（plugins_enabled）

- **配置文件**：`plugins.plugins_enabled` 为字符串数组，表示**仅加载这些名字的插件**（按清单中的 `name`）。  
- 若不配置或为空，则加载所有在 `plugin_dirs` 中发现的插件。

示例：只启用 `echo-plugin` 和 `skill-plugin` 时，如上例在 `plugins` 中设置 `plugins_enabled` 即可。

---

## 5. 使用方式

1. **准备插件**：按上述结构放置 `plugin.json`，可选 `tools.py`、`skills/`。
2. **指定目录**：  
   - 方式一：`export MW4AGENT_PLUGIN_DIR=/path/to/my-plugin`（或多个路径）。  
   - 方式二：在 `~/.mw4agent/mw4agent.json` 的 `plugins.plugin_dirs` 中写入路径。
3. **（可选）限制插件**：在 `plugins.plugins_enabled` 中列出要启用的插件 `name`。
4. **启动 Gateway**：`mw4agent gateway run`；启动时会调用 `load_plugins()`，注册工具并合并插件技能。

Runner 使用的工具列表和技能 snapshot 会包含插件提供的工具与技能（技能合并时默认技能同名优先）。

---

## 6. 安全与注意点

- **受信扩展**：插件代码与主进程同进程运行，无沙箱；仅加载可信来源的插件。
- **冲突**：工具名与已有工具（含内置 read/write）同名会报错并中止该插件加载。
- **配置优先级**：插件目录 = 环境变量优先，缺失时使用配置 `plugin_dirs`；启用列表 = 仅由配置 `plugins_enabled` 控制，无则全部加载。

更多实现细节与阶段规划见 [plugin-mechanism.md](./plugin-mechanism.md)。
