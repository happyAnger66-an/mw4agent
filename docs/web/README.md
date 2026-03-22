# MW4Agent Web 前端说明

本文档总结当前 Dashboard（控制台 SPA）的前端能力与实现方式，便于后续扩展或对接其它 Web 界面。

---

## 1. 多语言（中英文）

### 1.1 行为概览

- **支持语言**：英文（`en`）、简体中文（`zh-CN`）。
- **存储**：当前语言保存在 `localStorage`，键名为 `mw4agent-dashboard-locale`。
- **默认规则**：未保存过时，根据 `navigator.language` 推断（`zh*` → 中文，否则英文）。
- **切换**：页面右上角提供 **中文 / EN** 切换按钮，点击后立即生效并写入 `localStorage`，下次打开保持所选语言。

### 1.2 实现要点

- **模块**：`mw4agent/dashboard/static/i18n.js`。
- **文案表**：`messages.en`、`messages["zh-CN"]`，key 与页面/逻辑中使用的 key 一致（如 `title`、`chat`、`placeholder`、`statusConnected` 等）。
- **接口**：
  - `getLocale()`：当前语言。
  - `setLocale(locale)`：设置并持久化。
  - `t(key)`：按当前语言取文案，动态内容（如状态、消息 meta）在 JS 中通过 `t("key")` 使用。
  - `applyToPage()`：根据当前语言设置 `document.documentElement.lang`、`<title>`，并对所有带 `data-i18n` 的 DOM 元素填充文案；带 `data-i18n-placeholder` 的填充 `placeholder`。
- **页面用法**：需翻译的静态文案在 HTML 上增加 `data-i18n="key"`；输入框占位符使用 `data-i18n="key"` 且 `data-i18n-placeholder`。加载时与切换语言时调用 `applyToPage()`。

### 1.3 扩展新语言

在 `i18n.js` 的 `messages` 中增加新 locale（如 `"zh-TW"`），在 `detectLocale()` 与 `setLocale()` 中允许该值，并在页面上增加对应切换按钮即可。

---

## 2. 主题（浅色 / 深色）

### 2.1 行为概览

- **内置主题**：
  - **light**：浅色背景、深色文字，适合日间使用。
  - **soft-dark**：柔和深色（slate 系背景），比纯黑柔和。
  - **dark**：深色（接近原版深色），背景最深。
- **存储**：当前主题保存在 `localStorage`，键名为 `mw4agent-dashboard-theme`。
- **默认规则**：未保存时，若系统为 `prefers-color-scheme: dark` 则用 `soft-dark`，否则用 `light`。
- **切换**：页面右上角提供 **☀ / ◐ / ◇** 三个按钮，分别对应 light / soft-dark / dark，点击后立即生效并写入 `localStorage`。

### 2.2 实现要点

- **模块**：`mw4agent/dashboard/static/theme.js`。
- **主题应用**：通过 `document.documentElement.setAttribute("data-theme", currentTheme)` 设置根节点属性；CSS 中为 `[data-theme="light"]`、`[data-theme="soft-dark"]`、`[data-theme="dark"]` 分别定义一套 CSS 变量（如 `--bg`、`--bg-body`、`--panel`、`--text-main`、`--messages-bg`、`--msg-user-bg` 等），页面样式全部引用变量，无硬编码颜色。
- **接口**：
  - `getTheme()`：当前主题。
  - `setTheme(theme)`：设置并持久化。
  - `applyTheme()`：设置 `data-theme` 与 `color-scheme`。
  - `getThemes()`：返回可选主题列表。
- **加载顺序**：在 Dashboard 初始化时先执行 `applyTheme()`，再执行 `applyToPage()`，避免先渲染再闪变。

### 2.3 扩展新主题

在 `theme.js` 的 `THEMES` 中增加新 id，在 `index.html` 的 `<style>` 中增加 `[data-theme="新id"] { ... }` 变量块，并在主题切换区增加对应按钮即可。

---

## 3. 相关文件一览

| 文件 | 说明 |
|------|------|
| `mw4agent/dashboard/static/index.html` | 单页结构、主题变量与 data-i18n 标记、语言/主题切换器 DOM |
| `mw4agent/dashboard/static/app.js` | 入口逻辑、WebSocket/RPC、`agents.list` 多 Agent 面板、调用 `applyToPage`/`applyTheme`、切换器事件 |
| `mw4agent/dashboard/static/i18n.js` | 多语言文案与 `t`/`applyToPage` |
| `mw4agent/dashboard/static/theme.js` | 主题检测、`data-theme` 与 `applyTheme` |

以上静态资源由 Gateway 在 `/dashboard` 下提供（见 [CLI 手册](../manuals/cli.md) 中的 Dashboard 小节），安装时通过 `setup.py` 的 `package_data` 打包进 `mw4agent` 包。

### 3.1 Agents 标签页

- 右侧面板 **Agents** 通过 `POST /rpc` 调用 `method: "agents.list"`，展示各 `agentId` 的 `agent_dir`、`workspace_dir`、会话存储路径，以及 Gateway 进程内该 Agent 的 **运行状态**（`idle` / `running`、活动运行数、最近一次完成的 `run` 摘要）。
- 后端实现：`mw4agent/gateway/server.py`（`agents.list`）；运行状态来自 `GatewayState` 中与 `agent_id` 关联的 `RunRecord`。
