# Shannon Desktop 实现参考

本文档基于工作区内的 **`/home/zhangxa/codes/claw/Shannon/desktop`**（及 Shannon 仓库根目录说明），概括 **Shannon Desktop** 的架构、数据流与主要技术选型，供 mw4agent 侧做桌面/Web 客户端对标或集成时参考。

---

## 1. 总体架构

Shannon Desktop 是 **「Web 前端 + 原生壳」** 的组合：

| 模式 | 行为 |
|------|------|
| **纯 Web 开发** | `npm run dev` 启动 Next.js 开发服务器，浏览器访问（如 `http://localhost:3000`）。 |
| **Tauri 桌面** | Rust 运行时内嵌 WebView，开发时加载 `devUrl`（同上），生产构建时加载静态导出目录 `out/`。 |

**后端依赖**：UI 通过 HTTP 调用 Shannon **Gateway**（默认 `http://localhost:8080`），与编排器、Agent、LLM 等后端服务解耦；桌面应用本身不承担业务编排逻辑。

---

## 2. 前端：Next.js 静态导出

- **框架**：Next.js 16，**App Router**（`app/` 目录）。
- **构建配置**（`next.config.ts`）：
  - `output: 'export'`：纯静态导出，无 Node 服务端运行时，便于打进 Tauri 资源包。
  - `trailingSlash: true`、`images.unoptimized: true`：与静态托管/WebView 场景常见配置一致。
- **UI**：React 19、Tailwind CSS 4、**Radix UI** + **shadcn/ui** 风格组件（`components/ui/`）、`next-themes` 做暗色/系统主题。
- **字体**：`next/font`（Geist）在根 `layout` 中注入。

---

## 3. 桌面壳：Tauri v2

- **入口**：`src-tauri/src/main.rs` 调用 `app_lib::run()`；`lib.rs` 中 `tauri::Builder` 组装应用。
- **已启用插件**：
  - **`tauri-plugin-shell`**：与系统 shell/外部命令相关能力（按 Tauri 惯例封装）。
  - **`tauri-plugin-log`**：仅在 **debug** 构建下注册，便于开发期日志。
- **构建管线**（`tauri.conf.json`）：
  - `frontendDist`: `../out` ← 对应 `npm run build`（Next 静态导出）。
  - `beforeDevCommand`: `npm run dev`；`devUrl`: `http://localhost:3000`。
  - `beforeBuildCommand`: `npm run build`。
- **分发**：`bundle` 多平台（含 AppImage、deb、msi 等）；**updater** 配置指向 GitHub Releases 的 `latest.json`（公钥内置于配置）。
- **安全**：当前 `csp: null`，依赖 WebView 默认与后端 API 的常规 HTTPS/同源策略（生产环境通常需收紧 CSP）。

**说明**：仓库中 `lib/tauri.ts` 为 **OSS Web-only 存根**（`isTauri()` 恒为 `false`，通知/文件对话框等为 no-op）。完整桌面集成可能在其他分支或通过条件编译补充；文档仍以 `package.json` / `src-tauri` 为准描述「目标形态」。

---

## 4. 与后端的通信

### 4.1 REST API

- 模块：`lib/shannon/api.ts`。
- **基址**：`NEXT_PUBLIC_API_URL`，默认 `http://localhost:8080`。
- 覆盖能力（节选）：认证注册/登录/刷新/`me`、任务提交与列表/详情、会话 CRUD/历史/事件、任务暂停/恢复/取消、调度（schedules）、skills 列表与详情等——与 Shannon Gateway 的 `/api/v1/...` 对齐。

### 4.2 鉴权策略

`getAuthHeaders()` 优先级：

1. **`X-API-Key`**（`getAPIKey()`，OSS 后端常用）。
2. **`Authorization: Bearer <JWT>`**。
3. 开发兜底：**`X-User-Id`**（环境变量 `NEXT_PUBLIC_USER_ID`，与种子数据对齐）。

### 4.3 SSE（Server-Sent Events）实时流

- **URL 构造**：`getStreamUrl(workflowId)` →  
  `GET /api/v1/stream/sse?workflow_id=...`  
  因浏览器 **`EventSource` 无法自定义 Header**，鉴权通过 **Query** 传递：`api_key` 或 `token`。
- **客户端实现**：`lib/shannon/stream.ts` 中 **`useRunStream`** Hook：
  - 使用 **`EventSource`** 连接，按 **事件名** 注册大量 Shannon 事件类型（工作流生命周期、Agent、LLM、工具、合成、审批、蜂群状态等）。
  - **`thread.message.delta`**：按 `stream_id` / `seq` 做 **增量缓冲**，用 **`requestAnimationFrame`** 合并后再 `dispatch`，减少 UI 抖动。
  - **断线重连**：指数退避 + 上限；支持 `last_event_id` 查询参数做 **断点续传**（从 `lastEventId` 或 payload 中的 `seq`/`stream_id`/`id` 等候选字段提取）。
  - **`done` / `STREAM_END`**：冲刷缓冲、置连接状态为 idle、关闭连接。
- **状态落点**：事件通过 **Redux** `run/addEvent` 等 action 进入 `runSlice`，驱动会话/运行详情 UI。

---

## 5. 状态管理

### 5.1 Redux Toolkit + redux-persist

- **Store**：`lib/store.ts`，`configureStore` + `persistReducer`。
- **`run` 切片**：`lib/features/runSlice.ts`（体积大），集中存放：
  - SSE 原始事件、派生 **消息列表**（含流式、截图、浏览器工具、HITL 研究计划等标记）。
  - 连接状态、流错误、会话标题、Agent 模式（normal / deep_research / browser_use）、研究策略。
  - 暂停/恢复/取消、浏览器自动化迭代与工具历史、蜂群（swarm）板与代理注册表、技能选择等。
- **持久化**：仅 **白名单** 持久化 `run` 中的轻量偏好（如 `selectedAgent`、`researchStrategy`）；**排除**大体积字段（事件、消息、含 base64 的 tool 历史等），避免撑爆 localStorage。

### 5.2 Zustand（vanilla）— Radar 可视化

- **`lib/radar/store.ts`**：`zustand/vanilla` 的 **`createStore`**，维护雷达图所需的 **items / agents / metrics** 等，并提供 **`applySnapshot` / `applyTick`**（带 `tick_id` 去重）做增量更新。
- **`components/radar/RadarBridge.tsx`**：**无 UI**，监听 Redux 中的 SSE 事件，过滤噪声后写入 radar store。
- **`components/radar/RadarCanvas.tsx`**：Canvas 绘制，从 `radarStore` 读状态。

该模式将 **「业务真相源（Redux）」** 与 **「可视化子系统（Zustand）」** 分离，避免把绘图专用状态塞进主 slice。

---

## 6. 其他依赖与文档声明

| 依赖 | 在代码中的角色（据当前树检索） |
|------|-------------------------------|
| **react-markdown** + **remark-gfm** + **rehype-highlight** | Markdown 渲染（GFM、代码高亮）。 |
| **class-variance-authority**、**clsx**、**tailwind-merge** | 组件变体与 class 合并。 |
| **lucide-react** | 图标。 |
| **dagre** | `package.json` 声明；常用于流程图自动布局（若与 `@xyflow` 联用）。 |
| **@xyflow/react** | `package.json` / README 声明为流程图技术栈；**当前应用源码树中未检出直接 import**（可能预留或位于未跟踪路径）。 |
| **dexie** | README 写明原生应用侧 **IndexedDB 离线历史**；**当前应用源码树中未检出直接使用**（可能仅在 Tauri 分支或后续功能中接入）。 |

以 **README + package.json** 为准时，可将二者记为「计划/可选能力」；以 **仓库现状** 为准时，核心可运行路径是 **REST + SSE + Redux + Radar(Zustand) + Tauri 壳**。

---

## 7. 与 Shannon 整体平台的关系

Shannon 平台本体为 **Rust（agent-core）+ Go（Temporal 编排）+ Python（LLM 服务）** 等（详见 Shannon 根目录 `CLAUDE.md` / `README.md`）。Desktop **仅作为客户端**：提交任务、展示流式事件与会话、部分控制（暂停/恢复/取消等），**不实现**工作流引擎或工具沙箱。

---

## 8. 参考路径速查

| 内容 | 路径（相对于 `Shannon/desktop/`） |
|------|-----------------------------------|
| NPM 脚本与依赖 | `package.json` |
| Next 静态导出配置 | `next.config.ts` |
| Tauri 配置 | `src-tauri/tauri.conf.json` |
| Tauri Rust 入口 | `src-tauri/src/lib.rs`、`main.rs` |
| API 与 SSE URL | `lib/shannon/api.ts` |
| SSE Hook | `lib/shannon/stream.ts` |
| Redux Store | `lib/store.ts`、`lib/features/runSlice.ts` |
| Radar | `lib/radar/store.ts`、`components/radar/*` |
| 全局 Provider | `components/providers.tsx` |
| 使用说明与构建命令 | `README.md` |

---

*文档生成时对照的 Shannon Desktop 版本号以 `package.json` / `tauri.conf.json` 中的 `0.3.1` 为准；若上游升级，请以实际仓库为准复核。*
