/**
 * Minimal i18n for dashboard: en / zh-CN.
 * Locale is stored in localStorage (key: mw4agent-dashboard-locale).
 */

const STORAGE_KEY = "mw4agent-dashboard-locale";

const messages = {
  en: {
    title: "MW4Agent Dashboard",
    titleSub: "Browser console · Gateway WebSocket + /rpc",
    statusConnected: "CONNECTED",
    statusDisconnected: "DISCONNECTED",
    statusReconnecting: "RECONNECTING…",
    reconnecting: "Reconnecting…",
    chat: "Chat",
    chatSubtitle: "Send a message via Gateway RPC and stream assistant events over WebSocket.",
    placeholder: "Type a message to MW4Agent…",
    send: "Send",
    gateway: "Gateway",
    gatewaySubtitle: "Live connection state & last run snapshot (minimal skeleton).",
    metaWs: "WebSocket",
    metaRpc: "RPC Endpoint",
    metaLastRun: "Last run",
    metaEvents: "Events",
    total: "total",
    notConnected: "Not connected",
    connected: "Connected",
    footerHint: "This is a minimal skeleton. You can evolve it into a full Control UI (sessions, channels, skills, cron, etc.).",
    metaYou: "you",
    metaAssistant: "assistant",
    metaError: "error",
    stepThinking: "Thinking…",
    stepCallingTool: "Calling tool: {name}",
    stepToolDone: "Tool {name} finished.",
    reasoningLabel: "Reasoning",
    showReasoning: "Show reasoning",
    logs: "Logs",
    events: "Events",
    config: "Config",
    agents: "Agents",
    agentsSubtitle: "Per-agent directories, session store, and Gateway run status.",
    colAgentId: "Agent ID",
    colRunStatus: "Run status",
    colPaths: "Directories",
    agentsEmpty: "No agents loaded.",
    runStateRunning: "Running",
    runStateIdle: "Idle",
    lastRunLabel: "Last run",
    configuredBadge: "Configured",
    defaultAgentBadge: "Default layout",
    agentsLoadError: "Failed to load agents.",
    activeRuns: "{n} active",
    configSubtitle: "Full mw4agent.json config (llm, tools, channels, skills, etc.).",
    refresh: "Refresh",
    save: "Save",
    configCurrent: "Current section",
    configAll: "All",
    noConfig: "No config loaded.",
    noLogs: "No logs yet.",
    noEvents: "No events yet.",
    langEn: "English",
    langZh: "中文",
  },
  "zh-CN": {
    title: "MW4Agent 控制台",
    titleSub: "浏览器控制台 · Gateway WebSocket + /rpc",
    statusConnected: "已连接",
    statusDisconnected: "未连接",
    statusReconnecting: "重连中…",
    reconnecting: "重连中…",
    chat: "聊天",
    chatSubtitle: "通过 Gateway RPC 发送消息，并通过 WebSocket 流式接收助手回复。",
    placeholder: "输入消息发送给 MW4Agent…",
    send: "发送",
    gateway: "网关",
    gatewaySubtitle: "连接状态与最近一次运行（最小骨架）。",
    metaWs: "WebSocket",
    metaRpc: "RPC 端点",
    metaLastRun: "最近运行",
    metaEvents: "事件",
    total: "条",
    notConnected: "未连接",
    connected: "已连接",
    footerHint: "当前为最小骨架，可扩展为完整控制台（会话、通道、技能、定时任务等）。",
    metaYou: "你",
    metaAssistant: "助手",
    metaError: "错误",
    stepThinking: "思考中…",
    stepCallingTool: "正在调用工具: {name}",
    stepToolDone: "工具 {name} 已完成。",
    reasoningLabel: "推理过程",
    showReasoning: "显示推理过程",
    logs: "日志",
    events: "事件",
    config: "配置",
    agents: "Agents",
    agentsSubtitle: "各 Agent 目录、会话存储与 Gateway 运行状态。",
    colAgentId: "Agent ID",
    colRunStatus: "运行状态",
    colPaths: "目录配置",
    agentsEmpty: "暂无 Agent 数据。",
    runStateRunning: "运行中",
    runStateIdle: "空闲",
    lastRunLabel: "上次运行",
    configuredBadge: "已配置",
    defaultAgentBadge: "默认目录",
    agentsLoadError: "加载 Agent 列表失败。",
    activeRuns: "{n} 个活动运行",
    configSubtitle: "展示 mw4agent.json 的完整配置内容（llm、tools、channels、skills 等）。",
    refresh: "刷新",
    save: "保存",
    configCurrent: "当前分区",
    configAll: "全部",
    noConfig: "暂无配置内容。",
    noLogs: "暂无日志",
    noEvents: "暂无事件",
    langEn: "English",
    langZh: "中文",
  },
};

/** Replace {name} etc. in template. */
export function tFormat(key, vars) {
  let s = t(key);
  if (vars && typeof vars === "object") {
    Object.keys(vars).forEach((k) => {
      s = s.replace(new RegExp(`\\{${k}\\}`, "g"), String(vars[k]));
    });
  }
  return s;
}

function detectLocale() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "en" || stored === "zh-CN") return stored;
  const lang = (navigator.language || navigator.browserLanguage || "").toLowerCase();
  if (lang.startsWith("zh")) return "zh-CN";
  return "en";
}

let currentLocale = detectLocale();

export function getLocale() {
  return currentLocale;
}

export function setLocale(locale) {
  if (locale !== "en" && locale !== "zh-CN") return;
  currentLocale = locale;
  localStorage.setItem(STORAGE_KEY, locale);
}

export function t(key) {
  const map = messages[currentLocale] || messages.en;
  return map[key] ?? messages.en[key] ?? key;
}

export function applyToPage() {
  document.documentElement.lang = currentLocale === "zh-CN" ? "zh-CN" : "en";
  const titleEl = document.querySelector("title");
  if (titleEl) titleEl.textContent = t("title");

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (!key) return;
    const value = t(key);
    if (el.hasAttribute("data-i18n-placeholder")) {
      el.placeholder = value;
    } else {
      el.textContent = value;
    }
  });
}
