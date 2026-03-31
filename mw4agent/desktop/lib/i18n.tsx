"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type DesktopLocale = "en" | "zh-CN";

const STORAGE_KEY = "mw4agent-desktop-locale";

type Params = Record<string, string | number | undefined>;

function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const v = params[key];
    return v !== undefined && v !== null ? String(v) : `{${key}}`;
  });
}

const en: Record<string, string> = {
  brandOrbit: "Orbit",
  newTask: "New task",
  myAgents: "My Agents",
  skillsNav: "Skills",
  orchestrateNav: "Orchestrate",
  gatewayUrl: "Gateway URL",
  themeLight: "Light",
  themeDark: "Dark",
  homeBlurb:
    "Start a task, manage agents, or inspect skills discovered from your workspace.",
  closeDialog: "Close",

  stepThinking: "Thinking…",
  stepCallingTool: "Calling tool: {name}",
  stepToolDone: "Tool finished: {name}",
  errorRpc: "Request failed",

  connected: "Connected",
  reconnecting: "Reconnecting…",
  disconnected: "Disconnected",

  subtitle: "Chat with your agent via Gateway",
  chatWorkPrompt: "Assign a task",
  language: "Language",
  newChat: "New chat",
  agentId: "Agent ID",
  sessionKey: "Session key",
  placeholder: "Message…",
  metaYou: "You",
  metaAssistant: "Assistant",
  reasoning: "Reasoning",
  chatShowReasoning: "Show reasoning / progress",
  chatThinkTool: "Think",
  chatToolActivity: "Tool activity",
  chatToolPlanned: "Planned tool calls",
  chatToolRunning: "Running {name}… ({seconds}s)",
  send: "Send",
  orchestrateTitle: "Orchestrator",
  orchestrateCreate: "New orchestration",
  orchestrateCancel: "Cancel",
  orchestrateName: "Name (optional)",
  orchestrateMaxRounds: "Max rounds (per message)",
  orchestrateStrategy: "Routing strategy",
  orchestrateStrategyRoundRobin: "Round-robin",
  orchestrateStrategyRouter: "Router LLM",
  orchestrateStrategyDag: "DAG",
  orchestrateStrategySupervisor: "Supervisor pipeline",
  orchestrateSupervisorMaxIter: "Max macro-iterations (per message)",
  orchestrateSupervisorParticipantsHint:
    "List order is the run order A→B→C; remove and re-add to change order.",
  orchestrateSupervisorHint:
    "After each full pipeline pass, the supervisor LLM chooses continue (another pass) or stop.",
  orchestrateSupervisorLlmMaxRetries:
    "Supervisor LLM max retries (10s apart after failure or empty response)",
  orchestrateDagJson: "DAG spec (JSON)",
  orchestrateDagJsonHint:
    "nodes: [{ id, agentId, dependsOn?, title?, position?:{x,y} }]. Optional parallelism (1–32). The visual editor stays in sync when valid.",
  orchestrateDagJsonInvalid: "Invalid DAG JSON.",
  orchestrateDagVisualTab: "Visual",
  orchestrateDagJsonTab: "JSON",
  orchestrateDagCanvasLoading: "Loading canvas…",
  orchestrateDagCanvasHint:
    "Drag from the bottom handle of a node to the top of another to add a dependency (downstream depends on upstream). Delete nodes or edges with Delete/Backspace.",
  orchestrateDagAddNode: "Add node",
  orchestrateDagParallelism: "Parallelism",
  orchestrateDagEdgeHint: "Edge: upstream → downstream",
  orchestrateDagCycleError: "DAG has a cycle; remove edges until the graph is acyclic.",
  orchestrateDagNote:
    "DAG mode runs each node once per message; participants are inferred from nodes.",
  orchestrateNodeMeta: "node",
  orchestrateRouterHint: "Router LLM selects the next speaker from participants.",
  orchestrateAll: "Orchestrations",
  orchestrateEmptyList: "No orchestrations yet.",
  orchestratePickOne: "Pick an orchestration on the left.",
  orchestrateAddAgent: "Add agent",
  orchestrateParticipants: "Participants",
  orchestratePrompt: "Type a task for the group…",
  orchestrateMentionHint:
    "Use @agentId (participant name) to send only to that agent for this turn — round-robin & router modes.",
  orchestrateMentionNoMatch: "No participant matches this prefix.",
  orchestrateStatus: "Status",
  orchestrateEmpty: "No messages yet.",
  orchestrateError: "Orchestrator request failed",
  orchestrateNetworkError:
    "Cannot reach gateway. Check NEXT_PUBLIC_GATEWAY_URL and that the gateway is running.",
  orchestrateDelete: "Delete",
  orchestrateDeleteConfirm: "Delete orchestration “{name}”? This will remove its local data.",
  orchestrateEditTooltip: "Edit orchestration",
  orchestrateEditTitle: "Edit orchestration · {name}",
  orchestrateEditSave: "Save changes",
  orchestrateEditRunningError: "Cannot edit while the orchestration is running.",

  skillsTitle: "Skills",
  skillsSummary: "{count} skill(s) loaded",
  skillsVersion: "Version",
  skillsPromptCompact: "Prompt compact",
  skillsPromptTruncated: "Prompt truncated",
  skillsRefresh: "Refresh",
  skillsFilteredOut: "{n} filtered out",
  skillsLoading: "Loading skills…",
  skillsEmpty: "No skills found.",
  skillsError: "Could not load skills.",
  skillName: "Name",
  skillSource: "Source",
  skillDescription: "Description",
  skillLocation: "Location",

  agentsError: "Could not load agents.",
  agentsRefresh: "Refresh",
  agentsLoading: "Loading agents…",
  agentsEmpty: "No agents yet.",
  workspaceDir: "Workspace",
  runStatus: "Run status",
  actions: "Actions",
  agentNotConfigured: "Not configured",
  activeRuns: "Active runs",
  useInChat: "Assign task",

  agentsAddTooltip: "New agent",
  agentsCreateTitle: "New agent",
  agentsCreateAvatar: "Avatar",
  agentsCreateAvatarHint: "Shown in chat; files live in public/icons/headers",
  agentsCreateAvatarNone: "Default",
  agentsEditAvatarTooltip: "Edit avatar",
  agentsEditAvatarTitle: "Edit avatar",
  agentsEditAvatarSave: "Save",
  agentsCreateAgentId: "Agent ID",
  agentsCreateWorkspace: "Workspace path",
  agentsCreateWorkspaceHint: "Default for this ID (from Gateway)",
  agentsCreateUseDefaultWorkspace: "Reset to default",
  agentsCreateLlmProvider: "LLM provider",
  agentsCreateLlmModel: "Model",
  agentsCreateLlmBaseUrl: "Base URL (optional)",
  agentsCreateLlmApiKey: "API key (optional)",
  agentsCreateLlmThinking: "Thinking level (optional)",
  agentsCreateLlmOptional: "Leave blank to use global config",
  agentsCreateSubmit: "Create",
  agentsCreateCancel: "Cancel",
  agentsCreateIdRequired: "Please enter an agent ID.",
  agentsDeleteTooltip: "Delete agent",
  agentsDeleteConfirm: "Delete agent “{id}”? This will remove its local state.",
  agentsEditMemoryTooltip: "Edit memory.md",
  agentsEditSoulTooltip: "Edit SOUL.md",
  agentsEditLlmTooltip: "Edit LLM settings",
  agentsEditLlmTitle: "LLM: {id}",
  agentsEditLlmSave: "Save",
  agentsEditLlmApiKeyPlaceholder: "••••••••",
  agentsEditLlmApiKeyHint:
    "Leave blank to keep the existing API key; enter a new value to replace it.",
  agentsLlmTest: "Test API",
  agentsLlmTestTooltip: "Send a minimal chat request to verify LLM connectivity",
  agentsFileEditorTitle: "Edit {path} ({id})",
  agentsFileEditorLoading: "Loading…",
  agentsFileEditorCancel: "Cancel",
  agentsFileEditorSave: "Save",
  agentsFileEditorError: "Could not load/save file.",
};

const zhCN: Record<string, string> = {
  brandOrbit: "Orbit",
  newTask: "新任务",
  myAgents: "我的智能体",
  orchestrateNav: "编排",
  skillsNav: "技能",
  gatewayUrl: "网关地址",
  themeLight: "浅色",
  themeDark: "深色",
  homeBlurb: "发起任务、管理智能体，或查看工作区发现的技能。",
  closeDialog: "关闭",

  stepThinking: "思考中…",
  stepCallingTool: "正在调用工具：{name}",
  stepToolDone: "工具已完成：{name}",
  errorRpc: "请求失败",

  connected: "已连接",
  reconnecting: "正在重连…",
  disconnected: "未连接",

  subtitle: "通过网关与智能体对话",
  chatWorkPrompt: "请安排工作",
  language: "语言",
  newChat: "新会话",
  agentId: "智能体 ID",
  sessionKey: "会话键",
  placeholder: "输入消息…",
  metaYou: "你",
  metaAssistant: "助手",
  reasoning: "推理",
  chatShowReasoning: "显示推理与过程",
  chatThinkTool: "推理推送",
  chatToolActivity: "工具执行",
  chatToolPlanned: "计划调用的工具",
  chatToolRunning: "正在执行 {name}…（{seconds} 秒）",
  send: "发送",
  orchestrateTitle: "编排",
  orchestrateCreate: "新建协作",
  orchestrateCancel: "取消",
  orchestrateName: "名称（可选）",
  orchestrateMaxRounds: "最大轮次（每条消息）",
  orchestrateStrategy: "路由策略",
  orchestrateStrategyRoundRobin: "轮询",
  orchestrateStrategyRouter: "路由 LLM",
  orchestrateStrategyDag: "DAG",
  orchestrateStrategySupervisor: "监督流水线",
  orchestrateSupervisorMaxIter: "最大宏迭代次数（每条消息）",
  orchestrateSupervisorParticipantsHint: "列表顺序即执行顺序 A→B→C；可删除后重新添加以调整顺序。",
  orchestrateSupervisorHint: "每一轮完整流水线结束后，由监督 LLM 决定继续再来一轮或结束。",
  orchestrateSupervisorLlmMaxRetries: "监督 LLM 最大重试次数（失败或空响应后间隔 10 秒）",
  orchestrateDagJson: "DAG 定义（JSON）",
  orchestrateDagJsonHint:
    "nodes: [{ id, agentId, dependsOn?, title?, position?:{x,y} }]，可选 parallelism（1–32）。JSON 合法时会与画布同步。",
  orchestrateDagJsonInvalid: "DAG JSON 无效。",
  orchestrateDagVisualTab: "可视化",
  orchestrateDagJsonTab: "JSON",
  orchestrateDagCanvasLoading: "正在加载画布…",
  orchestrateDagCanvasHint:
    "从节点下方连接点拖到另一节点上方连接点表示依赖（下游依赖上游）。可用 Delete/退格 删除节点或边。",
  orchestrateDagAddNode: "添加节点",
  orchestrateDagParallelism: "并行度",
  orchestrateDagEdgeHint: "边：上游 → 下游",
  orchestrateDagCycleError: "DAG 存在环路，请删除部分边直至无环。",
  orchestrateDagNote: "DAG 模式下每条消息会按图执行一轮；参与者由节点内的 agentId 推导。",
  orchestrateNodeMeta: "节点",
  orchestrateRouterHint: "路由 LLM 会从参与者中选择下一位发话人。",
  orchestrateAll: "全部编排",
  orchestrateEmptyList: "暂无编排。",
  orchestratePickOne: "请在左侧选择一个编排。",
  orchestrateAddAgent: "添加智能体",
  orchestrateParticipants: "参与者",
  orchestratePrompt: "给这组智能体派活…",
  orchestrateMentionHint:
    "输入 @智能体ID（须为参与者之一）可指定仅由该智能体回复本轮；适用于轮询与路由模式。",
  orchestrateMentionNoMatch: "没有参与者匹配当前前缀。",
  orchestrateStatus: "状态",
  orchestrateEmpty: "暂无对话。",
  orchestrateError: "编排请求失败",
  orchestrateNetworkError: "无法连接网关，请检查 NEXT_PUBLIC_GATEWAY_URL 及网关是否在运行。",
  orchestrateDelete: "删除",
  orchestrateDeleteConfirm: "确认删除协作“{name}”？这会移除其本地数据。",
  orchestrateEditTooltip: "编辑编排",
  orchestrateEditTitle: "编辑编排 · {name}",
  orchestrateEditSave: "保存修改",
  orchestrateEditRunningError: "编排运行中，无法修改配置。",

  skillsTitle: "技能",
  skillsSummary: "已加载 {count} 个技能",
  skillsVersion: "版本",
  skillsPromptCompact: "提示已压缩",
  skillsPromptTruncated: "提示已截断",
  skillsRefresh: "刷新",
  skillsFilteredOut: "已过滤 {n} 项",
  skillsLoading: "正在加载技能…",
  skillsEmpty: "暂无技能。",
  skillsError: "无法加载技能。",
  skillName: "名称",
  skillSource: "来源",
  skillDescription: "说明",
  skillLocation: "路径",

  agentsError: "无法加载智能体列表。",
  agentsRefresh: "刷新",
  agentsLoading: "正在加载智能体…",
  agentsEmpty: "暂无智能体。",
  workspaceDir: "工作区",
  runStatus: "运行状态",
  actions: "操作",
  agentNotConfigured: "未配置",
  activeRuns: "进行中",
  useInChat: "派活",

  agentsAddTooltip: "新建智能体",
  agentsCreateTitle: "新建智能体",
  agentsCreateAvatar: "头像",
  agentsCreateAvatarHint: "在聊天中显示；图片放在 public/icons/headers",
  agentsCreateAvatarNone: "默认",
  agentsEditAvatarTooltip: "编辑头像",
  agentsEditAvatarTitle: "编辑头像",
  agentsEditAvatarSave: "保存",
  agentsCreateAgentId: "智能体 ID",
  agentsCreateWorkspace: "工作区路径",
  agentsCreateWorkspaceHint: "根据 ID 由网关给出的默认路径",
  agentsCreateUseDefaultWorkspace: "恢复默认路径",
  agentsCreateLlmProvider: "LLM 提供商",
  agentsCreateLlmModel: "模型",
  agentsCreateLlmBaseUrl: "API Base URL（可选）",
  agentsCreateLlmApiKey: "API Key（可选）",
  agentsCreateLlmThinking: "思考档位（可选）",
  agentsCreateLlmOptional: "留空则使用全局配置",
  agentsCreateSubmit: "创建",
  agentsCreateCancel: "取消",
  agentsCreateIdRequired: "请填写智能体 ID。",
  agentsDeleteTooltip: "删除智能体",
  agentsDeleteConfirm: "确认删除智能体“{id}”？这会移除其本地状态数据。",
  agentsEditMemoryTooltip: "编辑 memory.md",
  agentsEditSoulTooltip: "编辑 SOUL.md",
  agentsEditLlmTooltip: "编辑 LLM 配置",
  agentsEditLlmTitle: "LLM 配置：{id}",
  agentsEditLlmSave: "保存",
  agentsEditLlmApiKeyPlaceholder: "已保存（留空不变）",
  agentsEditLlmApiKeyHint: "留空保留已有 API Key；填写新值则替换。",
  agentsLlmTest: "测试连接",
  agentsLlmTestTooltip: "发送最小对话请求，验证 LLM / API 是否可用",
  agentsFileEditorTitle: "编辑 {path}（{id}）",
  agentsFileEditorLoading: "正在加载…",
  agentsFileEditorCancel: "取消",
  agentsFileEditorSave: "保存",
  agentsFileEditorError: "文件加载/保存失败。",
};

const MESSAGES: Record<DesktopLocale, Record<string, string>> = {
  en,
  "zh-CN": zhCN,
};

function detectInitialLocale(): DesktopLocale {
  if (typeof window === "undefined") return "en";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)?.trim();
    if (stored === "zh-CN" || stored === "en") return stored;
  } catch {
    /* ignore */
  }
  const nav = (navigator.language || "").toLowerCase();
  if (nav.startsWith("zh")) return "zh-CN";
  return "en";
}

type I18nContextValue = {
  locale: DesktopLocale;
  setLocale: (loc: DesktopLocale) => void;
  t: (key: keyof typeof en | string, params?: Params) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<DesktopLocale>("en");

  useEffect(() => {
    setLocaleState(detectInitialLocale());
  }, []);

  const setLocale = useCallback((loc: DesktopLocale) => {
    setLocaleState(loc);
    try {
      window.localStorage.setItem(STORAGE_KEY, loc);
    } catch {
      /* ignore */
    }
  }, []);

  const t = useCallback(
    (key: string, params?: Params) => {
      const table = MESSAGES[locale];
      const raw = table[key] ?? MESSAGES.en[key] ?? key;
      return interpolate(raw, params);
    },
    [locale]
  );

  const value = useMemo(
    () => ({ locale, setLocale, t }),
    [locale, setLocale, t]
  );

  return (
    <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return ctx;
}
