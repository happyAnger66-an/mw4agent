# MW4Agent Desktop（Orbit）

最小可用的 **Next.js + Tauri** 桌面/Web 界面：左侧栏品牌 **Orbit**，**New Task / 新任务** 打开对话弹窗，**My Agents / 我的 Agents** 通过 `agents.list` 展示全部 Agent 并可一键用该 Agent 打开对话。协议：**HTTP `POST /rpc`** + **`WebSocket /ws`**。

## 功能（当前）

- 左侧栏：Orbit、New Task、My Agents；底栏网关地址与语言 / 主题
- 中英文切换（`localStorage`: `mw4agent-desktop-locale`）
- 浅色 / 深色主题（`next-themes`）
- 连接 Gateway 事件流（自动重连、退避）
- 调用 `agent` RPC；解析 WS：`lifecycle` / `tool` / `assistant`
- 每次打开「新任务」弹窗会重置会话并清空消息；从列表点「对话」会预填对应 `agentId`

## 先启动 Gateway

```bash
# 仓库根目录，默认 http://127.0.0.1:18790
mw4agent gateway run
```

Gateway 已启用 **CORS**（默认 `allow_origins=["*"]`），便于 `localhost:3000` 与 Tauri 开发加载前端。生产环境可设置：

`GATEWAY_CORS_ORIGINS=http://localhost:3000,https://tauri.localhost`

## Web 开发

```bash
cd mw4agent/desktop
cp .env.local.example .env.local   # 按需修改网关地址
npm install
npm run dev
```

浏览器打开 <http://localhost:3000>。

## 原生桌面（Tauri）

1. 安装 [Tauri 前置依赖](https://tauri.app/start/prerequisites/)（Linux 需 WebKitGTK、GTK 等）。
2. 生成图标（仅需一次；已生成可跳过）：

   ```bash
   npx --yes @tauri-apps/cli@2 icon /path/to/1024.png
   ```

3. 构建静态前端并打包：

   ```bash
   npm run build
   npm run tauri build
   ```

4. 开发模式（同时起 Next dev + 原生窗口）：

   ```bash
   npm run tauri:dev
   ```

## 与 Shannon Desktop 的差异（刻意最小化）

- 无 Redux / 工作流可视化 / SSE；Gateway 使用 **RPC + WebSocket**。
- 无登录与多租户；与本地 Gateway 直连即可。

## 品牌图标

- 源文件可放在 `icons/planet.png`（便于与仓库其它资源一起管理）。
- Web / 静态导出从 **`public/icons/planet.png`** 读取；更新源文件后请同步：  
  `cp icons/planet.png public/icons/planet.png`  
  （或直接在 `public/icons/` 下维护同名文件。）

## 目录说明

| 路径 | 说明 |
|------|------|
| `app/` | Next App Router |
| `components/ChatApp.tsx` | 主界面与网关协议 |
| `lib/gateway.ts` | `NEXT_PUBLIC_GATEWAY_URL`、RPC/WS URL |
| `lib/i18n.tsx` | 文案与语言上下文 |
| `src-tauri/` | Tauri 2 壳 |
