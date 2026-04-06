/**
 * HTTP JSON-RPC client for Orbit Gateway (`POST /rpc`).
 * Base URL: `NEXT_PUBLIC_GATEWAY_URL` or http://127.0.0.1:18790
 */

export function getGatewayBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_GATEWAY_URL;
  if (typeof raw === "string" && raw.trim()) {
    return raw.replace(/\/+$/, "");
  }
  return "http://127.0.0.1:18790";
}

export function getGatewayWsUrl(): string {
  const base = getGatewayBaseUrl();
  try {
    const u = new URL(base);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.pathname = "/ws";
    u.search = "";
    u.hash = "";
    return u.toString().replace(/\/+$/, "");
  } catch {
    return base.replace(/^http/, "ws").replace(/\/+$/, "") + "/ws";
  }
}

export type RpcResult = {
  id?: string;
  ok: boolean;
  payload?: Record<string, unknown>;
  error?: { code?: string; message?: string };
  runId?: string;
};

export type ConfigSectionsListResult =
  | { ok: true; sections: string[] }
  | { ok: false; error?: string };

export async function configSectionsList(): Promise<ConfigSectionsListResult> {
  const r = await callRpc("config.sections.list", {});
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "config.sections.list failed" };
  }
  const sections = Array.isArray(r.payload.sections) ? (r.payload.sections as string[]) : [];
  return { ok: true, sections };
}

export type ConfigSectionGetResult =
  | { ok: true; section: string; value: Record<string, unknown> }
  | { ok: false; error?: string };

export async function configSectionGet(section: string): Promise<ConfigSectionGetResult> {
  const r = await callRpc("config.section.get", { section: section.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "config.section.get failed" };
  }
  const sec = String(r.payload.section ?? section);
  const raw = r.payload.value;
  const value =
    raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  return { ok: true, section: sec, value };
}

export type ConfigSectionSetResult =
  | { ok: true; section: string }
  | { ok: false; error?: string };

export async function configSectionSet(
  section: string,
  value: Record<string, unknown>
): Promise<ConfigSectionSetResult> {
  const r = await callRpc("config.section.set", { section: section.trim(), value });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "config.section.set failed" };
  }
  return { ok: true, section: String(r.payload.section ?? section) };
}

export type LlmProviderInfo = {
  id: string;
  default_base_url?: string | null;
  default_model?: string;
  api_key_env?: string;
  require_api_key?: boolean;
  base_url_required?: boolean;
};

export type LlmProvidersListResult =
  | { ok: true; providers: string[]; providerInfos: LlmProviderInfo[] }
  | { ok: false; error?: string };

export async function llmProvidersList(): Promise<LlmProvidersListResult> {
  const r = await callRpc("llm.providers.list", {});
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "llm.providers.list failed" };
  }
  const providers = Array.isArray(r.payload.providers) ? (r.payload.providers as string[]) : [];
  const infos = Array.isArray(r.payload.providerInfos) ? (r.payload.providerInfos as LlmProviderInfo[]) : [];
  return { ok: true, providers, providerInfos: infos };
}

export type LlmTestResult =
  | { ok: true; success: boolean; message: string; preview?: string | null }
  | { ok: false; error?: string };

export async function llmTest(llm: Record<string, unknown>): Promise<LlmTestResult> {
  const r = await callRpc("llm.test", { llm });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "llm.test failed" };
  }
  return {
    ok: true,
    success: Boolean(r.payload.success),
    message: String(r.payload.message || ""),
    preview: typeof r.payload.preview === "string" ? (r.payload.preview as string) : null,
  };
}

function newRpcId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `rpc-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

export async function callRpc(
  method: string,
  params: Record<string, unknown>
): Promise<RpcResult> {
  const base = getGatewayBaseUrl();
  const body = JSON.stringify({
    id: newRpcId(),
    method,
    params,
  });
  try {
    const res = await fetch(`${base}/rpc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      cache: "no-store",
    });
    if (!res.ok) {
      return {
        ok: false,
        error: {
          code: "http_error",
          message: `HTTP ${res.status}: ${res.statusText}`,
        },
      };
    }
    return (await res.json()) as RpcResult;
  } catch (e) {
    const msg =
      e instanceof TypeError && String(e.message).includes("fetch")
        ? "Network error: gateway unreachable (check NEXT_PUBLIC_GATEWAY_URL and that the gateway is running)"
        : e instanceof Error
          ? e.message
          : String(e);
    return {
      ok: false,
      error: { code: "network_error", message: msg },
    };
  }
}

export type AgentWsEvent = {
  run_id: string;
  stream: string;
  data: Record<string, unknown>;
  seq?: number;
  ts?: number;
};

/** Per-agent LLM overrides (from ``agents.list``, ``api_key`` never included). */
export type ListedAgentLlm = {
  provider?: string;
  model?: string;
  base_url?: string;
  thinking_level?: string;
  /** Configured context window (tokens), when set in agent ``llm`` JSON */
  context_window?: number;
  contextWindow?: number;
};

export type ListedAgent = {
  agentId: string;
  configured?: boolean;
  agentDir?: string;
  workspaceDir?: string;
  sessionsFile?: string;
  /** Basename under ``/icons/headers/`` (desktop UI). */
  avatar?: string;
  /** Per-agent skill name allowlist (intersected with global ``skills.filter``). */
  skills?: string[] | null;
  /** Omitted ``api_key`` for safety; use ``llmApiKeyConfigured`` when needed. */
  llm?: ListedAgentLlm;
  llmApiKeyConfigured?: boolean;
  createdAt?: number;
  updatedAt?: number;
  runStatus?: {
    state?: string;
    activeRuns?: number;
    lastRun?: unknown;
  };
};

export type ListAgentsResult =
  | { ok: true; agents: ListedAgent[] }
  | { ok: false; error?: string };

export async function listAgents(): Promise<ListAgentsResult> {
  const r = await callRpc("agents.list", {});
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.list failed" };
  }
  const agents = (r.payload.agents as ListedAgent[]) ?? [];
  return { ok: true, agents };
}

export type AgentsUpdateSkillsResult =
  | { ok: true; agentId: string; skills: string[] | null }
  | { ok: false; error?: string };

/** Set per-agent ``skills`` in ``agent.json``. Pass ``null`` to remove override (inherit global-only). */
export async function agentsUpdateSkills(
  agentId: string,
  skills: string[] | null
): Promise<AgentsUpdateSkillsResult> {
  const r = await callRpc("agents.update_skills", {
    agentId: (agentId || "").trim() || "main",
    skills,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.update_skills failed" };
  }
  const p = r.payload as { agentId?: string; skills?: string[] | null };
  return {
    ok: true,
    agentId: String(p.agentId ?? agentId),
    skills: p.skills === undefined ? null : p.skills,
  };
}

export type AgentLlmUsageStats = {
  promptTokensTotal?: number;
  completionTokensTotal?: number;
  totalTokensTotal?: number;
  numRequests?: number;
};

export type StatsAgentListRow = {
  agentId: string;
  path: string;
  llmUsage?: AgentLlmUsageStats | null;
  updatedAtMs?: number;
};

export type StatsAgentsListResult =
  | { ok: true; agents: StatsAgentListRow[] }
  | { ok: false; error?: string };

export async function statsAgentsList(): Promise<StatsAgentsListResult> {
  const r = await callRpc("stats.agents.list", {});
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "stats.agents.list failed" };
  }
  const agents = (r.payload.agents as StatsAgentListRow[]) ?? [];
  return { ok: true, agents };
}

export type StatsAgentGetResult =
  | { ok: true; agentId: string; path: string; stats: Record<string, unknown> }
  | { ok: false; error?: string };

export async function statsAgentGet(agentId: string): Promise<StatsAgentGetResult> {
  const r = await callRpc("stats.agent.get", { agentId: (agentId || "").trim() || "main" });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "stats.agent.get failed" };
  }
  return {
    ok: true,
    agentId: String(r.payload.agentId ?? agentId),
    path: String(r.payload.path ?? ""),
    stats: (r.payload.stats as Record<string, unknown>) ?? {},
  };
}

export type AgentSessionHistoryMessage = {
  role: "user" | "assistant";
  text: string;
};

export type AgentSessionHistoryResult =
  | {
      ok: true;
      sessionId: string | null;
      messages: AgentSessionHistoryMessage[];
    }
  | { ok: false; error?: string };

/** Load latest desktop session transcript for an agent (continue chat from My Agents). */
export async function getAgentSessionHistory(
  agentId: string,
  sessionKey = "desktop-app"
): Promise<AgentSessionHistoryResult> {
  const r = await callRpc("agent.session.history", {
    agentId: (agentId || "").trim() || "main",
    sessionKey: (sessionKey || "").trim() || "desktop-app",
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agent.session.history failed" };
  }
  const sessionId =
    typeof r.payload.sessionId === "string" ? r.payload.sessionId.trim() || null : null;
  const raw = r.payload.messages;
  const messages: AgentSessionHistoryMessage[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (!item || typeof item !== "object") continue;
      const role = (item as { role?: string }).role;
      const text = (item as { text?: string }).text;
      if (role !== "user" && role !== "assistant") continue;
      if (typeof text !== "string") continue;
      messages.push({ role, text });
    }
  }
  return { ok: true, sessionId, messages };
}

export type ListedSkill = {
  name: string;
  source?: string;
  description?: string;
  location?: string;
};

export type ListSkillsResult =
  | {
      ok: true;
      skills: ListedSkill[];
      count: number;
      version?: string;
      sources: { name: string; count: number }[];
      filteredOut?: string[];
      promptTruncated?: boolean;
      promptCompact?: boolean;
    }
  | { ok: false; error?: string };

export async function listSkills(
  workspaceDir?: string
): Promise<ListSkillsResult> {
  const params: Record<string, unknown> = {};
  if (workspaceDir?.trim()) {
    params.workspaceDir = workspaceDir.trim();
  }
  const r = await callRpc("skills.list", params);
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "skills.list failed" };
  }
  const p = r.payload;
  return {
    ok: true,
    skills: (p.skills as ListedSkill[]) ?? [],
    count: Number(p.count ?? 0),
    version: typeof p.version === "string" ? p.version : undefined,
    sources: (p.sources as { name: string; count: number }[]) ?? [],
    filteredOut: (p.filteredOut as string[]) ?? [],
    promptTruncated: Boolean(p.promptTruncated),
    promptCompact: Boolean(p.promptCompact),
  };
}

export type ResolveAgentDefaultsResult =
  | { ok: true; agentId: string; agentDir: string; workspaceDir: string }
  | { ok: false; error?: string };

export async function resolveAgentDefaults(
  agentId: string
): Promise<ResolveAgentDefaultsResult> {
  const r = await callRpc("agents.resolve_defaults", {
    agentId: agentId.trim() || "new-agent",
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message };
  }
  const p = r.payload;
  return {
    ok: true,
    agentId: String(p.agentId ?? ""),
    agentDir: String(p.agentDir ?? ""),
    workspaceDir: String(p.workspaceDir ?? ""),
  };
}

export type CreateAgentBody = {
  agentId: string;
  workspaceDir?: string;
  /** Basename only; file must exist under ``public/icons/headers``. */
  avatar?: string;
  llm?: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    thinking_level?: string;
  };
};

export type CreateAgentResult =
  | { ok: true; agentId: string; agentDir: string; workspaceDir: string }
  | { ok: false; error?: string };

export async function createAgent(body: CreateAgentBody): Promise<CreateAgentResult> {
  const params: Record<string, unknown> = {
    agentId: body.agentId.trim(),
  };
  if (body.workspaceDir?.trim()) {
    params.workspaceDir = body.workspaceDir.trim();
  }
  if (body.llm && Object.keys(body.llm).length > 0) {
    params.llm = body.llm;
  }
  if (body.avatar?.trim()) {
    params.avatar = body.avatar.trim();
  }
  const r = await callRpc("agents.create", params);
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.create failed" };
  }
  const p = r.payload;
  return {
    ok: true,
    agentId: String(p.agentId ?? ""),
    agentDir: String(p.agentDir ?? ""),
    workspaceDir: String(p.workspaceDir ?? ""),
  };
}

export type SetAgentAvatarResult =
  | { ok: true; agentId: string; avatar?: string }
  | { ok: false; error?: string };

/** Set or clear (empty string) per-agent avatar basename. */
export type UpdateAgentLlmBody = {
  llm: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    thinking_level?: string;
  };
};

export type UpdateAgentLlmResult =
  | { ok: true; agentId: string; llm?: ListedAgentLlm; llmApiKeyConfigured?: boolean }
  | { ok: false; error?: string };

/** Patch per-agent LLM (empty string clears a field; omit ``api_key`` to keep stored key). */
export async function updateAgentLlm(
  agentId: string,
  body: UpdateAgentLlmBody
): Promise<UpdateAgentLlmResult> {
  const r = await callRpc("agents.update_llm", {
    agentId: agentId.trim(),
    llm: body.llm,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.update_llm failed" };
  }
  const p = r.payload;
  const raw = p.llm;
  const llm =
    raw && typeof raw === "object"
      ? (raw as ListedAgentLlm)
      : undefined;
  return {
    ok: true,
    agentId: String(p.agentId ?? ""),
    llm,
    llmApiKeyConfigured: Boolean(p.llmApiKeyConfigured),
  };
}

export async function setAgentAvatar(
  agentId: string,
  avatar: string
): Promise<SetAgentAvatarResult> {
  const r = await callRpc("agents.set_avatar", {
    agentId: agentId.trim(),
    avatar: avatar ?? "",
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.set_avatar failed" };
  }
  const p = r.payload;
  const av = p.avatar;
  return {
    ok: true,
    agentId: String(p.agentId ?? ""),
    avatar:
      av !== undefined && av !== null && String(av).trim()
        ? String(av).trim()
        : undefined,
  };
}

export type DeleteAgentResult =
  | { ok: true; deleted: boolean }
  | { ok: false; error?: string };

export async function deleteAgent(agentId: string): Promise<DeleteAgentResult> {
  const r = await callRpc("agents.delete", { agentId: agentId.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.delete failed" };
  }
  return { ok: true, deleted: Boolean(r.payload.deleted) };
}

export type ReadAgentWorkspaceFileResult =
  | { ok: true; path: string; text: string; missing: boolean }
  | { ok: false; error?: string };

/** Allowed relative paths: memory.md, SOUL.md, AGENTS.md (gateway normalizes case if a file exists). */
export async function readAgentWorkspaceFile(
  agentId: string,
  path: string
): Promise<ReadAgentWorkspaceFileResult> {
  const p = path.trim();
  const r = await callRpc("agents.workspace_file.read", {
    agentId: agentId.trim(),
    path: p,
    name: p,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.workspace_file.read failed" };
  }
  return {
    ok: true,
    path: String(r.payload.path ?? path),
    text: String(r.payload.text ?? ""),
    missing: Boolean(r.payload.missing),
  };
}

export type WriteAgentWorkspaceFileResult =
  | { ok: true; path: string; saved: boolean }
  | { ok: false; error?: string };

export async function writeAgentWorkspaceFile(
  agentId: string,
  path: string,
  text: string
): Promise<WriteAgentWorkspaceFileResult> {
  const p = path.trim();
  const r = await callRpc("agents.workspace_file.write", {
    agentId: agentId.trim(),
    path: p,
    name: p,
    text,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "agents.workspace_file.write failed" };
  }
  return {
    ok: true,
    path: String(r.payload.path ?? path),
    saved: Boolean(r.payload.saved),
  };
}

/** Team ``AGENTS.md`` at orchestration root; path allowlist matches gateway. */
export type ReadOrchestrateWorkspaceFileResult =
  | { ok: true; path: string; text: string; missing: boolean }
  | { ok: false; error?: string };

export async function readOrchestrateWorkspaceFile(
  orchId: string,
  path: string
): Promise<ReadOrchestrateWorkspaceFileResult> {
  const r = await callRpc("orchestrate.workspace_file.read", {
    orchId: orchId.trim(),
    path: path.trim(),
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.workspace_file.read failed" };
  }
  return {
    ok: true,
    path: String(r.payload.path ?? path),
    text: String(r.payload.text ?? ""),
    missing: Boolean(r.payload.missing),
  };
}

export type WriteOrchestrateWorkspaceFileResult =
  | { ok: true; path: string; saved: boolean }
  | { ok: false; error?: string };

export async function writeOrchestrateWorkspaceFile(
  orchId: string,
  path: string,
  text: string
): Promise<WriteOrchestrateWorkspaceFileResult> {
  const r = await callRpc("orchestrate.workspace_file.write", {
    orchId: orchId.trim(),
    path: path.trim(),
    text,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.workspace_file.write failed" };
  }
  return {
    ok: true,
    path: String(r.payload.path ?? path),
    saved: Boolean(r.payload.saved),
  };
}

export type ListLlmProvidersResult =
  | { ok: true; providers: string[] }
  | { ok: false; error?: string };

export async function listLlmProviders(): Promise<ListLlmProvidersResult> {
  const r = await callRpc("llm.providers.list", {});
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message };
  }
  return { ok: true, providers: (r.payload.providers as string[]) ?? [] };
}

export type TestLlmConnectionResult =
  | {
      ok: true;
      success: boolean;
      message: string;
      preview?: string | null;
    }
  | { ok: false; error?: string };

/** Minimal chat completion against current form fields; optional agentId merges saved API key for testing. */
export async function testLlmConnection(body: {
  llm: Record<string, string>;
  agentId?: string;
}): Promise<TestLlmConnectionResult> {
  const params: Record<string, unknown> = { llm: body.llm };
  if (body.agentId?.trim()) {
    params.agentId = body.agentId.trim();
  }
  const r = await callRpc("llm.test", params);
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "llm.test failed" };
  }
  const p = r.payload;
  const prev = p.preview;
  return {
    ok: true,
    success: Boolean(p.success),
    message: String(p.message ?? ""),
    preview: typeof prev === "string" ? prev : undefined,
  };
}

export type OrchMessage = {
  id: string;
  ts: number;
  round: number;
  speaker: string;
  role: "user" | "assistant";
  text: string;
  /** DAG 节点 id（strategy=dag 时助手消息可能带此字段） */
  nodeId?: string;
  /** 该条助手消息对应一次 agent 完成的 token 用量（网关 runner meta） */
  usage?: { input?: number; output?: number; total?: number };
};

/** 与网关 `orchestrate.create` / `orch.json` 中 dag 字段一致；节点可含 `position` 供后续画布编辑 */
export type OrchestrateDagSpec = {
  nodes: Array<{
    id: string;
    agentId: string;
    title?: string;
    dependsOn?: string[];
    position?: { x: number; y: number };
  }>;
  parallelism?: number;
};

/** Orchestration user-visible reply language (gateway persists ``orchReplyLanguage``). */
export type OrchestrateReplyLanguage = "auto" | "zh" | "en";

export type OrchestrateRunBody = {
  sessionKey: string;
  name?: string;
  message: string;
  participants: string[];
  maxRounds?: number;
  strategy?: string;
  dag?: OrchestrateDagSpec;
  routerLlm?: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    thinking_level?: string;
  };
  /** Per-agent role / identity for router LLM (strategy ``router_llm``). */
  routerAgentRoles?: Record<string, string>;
  supervisorPipeline?: string[];
  supervisorLlm?: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    thinking_level?: string;
  };
  supervisorMaxIterations?: number;
  supervisorLlmMaxRetries?: number;
  /** ``auto`` = follow user language; ``zh`` / ``en`` = force. */
  orchReplyLanguage?: OrchestrateReplyLanguage;
  /** Persist tool/LLM trace to ``trace.jsonl`` when true. */
  orchTraceEnabled?: boolean;
  idempotencyKey: string;
};

export type OrchestrateRunResult =
  | { ok: true; orchId: string; status: string; sessionKey: string }
  | { ok: false; error?: string };

export async function orchestrateRun(
  body: OrchestrateRunBody
): Promise<OrchestrateRunResult> {
  const r = await callRpc("orchestrate.run", {
    sessionKey: body.sessionKey,
    name: body.name,
    message: body.message,
    participants: body.participants,
    maxRounds: body.maxRounds,
    strategy: body.strategy,
    dag: body.dag,
    routerLlm: body.routerLlm,
    routerAgentRoles: body.routerAgentRoles,
    supervisorPipeline: body.supervisorPipeline,
    supervisorLlm: body.supervisorLlm,
    supervisorMaxIterations: body.supervisorMaxIterations,
    supervisorLlmMaxRetries: body.supervisorLlmMaxRetries,
    ...(body.orchReplyLanguage != null ? { orchReplyLanguage: body.orchReplyLanguage } : {}),
    ...(body.orchTraceEnabled === true ? { orchTraceEnabled: true } : {}),
    idempotencyKey: body.idempotencyKey,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.run failed" };
  }
  return {
    ok: true,
    orchId: String(r.payload.orchId ?? ""),
    status: String(r.payload.status ?? ""),
    sessionKey: String(r.payload.sessionKey ?? body.sessionKey),
  };
}

export type OrchestrateCreateBody = {
  sessionKey: string;
  name?: string;
  participants: string[];
  maxRounds?: number;
  strategy?: string;
  dag?: OrchestrateDagSpec;
  routerLlm?: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    thinking_level?: string;
  };
  routerAgentRoles?: Record<string, string>;
  /** Order = pipeline A→B→C for ``strategy: supervisor_pipeline`` */
  supervisorPipeline?: string[];
  supervisorLlm?: {
    provider?: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    thinking_level?: string;
  };
  supervisorMaxIterations?: number;
  /** Retries after a failed/empty supervisor HTTP call; 10s between attempts. */
  supervisorLlmMaxRetries?: number;
  orchReplyLanguage?: OrchestrateReplyLanguage;
  orchTraceEnabled?: boolean;
  /** Absolute path on the gateway host; all agents use it as tool cwd when set. */
  orchWorkspaceRoot?: string;
  idempotencyKey: string;
};

export type OrchestrateCreateResult =
  | { ok: true; orchId: string; status: string; sessionKey: string }
  | { ok: false; error?: string };

export async function orchestrateCreate(
  body: OrchestrateCreateBody
): Promise<OrchestrateCreateResult> {
  const r = await callRpc("orchestrate.create", {
    sessionKey: body.sessionKey,
    name: body.name,
    participants: body.participants,
    maxRounds: body.maxRounds,
    strategy: body.strategy,
    dag: body.dag,
    routerLlm: body.routerLlm,
    routerAgentRoles: body.routerAgentRoles,
    supervisorPipeline: body.supervisorPipeline,
    supervisorLlm: body.supervisorLlm,
    supervisorMaxIterations: body.supervisorMaxIterations,
    supervisorLlmMaxRetries: body.supervisorLlmMaxRetries,
    ...(body.orchReplyLanguage != null ? { orchReplyLanguage: body.orchReplyLanguage } : {}),
    ...(body.orchTraceEnabled === true ? { orchTraceEnabled: true } : {}),
    ...(body.orchWorkspaceRoot != null && String(body.orchWorkspaceRoot).trim()
      ? { orchWorkspaceRoot: String(body.orchWorkspaceRoot).trim() }
      : {}),
    idempotencyKey: body.idempotencyKey,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.create failed" };
  }
  return {
    ok: true,
    orchId: String(r.payload.orchId ?? ""),
    status: String(r.payload.status ?? ""),
    sessionKey: String(r.payload.sessionKey ?? body.sessionKey),
  };
}

export type OrchestrateUpdateBody = OrchestrateCreateBody & { orchId: string };

export type OrchestrateUpdateResult =
  | { ok: true; orchId: string; status: string; sessionKey: string }
  | { ok: false; error?: string };

export async function orchestrateUpdate(
  body: OrchestrateUpdateBody
): Promise<OrchestrateUpdateResult> {
  const r = await callRpc("orchestrate.update", {
    orchId: body.orchId.trim(),
    sessionKey: body.sessionKey,
    name: body.name,
    participants: body.participants,
    maxRounds: body.maxRounds,
    strategy: body.strategy,
    dag: body.dag,
    routerLlm: body.routerLlm,
    routerAgentRoles: body.routerAgentRoles,
    supervisorPipeline: body.supervisorPipeline,
    supervisorLlm: body.supervisorLlm,
    supervisorMaxIterations: body.supervisorMaxIterations,
    supervisorLlmMaxRetries: body.supervisorLlmMaxRetries,
    ...(body.orchReplyLanguage != null ? { orchReplyLanguage: body.orchReplyLanguage } : {}),
    ...(body.orchTraceEnabled != null ? { orchTraceEnabled: body.orchTraceEnabled } : {}),
    idempotencyKey: body.idempotencyKey,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.update failed" };
  }
  return {
    ok: true,
    orchId: String(r.payload.orchId ?? ""),
    status: String(r.payload.status ?? ""),
    sessionKey: String(r.payload.sessionKey ?? body.sessionKey),
  };
}

export type OrchestrateListItem = {
  orchId: string;
  name?: string;
  status: string;
  sessionKey: string;
  strategy?: string;
  maxRounds?: number;
  participants?: string[];
  currentRound?: number;
  createdAt?: number;
  updatedAt?: number;
  error?: string;
  orchReplyLanguage?: OrchestrateReplyLanguage;
  orchTraceEnabled?: boolean;
};

export type OrchestrateListResult =
  | { ok: true; orchestrations: OrchestrateListItem[] }
  | { ok: false; error?: string };

export async function orchestrateList(): Promise<OrchestrateListResult> {
  const r = await callRpc("orchestrate.list", {});
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.list failed" };
  }
  return {
    ok: true,
    orchestrations: (r.payload.orchestrations as OrchestrateListItem[]) ?? [],
  };
}

export type OrchestrateDeleteResult =
  | { ok: true; deleted: boolean }
  | { ok: false; error?: string };

export async function orchestrateDelete(orchId: string): Promise<OrchestrateDeleteResult> {
  const r = await callRpc("orchestrate.delete", { orchId: orchId.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.delete failed" };
  }
  return { ok: true, deleted: Boolean(r.payload.deleted) };
}

export type OrchestrateResetResult =
  | { ok: true; orchId: string; status: string; sessionKey: string; currentRound: number }
  | { ok: false; error?: string };

/** Clear orchestration transcript and agent session ids (rewrites orch.json); removes trace.jsonl. */
export async function orchestrateReset(orchId: string): Promise<OrchestrateResetResult> {
  const r = await callRpc("orchestrate.reset", { orchId: orchId.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.reset failed" };
  }
  const p = r.payload;
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    status: String(p.status ?? ""),
    sessionKey: String(p.sessionKey ?? ""),
    currentRound: Number(p.currentRound ?? 0),
  };
}

export type OrchestrateInspectSkillRow = {
  name?: string;
  source?: string;
  description?: string;
};

export type OrchestrateInspectAgentRow = {
  agentId: string;
  tools: string[];
  skills: OrchestrateInspectSkillRow[];
  skillsCount?: number;
  skillsPromptCount?: number;
  skillsPromptTruncated?: boolean;
  skillsPromptCompact?: boolean;
};

export type OrchestrateInspectResult =
  | { ok: true; orchId: string; agents: OrchestrateInspectAgentRow[] }
  | { ok: false; error?: string };

/** List effective tools + skills per orchestration participant (read-only audit). */
export async function orchestrateInspectAgents(orchId: string): Promise<OrchestrateInspectResult> {
  const r = await callRpc("orchestrate.inspect_agents", { orchId: orchId.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.inspect_agents failed" };
  }
  const p = r.payload as { orchId?: string; agents?: OrchestrateInspectAgentRow[] };
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    agents: Array.isArray(p.agents) ? p.agents : [],
  };
}

export type OrchestrateDumpResult =
  | {
      ok: true;
      orchId: string;
      filename: string;
      zipBase64: string;
      sizeBytes: number;
      encrypted?: boolean;
    }
  | { ok: false; error?: string; errorCode?: string };

/** ZIP export: orch state (redacted by default), team MDs, per-agent workspaces, tools/skills JSON. Pass ``password`` to export full secrets inside an AES-GCM encrypted ``.orchbundle``. */
export async function orchestrateDump(
  orchId: string,
  password?: string
): Promise<OrchestrateDumpResult> {
  const r = await callRpc("orchestrate.dump", {
    orchId: orchId.trim(),
    ...(password ? { password } : {}),
  });
  if (!r.ok || !r.payload) {
    return {
      ok: false,
      error: r.error?.message || "orchestrate.dump failed",
      errorCode: r.error?.code,
    };
  }
  const p = r.payload as {
    orchId?: string;
    filename?: string;
    zipBase64?: string;
    sizeBytes?: number;
    encrypted?: boolean;
  };
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    filename: String(p.filename ?? "orch-dump.zip"),
    zipBase64: String(p.zipBase64 ?? ""),
    sizeBytes: Number(p.sizeBytes ?? 0),
    encrypted: Boolean(p.encrypted),
  };
}

export type OrchestrateImportBundleResult =
  | {
      ok: true;
      orchId: string;
      name: string;
      sessionKey: string;
      status: string;
      participants: string[];
    }
  | { ok: false; error?: string; errorCode?: string };

/** Import a bundle (plain ZIP or password-encrypted ``.orchbundle``) as a new orchestration. */
export async function orchestrateImportBundle(
  zipBase64: string,
  options?: { password?: string; restoreHomeWorkspace?: boolean }
): Promise<OrchestrateImportBundleResult> {
  const r = await callRpc("orchestrate.import_bundle", {
    zipBase64,
    password: options?.password ?? "",
    restoreHomeWorkspace: options?.restoreHomeWorkspace === true,
  });
  if (!r.ok || !r.payload) {
    return {
      ok: false,
      error: r.error?.message || "orchestrate.import_bundle failed",
      errorCode: r.error?.code,
    };
  }
  const p = r.payload as {
    orchId?: string;
    name?: string;
    sessionKey?: string;
    status?: string;
    participants?: string[];
  };
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    name: String(p.name ?? ""),
    sessionKey: String(p.sessionKey ?? ""),
    status: String(p.status ?? ""),
    participants: Array.isArray(p.participants) ? p.participants : [],
  };
}

/** Large file → base64 without stack overflow (client components only). */
export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunk = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/** Browser download for ``orchestrateDump`` payload (client components only). */
export function downloadZipFromBase64(zipBase64: string, filename: string): void {
  if (typeof window === "undefined") return;
  const binaryString = atob(zipBase64);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
  const mime = filename.endsWith(".orchbundle") ? "application/octet-stream" : "application/zip";
  const blob = new Blob([bytes], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.replace(/[/\\]/g, "_");
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export type OrchestrateRouterLlmPublic = {
  provider?: string;
  model?: string;
  base_url?: string;
  thinking_level?: string;
};

export type OrchestrateGetResult =
  | {
      ok: true;
      orchId: string;
      sessionKey: string;
      status: string;
      currentRound: number;
      maxRounds: number;
      participants: string[];
      messages: OrchMessage[];
      name?: string;
      strategy?: string;
      error?: string;
      createdAt?: number;
      updatedAt?: number;
      orchSchemaVersion?: number;
      dagSpec?: OrchestrateDagSpec | Record<string, unknown> | null;
      dagProgress?: Record<string, { status?: string; outputPreview?: string; error?: string }> | null;
      dagParallelism?: number;
      /** Excludes ``api_key``; use ``routerApiKeyConfigured`` when editing. */
      routerLlm?: OrchestrateRouterLlmPublic | null;
      routerApiKeyConfigured?: boolean;
      /** agentId -> role description for router (strategy ``router_llm``). */
      routerAgentRoles?: Record<string, string>;
      supervisorPipeline?: string[];
      supervisorMaxIterations?: number;
      supervisorLlmMaxRetries?: number;
      supervisorIteration?: number;
      supervisorLastDecision?: Record<string, unknown> | null;
      supervisorLlm?: OrchestrateRouterLlmPublic | null;
      supervisorApiKeyConfigured?: boolean;
      orchReplyLanguage?: OrchestrateReplyLanguage;
      orchTraceEnabled?: boolean;
      orchTraceSeq?: number;
      orchWorkspaceRoot?: string;
    }
  | { ok: false; error?: string };

export async function orchestrateGet(orchId: string): Promise<OrchestrateGetResult> {
  const r = await callRpc("orchestrate.get", { orchId: orchId.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.get failed" };
  }
  const p = r.payload;
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    sessionKey: String(p.sessionKey ?? ""),
    status: String(p.status ?? ""),
    currentRound: Number(p.currentRound ?? 0),
    maxRounds: Number(p.maxRounds ?? 0),
    participants: (p.participants as string[]) ?? [],
    messages: (p.messages as OrchMessage[]) ?? [],
    name: typeof p.name === "string" ? p.name : undefined,
    strategy: typeof p.strategy === "string" ? p.strategy : undefined,
    error: typeof p.error === "string" ? p.error : undefined,
    createdAt: typeof p.createdAt === "number" ? p.createdAt : undefined,
    updatedAt: typeof p.updatedAt === "number" ? p.updatedAt : undefined,
    orchSchemaVersion:
      typeof p.orchSchemaVersion === "number" ? p.orchSchemaVersion : undefined,
    dagSpec: p.dagSpec != null ? (p.dagSpec as OrchestrateDagSpec) : undefined,
    dagProgress:
      p.dagProgress != null
        ? (p.dagProgress as Record<
            string,
            { status?: string; outputPreview?: string; error?: string }
          >)
        : undefined,
    dagParallelism:
      typeof p.dagParallelism === "number" ? p.dagParallelism : undefined,
    routerLlm:
      p.routerLlm != null && typeof p.routerLlm === "object"
        ? (p.routerLlm as OrchestrateRouterLlmPublic)
        : undefined,
    routerApiKeyConfigured: Boolean(p.routerApiKeyConfigured),
    routerAgentRoles:
      p.routerAgentRoles != null && typeof p.routerAgentRoles === "object"
        ? (p.routerAgentRoles as Record<string, string>)
        : undefined,
    supervisorPipeline: Array.isArray(p.supervisorPipeline)
      ? (p.supervisorPipeline as string[])
      : undefined,
    supervisorMaxIterations:
      typeof p.supervisorMaxIterations === "number"
        ? p.supervisorMaxIterations
        : undefined,
    supervisorLlmMaxRetries:
      typeof p.supervisorLlmMaxRetries === "number"
        ? p.supervisorLlmMaxRetries
        : undefined,
    supervisorIteration:
      typeof p.supervisorIteration === "number" ? p.supervisorIteration : undefined,
    supervisorLastDecision:
      p.supervisorLastDecision != null && typeof p.supervisorLastDecision === "object"
        ? (p.supervisorLastDecision as Record<string, unknown>)
        : undefined,
    supervisorLlm:
      p.supervisorLlm != null && typeof p.supervisorLlm === "object"
        ? (p.supervisorLlm as OrchestrateRouterLlmPublic)
        : undefined,
    supervisorApiKeyConfigured: Boolean(p.supervisorApiKeyConfigured),
    orchReplyLanguage: ((): OrchestrateReplyLanguage | undefined => {
      const raw = p.orchReplyLanguage;
      if (typeof raw !== "string") return undefined;
      const s = raw.trim().toLowerCase();
      if (s === "zh" || s === "en" || s === "auto") return s as OrchestrateReplyLanguage;
      return undefined;
    })(),
    orchTraceEnabled: Boolean(p.orchTraceEnabled),
    orchTraceSeq: typeof p.orchTraceSeq === "number" ? p.orchTraceSeq : Number(p.orchTraceSeq ?? 0) || 0,
    orchWorkspaceRoot:
      p.orchWorkspaceRoot != null && String(p.orchWorkspaceRoot).trim()
        ? String(p.orchWorkspaceRoot).trim()
        : undefined,
  };
}

export type OrchestrateWorkspaceScanEntry = {
  relPath: string;
  isDir: boolean;
  mtimeMs: number;
  size: number;
};

export type OrchestrateWorkspaceScanResult =
  | { ok: true; orchId: string; root: string | null; entries: OrchestrateWorkspaceScanEntry[]; truncated: boolean }
  | { ok: false; error?: string };

export async function orchestrateWorkspaceScan(orchId: string): Promise<OrchestrateWorkspaceScanResult> {
  const r = await callRpc("orchestrate.workspace.scan", { orchId: orchId.trim() });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.workspace.scan failed" };
  }
  const p = r.payload as {
    orchId?: string;
    root?: string | null;
    entries?: OrchestrateWorkspaceScanEntry[];
    truncated?: boolean;
  };
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    root: p.root != null && String(p.root).trim() ? String(p.root) : null,
    entries: Array.isArray(p.entries) ? p.entries : [],
    truncated: Boolean(p.truncated),
  };
}

export type OrchestrateWorkspaceRootSetResult =
  | { ok: true; orchId: string; orchWorkspaceRoot?: string }
  | { ok: false; error?: string };

/** Set or clear shared orchestration workspace directory (gateway host path). Refuses while running. */
export async function orchestrateWorkspaceRootSet(
  orchId: string,
  workspaceRoot: string | null | undefined
): Promise<OrchestrateWorkspaceRootSetResult> {
  const trimmed = (workspaceRoot ?? "").trim();
  const r = await callRpc("orchestrate.workspace_root.set", {
    orchId: orchId.trim(),
    orchWorkspaceRoot: trimmed,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.workspace_root.set failed" };
  }
  const p = r.payload as { orchId?: string; orchWorkspaceRoot?: string };
  return {
    ok: true,
    orchId: String(p.orchId ?? ""),
    orchWorkspaceRoot:
      p.orchWorkspaceRoot != null && String(p.orchWorkspaceRoot).trim()
        ? String(p.orchWorkspaceRoot).trim()
        : undefined,
  };
}

/** One JSONL row from ``orchestrate.trace.list`` / ``trace.jsonl``. */
export type OrchTraceEvent = Record<string, unknown>;

export type OrchestrateTraceListResult =
  | { ok: true; orchId: string; events: OrchTraceEvent[] }
  | { ok: false; error?: string };

export async function orchestrateTraceList(
  orchId: string,
  afterSeq: number,
  limit = 200
): Promise<OrchestrateTraceListResult> {
  const a = Math.floor(afterSeq);
  const afterSeqRpc = Number.isFinite(a) ? a : -1;
  const r = await callRpc("orchestrate.trace.list", {
    orchId: orchId.trim(),
    afterSeq: afterSeqRpc,
    limit: Math.max(1, Math.min(2000, Math.floor(limit))),
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.trace.list failed" };
  }
  const ev = r.payload.events;
  return {
    ok: true,
    orchId: String(r.payload.orchId ?? orchId),
    events: Array.isArray(ev) ? (ev as OrchTraceEvent[]) : [],
  };
}

export type OrchestrateSendResult =
  | { ok: true; orchId: string; status: string; currentRound: number }
  | { ok: false; error?: string };

export async function orchestrateSend(
  orchId: string,
  message: string,
  idempotencyKey: string,
  options?: {
    reasoningLevel?: "off" | "on" | "stream";
    /** @mention target: must match orchestration participant id (round_robin / router_llm). */
    targetAgent?: string;
  }
): Promise<OrchestrateSendResult> {
  const r = await callRpc("orchestrate.send", {
    orchId: orchId.trim(),
    message,
    idempotencyKey,
    ...(options?.reasoningLevel != null
      ? { reasoningLevel: options.reasoningLevel }
      : {}),
    ...(options?.targetAgent?.trim()
      ? { targetAgent: options.targetAgent.trim() }
      : {}),
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.send failed" };
  }
  return {
    ok: true,
    orchId: String(r.payload.orchId ?? ""),
    status: String(r.payload.status ?? ""),
    currentRound: Number(r.payload.currentRound ?? 0),
  };
}

export type OrchestrateWaitResult =
  | { ok: true; orchId: string; status: string; currentRound: number }
  | { ok: false; error?: string };

export async function orchestrateWait(
  orchId: string,
  timeoutMs = 15_000
): Promise<OrchestrateWaitResult> {
  const r = await callRpc("orchestrate.wait", {
    orchId: orchId.trim(),
    timeoutMs,
  });
  if (!r.ok || !r.payload) {
    return { ok: false, error: r.error?.message || "orchestrate.wait failed" };
  }
  return {
    ok: true,
    orchId: String(r.payload.orchId ?? ""),
    status: String(r.payload.status ?? ""),
    currentRound: Number(r.payload.currentRound ?? 0),
  };
}
