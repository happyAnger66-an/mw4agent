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
  statsNav: "Statistics",
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
  agentsLlmUsage: "LLM usage (lifetime)",
  agentsLlmUsageHint:
    "Cumulative prompt/completion/total tokens from provider usage; runs counts completed turns that recorded usage.",
  agentsLlmUsageInOut: "in {prompt} · out {completion}",
  agentsLlmUsageTotalRuns: "total {total} · runs {runs}",

  statsTitle: "Statistics",
  statsSubtitle: "Usage and metrics from the Gateway (read-only).",
  statsSectionAgentLlm: "Agent LLM tokens (lifetime)",
  statsColPrompt: "Prompt",
  statsColCompletion: "Completion",
  statsColTotal: "Total",
  statsColRequests: "Requests",
  statsColUpdated: "Last updated",
  statsLoading: "Loading statistics…",
  statsError: "Could not load statistics.",
  statsEmpty: "No agents or no usage recorded yet.",
  statsMoreComing:
    "Additional metrics (sessions, tools, orchestrations, etc.) can be summarized here in future versions.",

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

  settingsNav: "Settings",
  settingsTitle: "Settings",
  settingsSections: "Sections",
  settingsRefresh: "Refresh",
  settingsReload: "Reload",
  settingsLoading: "Loading…",
  settingsSaving: "Saving…",
  settingsSave: "Save",
  settingsSaved: "Saved.",
  settingsNoSections: "No sections.",
  settingsSectionHint: "Edit one top-level section from mw4agent.json (JSON object only).",
  settingsModeForm: "Basic",
  settingsModeJson: "Advanced JSON",
  settingsFormNotAvailable: "No basic form for this section. Use Advanced JSON.",
  settingsLlmProvider: "Provider",
  settingsLlmModel: "Model",
  settingsLlmBaseUrl: "Base URL",
  settingsLlmApiKey: "API key",
  settingsLlmContextWindow: "Context window (tokens)",
  settingsLlmMaxTokens: "Max output tokens",
  settingsLlmThinking: "Thinking level",
  settingsLlmTest: "Test connection",
  settingsLlmTesting: "Testing…",
  settingsLlmTestFailed: "LLM test failed.",
  settingsApiKeyHint: "Stored in mw4agent.json. Avoid sharing secrets.",
  settingsMemoryEnabled: "Enable MemoryIndex",
  settingsSessionCompactionEnabled: "Enable session compaction",
  settingsToolsPolicy: "Tool policy",
  settingsToolsProfile: "Profile",
  settingsToolsProfileDefault: "default",
  settingsToolsAllow: "Allow",
  settingsToolsDeny: "Deny",
  settingsToolsPolicyHint:
    "profile controls tool visibility. allow/deny are comma-separated tool names, saved as arrays.",
  settingsToolsWebSearch: "Web search",
  settingsToolsWebFetch: "Web fetch",
  settingsEnabled: "Enabled",
  settingsProvider: "Provider",
  settingsTimeoutSeconds: "Timeout (s)",
  settingsCacheTtlMinutes: "Cache TTL (min)",
  settingsWebSearchProxy: "HTTPS proxy (optional)",
  settingsWebSearchApiKey: "Search API key (optional)",
  settingsWebSearchProviderApiKey: "Provider API key (optional)",
  settingsWebSearchProviderApiKeyHint:
    "If set, overrides the generic key for the provider named above (tools.web.search.<provider>.apiKey).",
  settingsUserAgent: "User-Agent",
  settingsMaxRedirects: "Max redirects",
  settingsMaxResponseBytes: "Max response bytes",
  settingsMaxCharsCap: "Max chars cap",
  settingsChannelsType: "Channel type",
  settingsChannelsSelectType: "Select a channel…",
  settingsChannelsStatus: "Status",
  settingsChannelsPickOne: "Select a channel type to configure.",
  settingsChannelsConsoleNoConfig: "Console channel has no required config.",
  settingsChannelsEnable: "Enable (create empty config)",
  settingsChannelsRemove: "Remove",
  settingsChannelsFeishuAppId: "Feishu app_id",
  settingsChannelsFeishuAppSecret: "Feishu app_secret",
  settingsChannelsFeishuMode: "Connection mode",
  settingsChannelsFeishuUat: "Feishu MCP user access token (optional)",
  settingsChannelsAdvancedHint:
    "This channel type is not yet form-supported. You can edit its per-channel JSON here, then Apply.",
  settingsChannelsApply: "Apply",
  settingsInvalidJson: "Invalid JSON.",
  settingsValueMustBeObject: "Value must be a JSON object.",
  settingsLoadError: "Could not load config sections.",
  settingsSaveError: "Could not save config section.",
};

const zhCN: Record<string, string> = {
  brandOrbit: "Orbit",
  newTask: "新任务",
  myAgents: "我的智能体",
  orchestrateNav: "编排",
  skillsNav: "技能",
  statsNav: "统计",
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
  agentsLlmUsage: "LLM 用量（累计）",
  agentsLlmUsageHint: "来自接口返回的 prompt/completion/total；次数为计入用量的已完成轮次。",
  agentsLlmUsageInOut: "输入 {prompt} · 输出 {completion}",
  agentsLlmUsageTotalRuns: "合计 {total} · 次数 {runs}",

  statsTitle: "统计",
  statsSubtitle: "来自网关的用量与指标（只读）。",
  statsSectionAgentLlm: "智能体 LLM 用量（累计）",
  statsColPrompt: "输入 (prompt)",
  statsColCompletion: "输出 (completion)",
  statsColTotal: "合计",
  statsColRequests: "次数",
  statsColUpdated: "最近更新",
  statsLoading: "正在加载统计…",
  statsError: "无法加载统计数据。",
  statsEmpty: "暂无智能体或尚无用量记录。",
  statsMoreComing: "后续版本可在此汇总更多指标（会话、工具、编排等）。",

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

  settingsNav: "配置",
  settingsTitle: "配置",
  settingsSections: "配置子项",
  settingsRefresh: "刷新",
  settingsReload: "重载",
  settingsLoading: "加载中…",
  settingsSaving: "保存中…",
  settingsSave: "保存",
  settingsSaved: "已保存。",
  settingsNoSections: "暂无配置子项。",
  settingsSectionHint: "编辑 mw4agent.json 的顶层配置段（仅支持 JSON 对象）。",
  settingsModeForm: "基础配置",
  settingsModeJson: "高级 JSON",
  settingsFormNotAvailable: "该配置段暂未提供表单视图，请用高级 JSON。",
  settingsLlmProvider: "Provider",
  settingsLlmModel: "Model",
  settingsLlmBaseUrl: "Base URL",
  settingsLlmApiKey: "API key",
  settingsLlmContextWindow: "上下文窗口（tokens）",
  settingsLlmMaxTokens: "最大输出 tokens",
  settingsLlmThinking: "Thinking level",
  settingsLlmTest: "测试连接",
  settingsLlmTesting: "测试中…",
  settingsLlmTestFailed: "LLM 测试失败。",
  settingsApiKeyHint: "会写入 mw4agent.json，请避免泄露密钥。",
  settingsMemoryEnabled: "启用 MemoryIndex",
  settingsSessionCompactionEnabled: "启用会话压缩",
  settingsToolsPolicy: "工具策略",
  settingsToolsProfile: "Profile",
  settingsToolsProfileDefault: "默认",
  settingsToolsAllow: "Allow",
  settingsToolsDeny: "Deny",
  settingsToolsPolicyHint:
    "profile 控制工具可见性；allow/deny 逗号分隔工具名，保存为数组。",
  settingsToolsWebSearch: "Web Search",
  settingsToolsWebFetch: "Web Fetch",
  settingsEnabled: "启用",
  settingsProvider: "Provider",
  settingsTimeoutSeconds: "超时（秒）",
  settingsCacheTtlMinutes: "缓存 TTL（分钟）",
  settingsWebSearchProxy: "HTTPS 代理（可选）",
  settingsWebSearchApiKey: "搜索 API 密钥（可选）",
  settingsWebSearchProviderApiKey: "按 Provider 的 API 密钥（可选）",
  settingsWebSearchProviderApiKeyHint:
    "若填写，将覆盖上方通用密钥，对应 tools.web.search.<provider>.apiKey。",
  settingsUserAgent: "User-Agent",
  settingsMaxRedirects: "最大重定向次数",
  settingsMaxResponseBytes: "响应体上限（字节）",
  settingsMaxCharsCap: "返回字符上限",
  settingsChannelsType: "通道类型",
  settingsChannelsSelectType: "选择一个通道…",
  settingsChannelsStatus: "状态",
  settingsChannelsPickOne: "请选择一个通道类型后再配置。",
  settingsChannelsConsoleNoConfig: "Console 通道无需配置。",
  settingsChannelsEnable: "启用（创建空配置）",
  settingsChannelsRemove: "移除",
  settingsChannelsFeishuAppId: "飞书 app_id",
  settingsChannelsFeishuAppSecret: "飞书 app_secret",
  settingsChannelsFeishuMode: "连接模式",
  settingsChannelsFeishuUat: "飞书 MCP 用户访问令牌（可选）",
  settingsChannelsAdvancedHint:
    "该通道类型暂未提供表单视图。可在此编辑该通道的 JSON，然后点击应用。",
  settingsChannelsApply: "应用",
  settingsInvalidJson: "JSON 格式不正确。",
  settingsValueMustBeObject: "必须是 JSON 对象（object）。",
  settingsLoadError: "加载配置失败。",
  settingsSaveError: "保存配置失败。",
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
