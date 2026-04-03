"use client";

import Image from "next/image";
import dynamic from "next/dynamic";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import {
  listAgents,
  listLlmProviders,
  orchestrateCreate,
  orchestrateDelete,
  orchestrateGet,
  orchestrateList,
  orchestrateSend,
  orchestrateUpdate,
  readOrchestrateWorkspaceFile,
  writeOrchestrateWorkspaceFile,
  testLlmConnection,
  type ListedAgent,
  type AgentWsEvent,
  type OrchMessage,
  type OrchestrateDagSpec,
  type OrchestrateListItem,
  type OrchestrateReplyLanguage,
} from "@/lib/gateway";
import { specHasCycle } from "@/lib/orchestrateDagFlow";
import { parseOrchestrateTargetAgent } from "@/lib/orchestrateMention";
import { useI18n } from "@/lib/i18n";
import { ChatThinkToolCheckbox } from "@/components/ChatThinkToolCheckbox";
import { OrchestrateMentionInput } from "@/components/OrchestrateMentionInput";
import { busyFromOrchestrateStatus } from "@/lib/orchestratePollBusy";
import { useGatewayWs } from "@/lib/gateway-ws-context";

type ToolTraceRow = {
  id: string;
  name: string;
  state: "running" | "done" | "error";
  preview?: string;
  elapsedMs?: number;
};

type PlannedToolCall = { name: string; arguments_preview?: string };

type LiveRun = {
  runId: string;
  agentId?: string;
  step?: string;
  reasoning?: string;
  plannedToolCalls?: PlannedToolCall[];
  toolTraces?: ToolTraceRow[];
};

function formatToolResultPreview(r: unknown): string {
  if (r == null) return "";
  if (typeof r === "string") return r.length > 4000 ? `${r.slice(0, 4000)}…` : r;
  try {
    const s = JSON.stringify(r, null, 0);
    return s.length > 4000 ? `${s.slice(0, 4000)}…` : s;
  } catch {
    return String(r).slice(0, 4000);
  }
}

function upsertToolTrace(
  traces: ToolTraceRow[] | undefined,
  toolCallId: string,
  patch: Partial<ToolTraceRow> & { name?: string }
): ToolTraceRow[] {
  const list = traces ? [...traces] : [];
  const idx = list.findIndex((t) => t.id === toolCallId);
  const base: ToolTraceRow =
    idx >= 0
      ? list[idx]
      : {
          id: toolCallId,
          name: (patch.name || "?").trim() || "?",
          state: "running",
        };
  const next: ToolTraceRow = {
    ...base,
    ...patch,
    name: (patch.name ?? base.name).trim() || base.name,
  };
  if (idx >= 0) list[idx] = next;
  else list.push(next);
  return list;
}

const DEFAULT_DAG_SPEC: OrchestrateDagSpec = {
  nodes: [
    { id: "n1", agentId: "main", title: "Step 1", dependsOn: [] },
    { id: "n2", agentId: "main", title: "Step 2", dependsOn: ["n1"] },
  ],
  parallelism: 2,
};

function DagCanvasLoading() {
  const { t } = useI18n();
  return (
    <div className="text-xs text-[var(--muted)] py-8 text-center">{t("orchestrateDagCanvasLoading")}</div>
  );
}

const OrchestrateDagCanvas = dynamic(
  () => import("./OrchestrateDagCanvas").then((m) => m.OrchestrateDagCanvas),
  { ssr: false, loading: () => <DagCanvasLoading /> }
);

function fmtTs(ts?: number): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return String(ts);
  }
}

function speakerColorClass(speaker: string): string {
  const s = (speaker || "").trim() || "unknown";
  // Stable hash -> pick a tailwind text color.
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  const palette = [
    "text-sky-400",
    "text-emerald-400",
    "text-amber-400",
    "text-fuchsia-400",
    "text-rose-400",
    "text-indigo-400",
    "text-cyan-400",
    "text-lime-400",
    "text-orange-400",
    "text-violet-400",
  ];
  return palette[h % palette.length];
}

function speakerCardClass(speaker: string): string {
  const s = (speaker || "").trim() || "unknown";
  if (s.toLowerCase() === "user") {
    return "bg-[var(--bg)]";
  }
  // Stable hash -> pick a subtle tinted background.
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  const palette = [
    "bg-sky-500/10",
    "bg-emerald-500/10",
    "bg-amber-500/10",
    "bg-fuchsia-500/10",
    "bg-rose-500/10",
    "bg-indigo-500/10",
    "bg-cyan-500/10",
    "bg-lime-500/10",
    "bg-orange-500/10",
    "bg-violet-500/10",
  ];
  return palette[h % palette.length];
}

export function OrchestratePanel({ autoOpenKey = 0 }: { autoOpenKey?: number }) {
  const { t } = useI18n();
  const { subscribe, connectionState } = useGatewayWs();
  const [listedAgents, setListedAgents] = useState<ListedAgent[]>([]);
  const [orches, setOrches] = useState<OrchestrateListItem[]>([]);
  const [selectedOrchId, setSelectedOrchId] = useState<string>("");
  const [selected, setSelected] = useState<{
    orchId: string;
    name?: string;
    status: string;
    strategy?: string;
    participants: string[];
    messages: OrchMessage[];
    dagProgress?: Record<string, { status?: string; outputPreview?: string; error?: string }>;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [participantsOpen, setParticipantsOpen] = useState(false);
  const participantsWrapRef = useRef<HTMLDivElement | null>(null);

  const [orchFormOpen, setOrchFormOpen] = useState(false);
  const [orchFormMode, setOrchFormMode] = useState<"create" | "edit">("create");
  const [orchFormEditId, setOrchFormEditId] = useState("");
  const [orchEditSessionKey, setOrchEditSessionKey] = useState("desktop-orchestrator");
  const [createName, setCreateName] = useState("");
  const [createMaxRounds, setCreateMaxRounds] = useState("8");
  const [createStrategy, setCreateStrategy] = useState<
    "round_robin" | "router_llm" | "dag" | "supervisor_pipeline"
  >("round_robin");
  const [createOrchReplyLanguage, setCreateOrchReplyLanguage] =
    useState<OrchestrateReplyLanguage>("auto");
  const [createOrchTraceEnabled, setCreateOrchTraceEnabled] = useState(false);
  const [createDagSpec, setCreateDagSpec] = useState<OrchestrateDagSpec>(DEFAULT_DAG_SPEC);
  const [createDagJson, setCreateDagJson] = useState(() =>
    JSON.stringify(DEFAULT_DAG_SPEC, null, 2)
  );
  const [dagJsonResetKey, setDagJsonResetKey] = useState(0);
  const [dagEditorMode, setDagEditorMode] = useState<"visual" | "json">("visual");
  const dagJsonParseTimerRef = useRef<number | null>(null);
  const [createParticipants, setCreateParticipants] = useState<string[]>(["main"]);
  const [addOpen, setAddOpen] = useState(false);
  const [providers, setProviders] = useState<string[]>([]);
  const [routerProvider, setRouterProvider] = useState("");
  const [routerModel, setRouterModel] = useState("");
  const [routerBaseUrl, setRouterBaseUrl] = useState("");
  const [routerApiKey, setRouterApiKey] = useState("");
  const [routerApiKeyConfigured, setRouterApiKeyConfigured] = useState(false);
  const [routerThinking, setRouterThinking] = useState("");
  /** Per-participant role text for router_llm (synced with createParticipants). */
  const [routerAgentRoles, setRouterAgentRoles] = useState<Record<string, string>>({});
  const [supervisorProvider, setSupervisorProvider] = useState("");
  const [supervisorModel, setSupervisorModel] = useState("");
  const [supervisorBaseUrl, setSupervisorBaseUrl] = useState("");
  const [supervisorApiKey, setSupervisorApiKey] = useState("");
  const [supervisorApiKeyConfigured, setSupervisorApiKeyConfigured] = useState(false);
  const [supervisorThinking, setSupervisorThinking] = useState("");
  const [createSupervisorMaxIter, setCreateSupervisorMaxIter] = useState("5");
  const [createSupervisorLlmMaxRetries, setCreateSupervisorLlmMaxRetries] = useState("12");
  const [routerLlmTestLoading, setRouterLlmTestLoading] = useState(false);
  const [routerLlmTestBanner, setRouterLlmTestBanner] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);
  const [supervisorLlmTestLoading, setSupervisorLlmTestLoading] = useState(false);
  const [supervisorLlmTestBanner, setSupervisorLlmTestBanner] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);

  const [input, setInput] = useState("");
  const [streamReasoning, setStreamReasoning] = useState(true);
  const [orchAgentsMdOpen, setOrchAgentsMdOpen] = useState(false);
  const [orchAgentsMdText, setOrchAgentsMdText] = useState("");
  const [orchAgentsMdDirty, setOrchAgentsMdDirty] = useState(false);
  const [orchAgentsMdSaving, setOrchAgentsMdSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<LiveRun | null>(null);
  const messagesWrapRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const forceOrchScrollBottomRef = useRef(false);

  useEffect(() => {
    const orchId = selectedOrchId.trim();
    if (!orchId) {
      setLive(null);
      return;
    }
    const wantSessionKey = `orch:${orchId}`;
    return subscribe((payload: AgentWsEvent) => {
      const data = payload.data || {};
      const runId = payload.run_id || (data.run_id as string) || "";
      const stream = payload.stream;
      const sessionKeyRaw =
        (data.session_key as string) ||
        (data.sessionKey as string) ||
        "";
      if (!runId || !sessionKeyRaw || String(sessionKeyRaw).trim() !== wantSessionKey) {
        return;
      }
      const agentId = typeof data.agent_id === "string" ? data.agent_id : undefined;

      if (stream === "lifecycle") {
        const phase = String((data.phase as string) || "");
        if (phase === "start") {
          setLive({
            runId,
            agentId,
            step: t("stepThinking"),
            reasoning: "",
            plannedToolCalls: [],
            toolTraces: [],
          });
        }
        if (phase === "end" || phase === "error") {
          setLive((prev) => (prev && prev.runId === runId ? { ...prev, step: undefined } : prev));
        }
        return;
      }

      if (stream === "tool") {
        const typ = String((data.type as string) || "");
        const name = String((data.tool_name as string) || "?");
        const tcid = String((data.tool_call_id as string) || (data.toolCallId as string) || name || runId);
        if (typ === "start") {
          setLive((prev) => {
            const cur = prev && prev.runId === runId ? prev : { runId, agentId };
            return {
              ...cur,
              step: t("stepCallingTool", { name }),
              toolTraces: upsertToolTrace(cur.toolTraces, tcid, { name, state: "running" }),
            } as LiveRun;
          });
        } else if (typ === "processing") {
          const elapsed = (data.elapsed_ms as number) ?? (data.elapsedMs as number);
          const sec =
            typeof elapsed === "number" && Number.isFinite(elapsed)
              ? Math.max(0, Math.round(elapsed / 1000))
              : 0;
          setLive((prev) => {
            const cur = prev && prev.runId === runId ? prev : { runId, agentId };
            return {
              ...cur,
              step: t("chatToolRunning", { name, seconds: sec }),
              toolTraces: upsertToolTrace(cur.toolTraces, tcid, {
                name,
                state: "running",
                elapsedMs: typeof elapsed === "number" ? elapsed : undefined,
              }),
            } as LiveRun;
          });
        } else if (typ === "end") {
          const ok = (data.success as boolean) !== false;
          const preview = formatToolResultPreview(data.result);
          setLive((prev) => {
            const cur = prev && prev.runId === runId ? prev : { runId, agentId };
            return {
              ...cur,
              step: t("stepToolDone", { name }),
              toolTraces: upsertToolTrace(cur.toolTraces, tcid, {
                name,
                state: ok ? "done" : "error",
                preview: preview || undefined,
              }),
            } as LiveRun;
          });
        } else if (typ === "error") {
          const err = String((data.error as string) || "error");
          setLive((prev) => {
            const cur = prev && prev.runId === runId ? prev : { runId, agentId };
            return {
              ...cur,
              step: t("stepToolDone", { name }),
              toolTraces: upsertToolTrace(cur.toolTraces, tcid, {
                name,
                state: "error",
                preview: err,
              }),
            } as LiveRun;
          });
        }
        return;
      }

      if (stream === "llm") {
        const rawCalls = (data.tool_calls as unknown) ?? (data.toolCalls as unknown);
        if (Array.isArray(rawCalls) && rawCalls.length) {
          const planned: PlannedToolCall[] = [];
          for (const c of rawCalls) {
            if (!c || typeof c !== "object") continue;
            const o = c as Record<string, unknown>;
            planned.push({
              name: String(o.name ?? "?"),
              arguments_preview:
                typeof o.arguments_preview === "string"
                  ? o.arguments_preview
                  : typeof o.argumentsPreview === "string"
                    ? o.argumentsPreview
                    : undefined,
            });
          }
          if (planned.length) {
            setLive((prev) => {
              const cur = prev && prev.runId === runId ? prev : { runId, agentId };
              return { ...(cur as LiveRun), plannedToolCalls: planned };
            });
          }
        }
        if (data.thinking != null && String(data.thinking).trim()) {
          const chunk = String(data.thinking).trim();
          setLive((prev) => {
            const cur = prev && prev.runId === runId ? prev : { runId, agentId };
            const old = (cur as LiveRun).reasoning || "";
            return { ...(cur as LiveRun), reasoning: (old ? `${old}\n\n` : "") + chunk };
          });
        }
        return;
      }

      if (stream === "assistant") {
        if (data.reasoning != null && String(data.reasoning).trim()) {
          const chunk = String(data.reasoning).trim();
          setLive((prev) => {
            const cur = prev && prev.runId === runId ? prev : { runId, agentId };
            const old = (cur as LiveRun).reasoning || "";
            return { ...(cur as LiveRun), reasoning: (old ? `${old}\n\n` : "") + chunk };
          });
        }
      }
    });
  }, [selectedOrchId, subscribe, t]);

  const loadAgents = useCallback(async () => {
    const r = await listAgents();
    if (!r.ok) return;
    setListedAgents(r.agents);
  }, []);

  const avatarUrlForSpeaker = useCallback(
    (speaker: string) => {
      const s = (speaker || "").trim();
      if (!s || s.toLowerCase() === "user") return null;
      const row = listedAgents.find((x) => x.agentId === s);
      const av = row?.avatar?.trim();
      return av ? `/icons/headers/${encodeURIComponent(av)}` : null;
    },
    [listedAgents]
  );

  const loadOrches = useCallback(async () => {
    const r = await orchestrateList();
    if (!r.ok) return;
    setOrches(r.orchestrations);
  }, []);

  useEffect(() => {
    void loadAgents();
    void loadOrches();
  }, [loadAgents, loadOrches]);

  useEffect(() => {
    if (!orchFormOpen) return;
    void listLlmProviders().then((r) => {
      if (r.ok) setProviders(r.providers);
      else setProviders(["echo", "openai", "deepseek", "vllm", "aliyun-bailian"]);
    });
  }, [orchFormOpen]);

  const canSend = useMemo(() => input.trim().length > 0 && Boolean(selectedOrchId), [input, selectedOrchId]);

  const saveOrchAgentsMd = useCallback(async () => {
    const id = selectedOrchId.trim();
    if (!id || busy) return;
    setOrchAgentsMdSaving(true);
    setError(null);
    const r = await writeOrchestrateWorkspaceFile(id, "AGENTS.md", orchAgentsMdText);
    setOrchAgentsMdSaving(false);
    if (!r.ok) {
      setError(r.error || t("orchestrateError"));
      return;
    }
    setOrchAgentsMdDirty(false);
  }, [selectedOrchId, busy, orchAgentsMdText, t]);

  const lastListRefreshRef = useRef(0);

  useEffect(() => {
    setBusy(false);
    setLive(null);
  }, [selectedOrchId]);

  useEffect(() => {
    const orchId = selectedOrchId.trim();
    if (!orchId) {
      setOrchAgentsMdText("");
      setOrchAgentsMdDirty(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      const r = await readOrchestrateWorkspaceFile(orchId, "AGENTS.md");
      if (cancelled) return;
      if (!r.ok) {
        setOrchAgentsMdText("");
        setOrchAgentsMdDirty(false);
        return;
      }
      setOrchAgentsMdText(r.missing ? "" : r.text);
      setOrchAgentsMdDirty(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedOrchId]);

  useEffect(() => {
    const orchId = selectedOrchId.trim();
    if (!orchId) return;
    lastListRefreshRef.current = 0;
    let cancelled = false;
    const tick = async () => {
      const r = await orchestrateGet(orchId);
      if (cancelled) return;
      if (!r.ok) {
        setBusy(false);
        const msg = r.error || t("orchestrateError");
        const isNet =
          msg.toLowerCase().includes("network error") ||
          msg.toLowerCase().includes("gateway unreachable") ||
          msg.toLowerCase().includes("failed to fetch");
        setError(isNet ? t("orchestrateNetworkError") : msg);
        return;
      }
      setError(null);
      setSelected({
        orchId: r.orchId,
        name: r.name,
        status: r.status,
        strategy: r.strategy,
        participants: r.participants,
        messages: r.messages || [],
        dagProgress: r.dagProgress ?? undefined,
      });
      const now = Date.now();
      if (now - lastListRefreshRef.current > 8000) {
        lastListRefreshRef.current = now;
        void loadOrches();
      }
      setBusy(busyFromOrchestrateStatus(r.status));
    };
    void tick();
    const timer = window.setInterval(tick, 900);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [loadOrches, selectedOrchId, t]);

  // After gateway/WebSocket reconnect, immediately resync status (server may have reconciled stale ``running``).
  useEffect(() => {
    if (connectionState !== "connected") return;
    const orchId = selectedOrchId.trim();
    if (!orchId) return;
    let cancelled = false;
    void orchestrateGet(orchId).then((r) => {
      if (cancelled || !r.ok) return;
      setError(null);
      setSelected({
        orchId: r.orchId,
        name: r.name,
        status: r.status,
        strategy: r.strategy,
        participants: r.participants,
        messages: r.messages || [],
        dagProgress: r.dagProgress ?? undefined,
      });
      setBusy(busyFromOrchestrateStatus(r.status));
      void loadOrches();
    });
    return () => {
      cancelled = true;
    };
  }, [connectionState, selectedOrchId, loadOrches]);

  const addParticipant = useCallback((aid: string) => {
    const v = (aid || "").trim();
    if (!v) return;
    setCreateParticipants((prev) => (prev.includes(v) ? prev : [...prev, v]));
  }, []);

  const removeParticipant = useCallback((aid: string) => {
    setCreateParticipants((prev) => prev.filter((x) => x !== aid));
  }, []);

  const send = useCallback(async () => {
    if (!canSend || busy) return;
    setBusy(true);
    setError(null);
    const msgText = input.trim();
    const localUserMsg: OrchMessage = {
      id: `local-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      ts: Date.now(),
      round: selected?.messages?.length ? (selected.messages[selected.messages.length - 1]?.round ?? 0) : 0,
      speaker: "user",
      role: "user",
      text: msgText,
    };
    setSelected((prev) => {
      if (!prev) return prev;
      return { ...prev, messages: [...(prev.messages || []), localUserMsg] };
    });
    const idem = `orch-send-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    const parts = selected?.participants ?? [];
    const strat = (selected?.strategy || "").trim();
    const targetAgent =
      strat !== "dag" && strat !== "supervisor_pipeline"
        ? parseOrchestrateTargetAgent(msgText, parts)
        : undefined;
    try {
      const r = await orchestrateSend(selectedOrchId, msgText, idem, {
        reasoningLevel: streamReasoning ? "stream" : "off",
        ...(targetAgent ? { targetAgent } : {}),
      });
      if (!r.ok) {
        setBusy(false);
        setError(r.error || t("orchestrateError"));
        return;
      }
      setInput("");
      const g = await orchestrateGet(selectedOrchId);
      if (g.ok) {
        setSelected({
          orchId: g.orchId,
          name: g.name,
          status: g.status,
          strategy: g.strategy,
          participants: g.participants,
          messages: g.messages || [],
          dagProgress: g.dagProgress ?? undefined,
        });
        setBusy(busyFromOrchestrateStatus(g.status));
      }
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [
    busy,
    canSend,
    input,
    selected?.messages,
    selected?.participants,
    selected?.strategy,
    selectedOrchId,
    streamReasoning,
    t,
  ]);

  const handleDagSpecChange = useCallback((spec: OrchestrateDagSpec) => {
    setCreateDagSpec(spec);
    setCreateDagJson(JSON.stringify(spec, null, 2));
  }, []);

  const onDagJsonChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    const txt = e.target.value;
    setCreateDagJson(txt);
    if (dagJsonParseTimerRef.current != null) window.clearTimeout(dagJsonParseTimerRef.current);
    dagJsonParseTimerRef.current = window.setTimeout(() => {
      try {
        const parsed = JSON.parse(txt) as OrchestrateDagSpec;
        if (!parsed.nodes || !Array.isArray(parsed.nodes)) return;
        setCreateDagSpec(parsed);
        setDagJsonResetKey((k) => k + 1);
      } catch {
        /* 编辑中可能暂时无效 */
      }
    }, 400);
  }, []);

  const openCreate = useCallback(() => {
    setOrchFormMode("create");
    setOrchFormEditId("");
    setOrchEditSessionKey("desktop-orchestrator");
    setCreateName("");
    setCreateMaxRounds("8");
    setCreateStrategy("round_robin");
    setCreateDagSpec(DEFAULT_DAG_SPEC);
    setCreateDagJson(JSON.stringify(DEFAULT_DAG_SPEC, null, 2));
    setDagJsonResetKey(0);
    setDagEditorMode("visual");
    setCreateParticipants(["main"]);
    setRouterAgentRoles({});
    setRouterProvider("");
    setRouterModel("");
    setRouterBaseUrl("http://127.0.0.1:8000/v1");
    setRouterApiKey("");
    setRouterApiKeyConfigured(false);
    setRouterThinking("");
    setSupervisorProvider("");
    setSupervisorModel("");
    setSupervisorBaseUrl("http://127.0.0.1:8000/v1");
    setSupervisorApiKey("");
    setSupervisorApiKeyConfigured(false);
    setSupervisorThinking("");
    setCreateSupervisorMaxIter("5");
    setCreateSupervisorLlmMaxRetries("12");
    setCreateOrchReplyLanguage("auto");
    setCreateOrchTraceEnabled(false);
    setRouterLlmTestBanner(null);
    setRouterLlmTestLoading(false);
    setSupervisorLlmTestBanner(null);
    setSupervisorLlmTestLoading(false);
    setOrchFormOpen(true);
  }, []);

  useEffect(() => {
    if (createStrategy !== "router_llm") return;
    setRouterAgentRoles((prev) => {
      const next: Record<string, string> = {};
      for (const p of createParticipants) {
        next[p] = prev[p] ?? "";
      }
      return next;
    });
  }, [createParticipants, createStrategy]);

  const runRouterLlmTest = useCallback(async () => {
    setRouterLlmTestLoading(true);
    setRouterLlmTestBanner(null);
    const llm: Record<string, string> = {};
    if (routerProvider.trim()) llm.provider = routerProvider.trim();
    if (routerModel.trim()) llm.model = routerModel.trim();
    if (routerBaseUrl.trim()) llm.base_url = routerBaseUrl.trim();
    if (routerApiKey.trim()) llm.api_key = routerApiKey.trim();
    if (routerThinking.trim()) llm.thinking_level = routerThinking.trim();
    const r = await testLlmConnection({ llm });
    setRouterLlmTestLoading(false);
    if (!r.ok) {
      setRouterLlmTestBanner({ ok: false, text: r.error || t("agentsError") });
      return;
    }
    const detail = r.preview ? `${r.message} · ${r.preview}` : r.message;
    setRouterLlmTestBanner({ ok: r.success, text: detail });
  }, [
    routerApiKey,
    routerBaseUrl,
    routerModel,
    routerProvider,
    routerThinking,
    t,
  ]);

  const runSupervisorLlmTest = useCallback(async () => {
    setSupervisorLlmTestLoading(true);
    setSupervisorLlmTestBanner(null);
    const llm: Record<string, string> = {};
    if (supervisorProvider.trim()) llm.provider = supervisorProvider.trim();
    if (supervisorModel.trim()) llm.model = supervisorModel.trim();
    if (supervisorBaseUrl.trim()) llm.base_url = supervisorBaseUrl.trim();
    if (supervisorApiKey.trim()) llm.api_key = supervisorApiKey.trim();
    if (supervisorThinking.trim()) llm.thinking_level = supervisorThinking.trim();
    const r = await testLlmConnection({ llm });
    setSupervisorLlmTestLoading(false);
    if (!r.ok) {
      setSupervisorLlmTestBanner({ ok: false, text: r.error || t("agentsError") });
      return;
    }
    const detail = r.preview ? `${r.message} · ${r.preview}` : r.message;
    setSupervisorLlmTestBanner({ ok: r.success, text: detail });
  }, [
    supervisorApiKey,
    supervisorBaseUrl,
    supervisorModel,
    supervisorProvider,
    supervisorThinking,
    t,
  ]);

  const openEdit = useCallback(
    async (o: OrchestrateListItem) => {
      if (busyFromOrchestrateStatus(o.status)) {
        setError(t("orchestrateEditRunningError"));
        return;
      }
      setError(null);
      setRouterLlmTestBanner(null);
      setRouterLlmTestLoading(false);
      setSupervisorLlmTestBanner(null);
      setSupervisorLlmTestLoading(false);
      const r = await orchestrateGet(o.orchId);
      if (!r.ok) {
        setError(r.error || t("orchestrateError"));
        return;
      }
      const s = (r.strategy || "round_robin").trim();
      const strat: "round_robin" | "router_llm" | "dag" | "supervisor_pipeline" =
        s === "dag"
          ? "dag"
          : s === "router_llm"
            ? "router_llm"
            : s === "supervisor_pipeline"
              ? "supervisor_pipeline"
              : "round_robin";
      const dagOk =
        strat === "dag" &&
        r.dagSpec &&
        typeof r.dagSpec === "object" &&
        Array.isArray((r.dagSpec as OrchestrateDagSpec).nodes) &&
        (r.dagSpec as OrchestrateDagSpec).nodes.length > 0;
      const spec: OrchestrateDagSpec = dagOk
        ? (r.dagSpec as OrchestrateDagSpec)
        : DEFAULT_DAG_SPEC;

      setOrchFormMode("edit");
      setOrchFormEditId(r.orchId);
      setOrchEditSessionKey(r.sessionKey?.trim() || "desktop-orchestrator");
      setCreateName(r.name || "");
      setCreateMaxRounds(String(Number.isFinite(r.maxRounds) && r.maxRounds > 0 ? r.maxRounds : 8));
      setCreateStrategy(strat);
      setCreateDagSpec(spec);
      setCreateDagJson(JSON.stringify(spec, null, 2));
      setDagJsonResetKey((k) => k + 1);
      setDagEditorMode("visual");
      const pipe =
        strat === "supervisor_pipeline" &&
        r.supervisorPipeline &&
        r.supervisorPipeline.length > 0
          ? r.supervisorPipeline
          : r.participants?.length
            ? r.participants
            : ["main"];
      setCreateParticipants(pipe);
      const p1 = r.routerLlm;
      setRouterProvider(typeof p1?.provider === "string" ? p1.provider : "");
      setRouterModel(typeof p1?.model === "string" ? p1.model : "");
      setRouterBaseUrl(
        typeof p1?.base_url === "string" && p1.base_url.trim()
          ? p1.base_url
          : "http://127.0.0.1:8000/v1"
      );
      setRouterApiKey("");
      setRouterApiKeyConfigured(Boolean(r.routerApiKeyConfigured));
      setRouterThinking(
        typeof p1?.thinking_level === "string" ? p1.thinking_level : ""
      );
      const ra = strat === "router_llm" ? r.routerAgentRoles : undefined;
      if (strat === "router_llm") {
        const m: Record<string, string> = {};
        for (const pid of pipe) {
          m[pid] = ra && typeof ra[pid] === "string" ? ra[pid] : "";
        }
        setRouterAgentRoles(m);
      } else {
        setRouterAgentRoles({});
      }
      const ps = r.supervisorLlm;
      setSupervisorProvider(typeof ps?.provider === "string" ? ps.provider : "");
      setSupervisorModel(typeof ps?.model === "string" ? ps.model : "");
      setSupervisorBaseUrl(
        typeof ps?.base_url === "string" && ps.base_url.trim()
          ? ps.base_url
          : "http://127.0.0.1:8000/v1"
      );
      setSupervisorApiKey("");
      setSupervisorApiKeyConfigured(Boolean(r.supervisorApiKeyConfigured));
      setSupervisorThinking(
        typeof ps?.thinking_level === "string" ? ps.thinking_level : ""
      );
      setCreateSupervisorMaxIter(
        String(
          typeof r.supervisorMaxIterations === "number" && r.supervisorMaxIterations > 0
            ? r.supervisorMaxIterations
            : 5
        )
      );
      setCreateSupervisorLlmMaxRetries(
        String(
          typeof r.supervisorLlmMaxRetries === "number" && r.supervisorLlmMaxRetries >= 0
            ? r.supervisorLlmMaxRetries
            : 12
        )
      );
      setCreateOrchReplyLanguage(
        r.orchReplyLanguage === "zh" || r.orchReplyLanguage === "en"
          ? r.orchReplyLanguage
          : "auto"
      );
      setCreateOrchTraceEnabled(Boolean(r.orchTraceEnabled));
      setOrchFormOpen(true);
    },
    [t]
  );

  useEffect(() => {
    // When user enters Orchestrate view, refresh data (do not auto-open create dialog).
    void loadAgents();
    void loadOrches();
  }, [autoOpenKey, loadAgents, loadOrches]);

  useEffect(() => {
    if (!selectedOrchId.trim()) return;
    forceOrchScrollBottomRef.current = true;
  }, [selectedOrchId]);

  useLayoutEffect(() => {
    const orchId = selectedOrchId.trim();
    if (!orchId) return;
    if (selected?.orchId !== orchId) return;
    const el = messagesWrapRef.current;
    const end = messagesEndRef.current;
    if (!el || !end) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distanceToBottom < 160;
    if (forceOrchScrollBottomRef.current || nearBottom) {
      requestAnimationFrame(() => {
        end.scrollIntoView({ block: "end" });
      });
    }
    forceOrchScrollBottomRef.current = false;
  }, [selectedOrchId, selected?.orchId, selected?.messages?.length]);

  const doSubmitOrch = useCallback(async () => {
    setError(null);
    const idem =
      orchFormMode === "edit"
        ? `orch-update-${orchFormEditId}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
        : `orch-create-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    const mr = Number(createMaxRounds || "8");
    let dagSpec: OrchestrateDagSpec | undefined;
    if (createStrategy === "dag") {
      try {
        dagSpec = JSON.parse(createDagJson) as OrchestrateDagSpec;
      } catch {
        setError(t("orchestrateDagJsonInvalid"));
        return;
      }
      if (specHasCycle(dagSpec)) {
        setError(t("orchestrateDagCycleError"));
        return;
      }
    }
    const routerPayload =
      createStrategy === "router_llm"
        ? {
            provider: routerProvider || undefined,
            model: routerModel || undefined,
            base_url: routerBaseUrl || undefined,
            api_key: routerApiKey.trim() || undefined,
            thinking_level: routerThinking || undefined,
          }
        : undefined;
    const routerAgentRolesPayload =
      createStrategy === "router_llm"
        ? Object.fromEntries(
            createParticipants.map((p) => [p, (routerAgentRoles[p] ?? "").trim()])
          )
        : undefined;
    const supervisorPayload =
      createStrategy === "supervisor_pipeline"
        ? {
            provider: supervisorProvider || undefined,
            model: supervisorModel || undefined,
            base_url: supervisorBaseUrl || undefined,
            api_key: supervisorApiKey.trim() || undefined,
            thinking_level: supervisorThinking || undefined,
          }
        : undefined;
    const supMax = Number(createSupervisorMaxIter || "5");
    const supLlmRetries = Number(createSupervisorLlmMaxRetries ?? "12");
    const sessionKey =
      orchFormMode === "edit" ? orchEditSessionKey.trim() || "desktop-orchestrator" : "desktop-orchestrator";

    if (orchFormMode === "edit") {
      const oid = orchFormEditId.trim();
      if (!oid) return;
      const res = await orchestrateUpdate({
        orchId: oid,
        sessionKey,
        name: createName.trim() || undefined,
        participants: createParticipants,
        maxRounds: Number.isFinite(mr) && mr > 0 ? mr : 8,
        strategy: createStrategy,
        dag: dagSpec,
        routerLlm: routerPayload,
        routerAgentRoles: routerAgentRolesPayload,
        supervisorPipeline:
          createStrategy === "supervisor_pipeline" ? [...createParticipants] : undefined,
        supervisorLlm: supervisorPayload,
        supervisorMaxIterations:
          createStrategy === "supervisor_pipeline" && Number.isFinite(supMax) && supMax > 0
            ? Math.min(64, supMax)
            : undefined,
        supervisorLlmMaxRetries:
          createStrategy === "supervisor_pipeline" &&
          Number.isFinite(supLlmRetries) &&
          supLlmRetries >= 0
            ? Math.min(64, Math.floor(supLlmRetries))
            : undefined,
        orchReplyLanguage: createOrchReplyLanguage,
        orchTraceEnabled: createOrchTraceEnabled,
        idempotencyKey: idem,
      });
      if (!res.ok) {
        setError(res.error || t("orchestrateError"));
        return;
      }
      setOrchFormOpen(false);
      await loadOrches();
      return;
    }

    const res = await orchestrateCreate({
      sessionKey,
      name: createName.trim() || undefined,
      participants: createParticipants,
      maxRounds: Number.isFinite(mr) && mr > 0 ? mr : 8,
      strategy: createStrategy,
      dag: dagSpec,
      routerLlm: routerPayload,
      routerAgentRoles: routerAgentRolesPayload,
      supervisorPipeline:
        createStrategy === "supervisor_pipeline" ? [...createParticipants] : undefined,
      supervisorLlm: supervisorPayload,
      supervisorMaxIterations:
        createStrategy === "supervisor_pipeline" && Number.isFinite(supMax) && supMax > 0
          ? Math.min(64, supMax)
          : undefined,
      supervisorLlmMaxRetries:
        createStrategy === "supervisor_pipeline" &&
        Number.isFinite(supLlmRetries) &&
        supLlmRetries >= 0
          ? Math.min(64, Math.floor(supLlmRetries))
          : undefined,
      orchReplyLanguage: createOrchReplyLanguage,
      orchTraceEnabled: createOrchTraceEnabled,
      idempotencyKey: idem,
    });
    if (!res.ok) {
      setError(res.error || t("orchestrateError"));
      return;
    }
    setOrchFormOpen(false);
    await loadOrches();
    setSelectedOrchId(res.orchId);
  }, [
    createDagJson,
    createMaxRounds,
    createName,
    createOrchReplyLanguage,
    createOrchTraceEnabled,
    createParticipants,
    createStrategy,
    loadOrches,
    orchEditSessionKey,
    orchFormEditId,
    orchFormMode,
    routerAgentRoles,
    routerApiKey,
    routerBaseUrl,
    routerModel,
    routerProvider,
    routerThinking,
    createSupervisorMaxIter,
    createSupervisorLlmMaxRetries,
    supervisorApiKey,
    supervisorBaseUrl,
    supervisorModel,
    supervisorProvider,
    supervisorThinking,
    t,
  ]);

  const doDelete = useCallback(
    async (o: OrchestrateListItem) => {
      const name = (o.name || "").trim() || o.orchId.slice(0, 8);
      const ok = window.confirm(t("orchestrateDeleteConfirm", { name }));
      if (!ok) return;
      setError(null);
      const r = await orchestrateDelete(o.orchId);
      if (!r.ok) {
        setError(r.error || t("orchestrateError"));
        return;
      }
      if (selectedOrchId === o.orchId) {
        setSelectedOrchId("");
        setSelected(null);
      }
      await loadOrches();
    },
    [loadOrches, selectedOrchId, t]
  );

  const selectedTitle = useMemo(() => {
    if (!selectedOrchId.trim()) return "";
    const name = (selected?.name || "").trim();
    if (name) return name;
    const fromList = orches.find((o) => o.orchId === selectedOrchId)?.name;
    const fromListName = (fromList || "").trim();
    return fromListName || selectedOrchId.slice(0, 8);
  }, [orches, selected?.name, selectedOrchId]);

  const selectedParticipants = useMemo(() => {
    const parts = selected?.participants ?? [];
    const norm = parts.map((p) => (p || "").trim()).filter(Boolean);
    return Array.from(new Set(norm)).sort((a, b) => a.localeCompare(b));
  }, [selected?.participants]);

  useEffect(() => {
    if (!participantsOpen) return;
    const onDown = (e: MouseEvent) => {
      const root = participantsWrapRef.current;
      if (!root) return;
      if (e.target instanceof Node && root.contains(e.target)) return;
      setParticipantsOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [participantsOpen]);

  return (
    <div className="flex h-full min-h-0 w-full">
      <div className="w-64 shrink-0 border-r border-[var(--border)] bg-[var(--panel)] p-3 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold">{t("orchestrateTitle")}</div>
          <button
            type="button"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90 shrink-0"
            title={t("orchestrateCreate")}
            aria-label={t("orchestrateCreate")}
            onClick={openCreate}
          >
            <Image src="/icons/add.png" alt="" width={20} height={20} className="h-5 w-5 object-contain" />
          </button>
        </div>

        <div className="space-y-2 min-h-0 flex-1">
          <div className="text-[10px] text-[var(--muted)]">{t("orchestrateAll")}</div>
          <div className="min-h-0 flex-1 overflow-auto space-y-1">
            {orches.length === 0 ? (
              <div className="text-xs text-[var(--muted)]">{t("orchestrateEmptyList")}</div>
            ) : (
              orches.map((o) => {
                const active = o.orchId === selectedOrchId;
                const title = (o.name || "").trim() || o.orchId.slice(0, 8);
                return (
                  <div
                    key={o.orchId}
                    className={`w-full rounded-lg border border-[var(--border)] px-3 py-2 hover:opacity-90 ${
                      active ? "bg-[var(--accent)] text-white" : "bg-[var(--panel)]"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => setSelectedOrchId(o.orchId)}
                      >
                        <div className="text-xs font-medium truncate">{title}</div>
                        <div
                          className={`text-[10px] ${
                            active ? "text-white/85" : "text-[var(--muted)]"
                          } truncate`}
                        >
                          {o.status} · {((o.participants || []).join(", ") || "—").slice(0, 60)}
                        </div>
                      </button>
                      <div className="flex items-start gap-1 shrink-0">
                        <button
                          type="button"
                          title={t("orchestrateEditTooltip")}
                          aria-label={t("orchestrateEditTooltip")}
                          disabled={busyFromOrchestrateStatus(o.status)}
                          className={`flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] hover:opacity-90 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed ${
                            active ? "bg-white/10" : "bg-[var(--bg)]"
                          }`}
                          onClick={() => void openEdit(o)}
                        >
                          <Image
                            src="/icons/edit.png"
                            alt=""
                            width={18}
                            height={18}
                            className="h-[18px] w-[18px] object-contain"
                          />
                        </button>
                        <button
                          type="button"
                          title={t("orchestrateDelete")}
                          aria-label={t("orchestrateDelete")}
                          className={`flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] hover:opacity-90 shrink-0 ${
                            active ? "bg-white/10" : "bg-[var(--bg)]"
                          }`}
                          onClick={() => void doDelete(o)}
                        >
                          <Image
                            src="/icons/del.png"
                            alt=""
                            width={18}
                            height={18}
                            className="h-[18px] w-[18px] object-contain"
                          />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="mt-auto space-y-1">
          {selectedOrchId ? (
            <div className="text-[10px] text-[var(--muted)] font-mono truncate" title={selectedOrchId}>
              orch: {selectedOrchId}
            </div>
          ) : null}
          {selected?.status ? (
            <div className="text-[10px] text-[var(--muted)]">
              {t("orchestrateStatus")}: {selected.status}
              {selected.strategy === "dag"
                ? ` · ${t("orchestrateStrategyDag")}`
                : selected.strategy === "supervisor_pipeline"
                  ? ` · ${t("orchestrateStrategySupervisor")}`
                  : selected.strategy === "router_llm"
                    ? ` · ${t("orchestrateStrategyRouter")}`
                    : ""}
            </div>
          ) : null}
          {selected?.strategy === "dag" && selected.dagProgress ? (
            <div className="text-[9px] text-[var(--muted)] font-mono max-h-20 overflow-y-auto space-y-0.5">
              {Object.entries(selected.dagProgress).map(([nid, pr]) => (
                <div key={nid} className="truncate" title={pr.outputPreview || ""}>
                  {nid}: {pr.status || "?"}
                  {pr.error ? ` (${pr.error.slice(0, 40)})` : ""}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
          {error ? <p className="text-xs text-red-500/90 mb-2">{error}</p> : null}
          {selectedOrchId ? (
            <div className="mb-2 flex items-center gap-3 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2">
              <div className="min-w-0 flex-1 flex items-center gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-semibold truncate" title={selectedTitle}>
                    {selectedTitle}
                  </div>
                  <div className="text-[10px] text-[var(--muted)]">
                    {t("orchestrateStatus")}: {selected?.status || "—"}
                  </div>
                </div>
              </div>
              <div ref={participantsWrapRef} className="relative shrink-0">
                <button
                  type="button"
                  className="text-xs px-2 py-1 rounded-md border border-[var(--border)] bg-[var(--bg)] hover:opacity-90"
                  onClick={() => setParticipantsOpen((v) => !v)}
                  aria-label="Show agents"
                  title={selectedParticipants.join(", ")}
                >
                  Agents: {selectedParticipants.length}
                </button>
                {participantsOpen ? (
                  <div className="absolute right-0 mt-2 w-56 rounded-lg border border-[var(--border)] bg-[var(--bg)] shadow-xl overflow-hidden z-20">
                    <div className="px-3 py-2 text-[10px] text-[var(--muted)] border-b border-[var(--border)]">
                      {selectedParticipants.length ? "Agents" : "No agents"}
                    </div>
                    <div className="max-h-56 overflow-auto">
                      {selectedParticipants.length ? (
                        <ul className="py-1">
                          {selectedParticipants.map((aid) => {
                            const known = listedAgents.find((a) => a.agentId === aid);
                            const configured = Boolean(known?.configured);
                            return (
                              <li key={aid}>
                                <button
                                  type="button"
                                  className="w-full text-left px-3 py-2 text-xs hover:bg-[var(--panel)] flex items-center justify-between gap-2"
                                  onClick={() => setParticipantsOpen(false)}
                                >
                                  <span className="font-mono truncate" title={aid}>
                                    {aid}
                                  </span>
                                  <span className="text-[10px] text-[var(--muted)] shrink-0">
                                    {configured ? "configured" : ""}
                                  </span>
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      ) : (
                        <div className="px-3 py-3 text-xs text-[var(--muted)]">—</div>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          {selectedOrchId && live ? (
            <div className="mb-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2">
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-[var(--muted)]">
                <span className="font-mono">
                  run: {(live.runId || "").slice(0, 8) || "—"}…
                  {live.agentId ? ` · ${live.agentId}` : ""}
                </span>
                <span className="ml-auto">
                  ws:{" "}
                  <span
                    className={
                      connectionState === "connected" ? "text-emerald-400" : "text-[var(--muted)]"
                    }
                  >
                    {connectionState}
                  </span>
                </span>
              </div>
              {live.step ? <div className="text-xs text-[var(--muted)] mt-1">{live.step}</div> : null}
              {live.plannedToolCalls && live.plannedToolCalls.length ? (
                <div className="mt-2 text-[10px] text-[var(--muted)]">
                  <div className="font-semibold text-[var(--text)]">{t("chatToolPlanned")}</div>
                  <ul className="list-disc pl-4 space-y-0.5 font-mono break-all">
                    {live.plannedToolCalls.slice(0, 6).map((p, i) => (
                      <li key={`${p.name}-${i}`}>
                        {p.name}
                        {p.arguments_preview
                          ? ` — ${
                              p.arguments_preview.length > 160
                                ? `${p.arguments_preview.slice(0, 160)}…`
                                : p.arguments_preview
                            }`
                          : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {live.reasoning ? (
                <div className="mt-2 text-xs text-[var(--muted)] border-l-2 border-[var(--accent)] pl-2 whitespace-pre-wrap max-h-40 overflow-y-auto">
                  <span className="font-medium">{t("reasoning")}: </span>
                  {live.reasoning}
                </div>
              ) : null}
              {live.toolTraces && live.toolTraces.length ? (
                <div className="mt-2 text-[10px] border border-[var(--border)] rounded-md p-2 bg-[var(--bg)]/40 max-h-40 overflow-y-auto">
                  <div className="font-semibold text-[var(--muted)] mb-1">{t("chatToolActivity")}</div>
                  <div className="space-y-2">
                    {live.toolTraces.slice(-8).map((tr) => (
                      <div
                        key={tr.id}
                        className="border-b border-[var(--border)]/60 last:border-0 pb-2 last:pb-0"
                      >
                        <div className="flex flex-wrap gap-2 items-baseline font-mono text-[10px]">
                          <span
                            className={
                              tr.state === "error"
                                ? "text-red-400"
                                : tr.state === "done"
                                  ? "text-emerald-400"
                                  : "text-amber-400"
                            }
                          >
                            {tr.state === "running" ? "…" : tr.state === "done" ? "✓" : "✗"}{" "}
                            {tr.name}
                          </span>
                          {tr.elapsedMs != null ? (
                            <span className="text-[var(--muted)]">
                              {(tr.elapsedMs / 1000).toFixed(1)}s
                            </span>
                          ) : null}
                        </div>
                        {tr.preview ? (
                          <pre className="mt-1 whitespace-pre-wrap break-words text-[9px] leading-relaxed text-[var(--text)] opacity-90">
                            {tr.preview}
                          </pre>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
          <div
            ref={messagesWrapRef}
            className="min-h-0 flex-1 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3 space-y-2"
          >
            {!selectedOrchId ? (
              <div className="text-xs text-[var(--muted)]">{t("orchestratePickOne")}</div>
            ) : (selected?.messages?.length || 0) === 0 ? (
              <div className="text-xs text-[var(--muted)]">{t("orchestrateEmpty")}</div>
            ) : (
              (selected?.messages || []).map((m) => {
                const avUrl = avatarUrlForSpeaker(m.speaker);
                const fallback = "/icons/planet.png";
                const imgSrc =
                  (m.speaker || "").trim().toLowerCase() === "user" ? fallback : avUrl || "/icons/robot.png";
                return (
                  <div key={m.id} className="flex gap-2 items-start">
                    <Image
                      src={imgSrc}
                      alt=""
                      width={32}
                      height={32}
                      className="h-8 w-8 shrink-0 rounded-lg object-cover mt-0.5"
                      unoptimized
                    />
                    <div
                      className={`min-w-0 flex-1 flex flex-col gap-1 rounded-lg border border-[var(--border)] px-3 py-2 ${speakerCardClass(
                        m.speaker
                      )}`}
                    >
                      <div className="flex flex-wrap items-center gap-2 text-[10px] text-[var(--muted)]">
                        <span className={`font-mono font-semibold ${speakerColorClass(m.speaker)}`}>
                          {m.speaker}
                        </span>
                        <span>·</span>
                        <span>{m.role}</span>
                        <span>·</span>
                        <span>r{m.round}</span>
                        {m.nodeId ? (
                          <>
                            <span>·</span>
                            <span className="font-mono">
                              {t("orchestrateNodeMeta")}:{m.nodeId}
                            </span>
                          </>
                        ) : null}
                        {m.ts ? (
                          <>
                            <span>·</span>
                            <span className="font-mono">{fmtTs(m.ts)}</span>
                          </>
                        ) : null}
                      </div>
                      <div className="text-xs whitespace-pre-wrap break-words leading-relaxed">
                        {m.text}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
            <div ref={messagesEndRef} />
          </div>

          {selectedOrchId ? (
            <div className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] overflow-hidden">
              <button
                type="button"
                className="w-full text-left px-3 py-2 text-xs font-medium flex items-center justify-between gap-2 hover:bg-[var(--bg)]/80"
                onClick={() => setOrchAgentsMdOpen((v) => !v)}
              >
                <span>{t("orchestrateAgentsMd")}</span>
                <span className="text-[var(--muted)] shrink-0">
                  {orchAgentsMdOpen ? t("orchestrateAgentsMdCollapse") : t("orchestrateAgentsMdExpand")}
                </span>
              </button>
              {orchAgentsMdOpen ? (
                <div className="px-3 pb-3 pt-0 border-t border-[var(--border)] space-y-2">
                  <p className="text-[10px] text-[var(--muted)] leading-relaxed pt-2">
                    {t("orchestrateAgentsMdHint")}
                  </p>
                  <textarea
                    className="w-full min-h-[140px] max-h-[40vh] text-xs font-mono px-2 py-2 rounded-md border border-[var(--border)] bg-[var(--bg)] text-[var(--text)] resize-y"
                    value={orchAgentsMdText}
                    onChange={(e) => {
                      setOrchAgentsMdText(e.target.value);
                      setOrchAgentsMdDirty(true);
                    }}
                    disabled={busy}
                    spellCheck={false}
                  />
                  <div className="flex justify-end">
                    <button
                      type="button"
                      className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs disabled:opacity-50"
                      disabled={!orchAgentsMdDirty || busy || orchAgentsMdSaving}
                      onClick={() => void saveOrchAgentsMd()}
                    >
                      {orchAgentsMdSaving ? t("orchestrateAgentsMdSaving") : t("orchestrateAgentsMdSave")}
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="mt-3 flex gap-2 items-stretch">
            <OrchestrateMentionInput
              value={input}
              onChange={setInput}
              onSubmit={() => void send()}
              busy={busy}
              placeholder={t("orchestratePrompt")}
              participants={selected?.participants ?? []}
              hintBelow={
                selected?.strategy !== "dag" && selected?.strategy !== "supervisor_pipeline"
                  ? t("orchestrateMentionHint")
                  : null
              }
              noMatchLabel={t("orchestrateMentionNoMatch")}
            />
            <div className="flex flex-col justify-end gap-2 shrink-0">
              <ChatThinkToolCheckbox
                checked={streamReasoning}
                onChange={setStreamReasoning}
                t={t}
              />
              <button
                type="button"
                className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50 min-h-[40px]"
                onClick={() => void send()}
                disabled={!canSend || busy}
              >
                {t("send")}
              </button>
            </div>
          </div>
        </div>
      </div>

      {orchFormOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4"
          role="presentation"
        >
          <div
            className={`w-full ${createStrategy === "dag" ? "max-w-4xl" : "max-w-lg"} max-h-[min(92vh,780px)] overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--bg)] shadow-2xl`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="orbit-create-orch-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-[var(--border)] px-4 py-3 flex items-center justify-between">
              <h3 id="orbit-create-orch-title" className="text-sm font-semibold">
                {orchFormMode === "edit"
                  ? t("orchestrateEditTitle", {
                      name:
                        (createName || "").trim() ||
                        (orchFormEditId ? orchFormEditId.slice(0, 8) : "—"),
                    })
                  : t("orchestrateCreate")}
              </h3>
              <button
                type="button"
                className="text-xs text-[var(--muted)] px-2 py-1 rounded hover:bg-[var(--panel)]"
                onClick={() => setOrchFormOpen(false)}
              >
                {t("closeDialog")}
              </button>
            </div>
            <div className="p-4 space-y-4 text-sm">
              <label className="flex flex-col gap-1">
                <span className="text-[var(--muted)] text-xs">{t("orchestrateName")}</span>
                <input
                  className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="team-1"
                />
              </label>

              {createStrategy === "dag" ? (
                <p className="text-[10px] text-[var(--muted)]">{t("orchestrateDagNote")}</p>
              ) : createStrategy === "supervisor_pipeline" ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("orchestrateSupervisorMaxIter")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={createSupervisorMaxIter}
                      onChange={(e) => setCreateSupervisorMaxIter(e.target.value)}
                      inputMode="numeric"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("orchestrateSupervisorLlmMaxRetries")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={createSupervisorLlmMaxRetries}
                      onChange={(e) => setCreateSupervisorLlmMaxRetries(e.target.value)}
                      inputMode="numeric"
                    />
                  </label>
                </div>
              ) : (
                <label className="flex flex-col gap-1">
                  <span className="text-[var(--muted)] text-xs">{t("orchestrateMaxRounds")}</span>
                  <input
                    className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                    value={createMaxRounds}
                    onChange={(e) => setCreateMaxRounds(e.target.value)}
                    inputMode="numeric"
                  />
                </label>
              )}

              <label className="flex flex-col gap-1">
                <span className="text-[var(--muted)] text-xs">{t("orchestrateStrategy")}</span>
                <select
                  className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                  value={createStrategy}
                  onChange={(e) =>
                    setCreateStrategy(
                      e.target.value as
                        | "round_robin"
                        | "router_llm"
                        | "dag"
                        | "supervisor_pipeline"
                    )
                  }
                >
                  <option value="round_robin">{t("orchestrateStrategyRoundRobin")}</option>
                  <option value="router_llm">{t("orchestrateStrategyRouter")}</option>
                  <option value="dag">{t("orchestrateStrategyDag")}</option>
                  <option value="supervisor_pipeline">{t("orchestrateStrategySupervisor")}</option>
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-[var(--muted)] text-xs">{t("orchestrateReplyLanguage")}</span>
                <select
                  className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                  value={createOrchReplyLanguage}
                  onChange={(e) =>
                    setCreateOrchReplyLanguage(e.target.value as OrchestrateReplyLanguage)
                  }
                >
                  <option value="auto">{t("orchestrateReplyLanguageAuto")}</option>
                  <option value="zh">{t("orchestrateReplyLanguageZh")}</option>
                  <option value="en">{t("orchestrateReplyLanguageEn")}</option>
                </select>
                <p className="text-[10px] text-[var(--muted)] leading-relaxed">
                  {t("orchestrateReplyLanguageHint")}
                </p>
              </label>

              <label className="flex items-start gap-2 cursor-pointer py-1">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={createOrchTraceEnabled}
                  onChange={(e) => setCreateOrchTraceEnabled(e.target.checked)}
                />
                <span className="flex flex-col gap-0.5 min-w-0">
                  <span className="text-[var(--muted)] text-xs">{t("orchestrateTraceEnabled")}</span>
                  <span className="text-[10px] text-[var(--muted)] leading-relaxed">
                    {t("orchestrateTraceEnabledHint")}
                  </span>
                </span>
              </label>

              {createStrategy === "dag" ? (
                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className={`rounded-lg border px-3 py-1.5 text-xs ${
                        dagEditorMode === "visual"
                          ? "border-[var(--accent)] bg-[var(--accent)] text-white"
                          : "border-[var(--border)] bg-[var(--panel)] text-[var(--text)]"
                      }`}
                      onClick={() => setDagEditorMode("visual")}
                    >
                      {t("orchestrateDagVisualTab")}
                    </button>
                    <button
                      type="button"
                      className={`rounded-lg border px-3 py-1.5 text-xs ${
                        dagEditorMode === "json"
                          ? "border-[var(--accent)] bg-[var(--accent)] text-white"
                          : "border-[var(--border)] bg-[var(--panel)] text-[var(--text)]"
                      }`}
                      onClick={() => setDagEditorMode("json")}
                    >
                      {t("orchestrateDagJsonTab")}
                    </button>
                  </div>
                  {dagEditorMode === "visual" ? (
                    <OrchestrateDagCanvas
                      key={dagJsonResetKey}
                      initialSpec={createDagSpec}
                      listedAgents={listedAgents}
                      onSpecChange={handleDagSpecChange}
                      t={t}
                    />
                  ) : (
                    <label className="flex flex-col gap-1">
                      <span className="text-[var(--muted)] text-xs">{t("orchestrateDagJson")}</span>
                      <p className="text-[10px] text-[var(--muted)]">{t("orchestrateDagJsonHint")}</p>
                      <textarea
                        className="min-h-[220px] px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-[11px] font-mono resize-y"
                        value={createDagJson}
                        onChange={onDagJsonChange}
                        spellCheck={false}
                      />
                    </label>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  {createStrategy === "supervisor_pipeline" ? (
                    <p className="text-[10px] text-[var(--muted)]">{t("orchestrateSupervisorParticipantsHint")}</p>
                  ) : null}
                  <div className="flex items-center justify-between">
                    <span className="text-[var(--muted)] text-xs">{t("orchestrateParticipants")}</span>
                    <button
                      type="button"
                      className="text-xs px-2 py-1 rounded border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
                      onClick={() => setAddOpen(true)}
                    >
                      {t("orchestrateAddAgent")}
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {createParticipants.map((p) => (
                      <span
                        key={p}
                        className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[10px] font-mono"
                      >
                        {p}
                        {createParticipants.length > 1 ? (
                          <button
                            type="button"
                            className="text-[var(--muted)] hover:text-[var(--text)]"
                            onClick={() => removeParticipant(p)}
                          >
                            ×
                          </button>
                        ) : null}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {createStrategy === "router_llm" ? (
                <div className="border-t border-[var(--border)] pt-3 space-y-3">
                  <div className="text-[10px] text-[var(--muted)]">
                    {t("orchestrateRouterHint")}
                  </div>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmProvider")}</span>
                    <select
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={routerProvider}
                      onChange={(e) => setRouterProvider(e.target.value)}
                    >
                      <option value="">openai</option>
                      {providers.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmModel")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={routerModel}
                      onChange={(e) => setRouterModel(e.target.value)}
                      placeholder="gpt-4o-mini"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmBaseUrl")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={routerBaseUrl}
                      onChange={(e) => setRouterBaseUrl(e.target.value)}
                      placeholder="http://127.0.0.1:8000/v1"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmApiKey")}</span>
                    <input
                      type="password"
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={routerApiKey}
                      onChange={(e) => setRouterApiKey(e.target.value)}
                      autoComplete="off"
                      placeholder={
                        routerApiKeyConfigured ? t("agentsEditLlmApiKeyPlaceholder") : undefined
                      }
                    />
                    {routerApiKeyConfigured ? (
                      <span className="text-[10px] text-[var(--muted)]">
                        {t("agentsEditLlmApiKeyHint")}
                      </span>
                    ) : null}
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmThinking")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={routerThinking}
                      onChange={(e) => setRouterThinking(e.target.value)}
                      placeholder="off | low | medium | high"
                    />
                  </label>
                  <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] p-2 space-y-2">
                    <div className="text-xs text-[var(--text)]">{t("orchestrateRouterAgentRoles")}</div>
                    <p className="text-[10px] text-[var(--muted)]">{t("orchestrateRouterAgentRolesHint")}</p>
                    {createParticipants.map((pid) => (
                      <label key={pid} className="flex flex-col gap-1">
                        <span className="text-[10px] text-[var(--muted)] font-mono">{pid}</span>
                        <textarea
                          className="min-h-[52px] px-2 py-1.5 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-[11px] resize-y"
                          value={routerAgentRoles[pid] ?? ""}
                          onChange={(e) =>
                            setRouterAgentRoles((prev) => ({ ...prev, [pid]: e.target.value }))
                          }
                          placeholder={t("orchestrateRouterAgentRolePlaceholder")}
                          spellCheck={false}
                        />
                      </label>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <button
                      type="button"
                      title={t("agentsLlmTestTooltip")}
                      aria-label={t("agentsLlmTestTooltip")}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] text-xs hover:opacity-90 disabled:opacity-50"
                      disabled={routerLlmTestLoading}
                      onClick={() => void runRouterLlmTest()}
                    >
                      <Image
                        src="/icons/test.png"
                        alt=""
                        width={16}
                        height={16}
                        className="h-4 w-4 object-contain"
                      />
                      {t("agentsLlmTest")}
                    </button>
                    {routerLlmTestBanner ? (
                      <span
                        className={`text-[11px] ${
                          routerLlmTestBanner.ok ? "text-emerald-500/90" : "text-red-500/90"
                        }`}
                      >
                        {routerLlmTestBanner.text}
                      </span>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {createStrategy === "supervisor_pipeline" ? (
                <div className="border-t border-[var(--border)] pt-3 space-y-3">
                  <div className="text-[10px] text-[var(--muted)]">{t("orchestrateSupervisorHint")}</div>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmProvider")}</span>
                    <select
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={supervisorProvider}
                      onChange={(e) => setSupervisorProvider(e.target.value)}
                    >
                      <option value="">openai</option>
                      {providers.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmModel")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={supervisorModel}
                      onChange={(e) => setSupervisorModel(e.target.value)}
                      placeholder="gpt-4o-mini"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmBaseUrl")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={supervisorBaseUrl}
                      onChange={(e) => setSupervisorBaseUrl(e.target.value)}
                      placeholder="http://127.0.0.1:8000/v1"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmApiKey")}</span>
                    <input
                      type="password"
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={supervisorApiKey}
                      onChange={(e) => setSupervisorApiKey(e.target.value)}
                      autoComplete="off"
                      placeholder={
                        supervisorApiKeyConfigured ? t("agentsEditLlmApiKeyPlaceholder") : undefined
                      }
                    />
                    {supervisorApiKeyConfigured ? (
                      <span className="text-[10px] text-[var(--muted)]">
                        {t("agentsEditLlmApiKeyHint")}
                      </span>
                    ) : null}
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[var(--muted)] text-xs">{t("agentsCreateLlmThinking")}</span>
                    <input
                      className="px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-xs"
                      value={supervisorThinking}
                      onChange={(e) => setSupervisorThinking(e.target.value)}
                      placeholder="off | low | medium | high"
                    />
                  </label>
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <button
                      type="button"
                      title={t("agentsLlmTestTooltip")}
                      aria-label={t("agentsLlmTestTooltip")}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] text-xs hover:opacity-90 disabled:opacity-50"
                      disabled={supervisorLlmTestLoading}
                      onClick={() => void runSupervisorLlmTest()}
                    >
                      <Image
                        src="/icons/test.png"
                        alt=""
                        width={16}
                        height={16}
                        className="h-4 w-4 object-contain"
                      />
                      {t("agentsLlmTest")}
                    </button>
                    {supervisorLlmTestBanner ? (
                      <span
                        className={`text-[11px] ${
                          supervisorLlmTestBanner.ok ? "text-emerald-500/90" : "text-red-500/90"
                        }`}
                      >
                        {supervisorLlmTestBanner.text}
                      </span>
                    ) : null}
                  </div>
                </div>
              ) : null}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  className="px-3 py-2 rounded-lg border border-[var(--border)] text-xs"
                  onClick={() => setOrchFormOpen(false)}
                >
                  {t("orchestrateCancel")}
                </button>
                <button
                  type="button"
                  className="px-3 py-2 rounded-lg bg-[var(--accent)] text-white text-xs font-medium"
                  onClick={() => void doSubmitOrch()}
                >
                  {orchFormMode === "edit" ? t("orchestrateEditSave") : t("orchestrateCreate")}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {addOpen ? (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/55 p-4"
          role="presentation"
        >
          <div
            className="w-full max-w-md rounded-xl border border-[var(--border)] bg-[var(--bg)] shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby="orbit-add-orch-agent-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-[var(--border)] px-4 py-3 flex items-center justify-between">
              <h3 id="orbit-add-orch-agent-title" className="text-sm font-semibold">
                {t("orchestrateAddAgent")}
              </h3>
              <button
                type="button"
                className="text-xs text-[var(--muted)] px-2 py-1 rounded hover:bg-[var(--panel)]"
                onClick={() => setAddOpen(false)}
              >
                {t("closeDialog")}
              </button>
            </div>
            <div className="p-4 space-y-2">
              {listedAgents.length === 0 ? (
                <p className="text-xs text-[var(--muted)]">{t("agentsLoading")}</p>
              ) : (
                <div className="max-h-72 overflow-auto space-y-1">
                  {listedAgents.map((row) => {
                    const av = row.avatar?.trim();
                    const src = av ? `/icons/headers/${encodeURIComponent(av)}` : "/icons/robot.png";
                    return (
                      <button
                        key={row.agentId}
                        type="button"
                        className="w-full flex items-center gap-2 text-left text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90 font-mono"
                        onClick={() => {
                          addParticipant(row.agentId);
                          setAddOpen(false);
                        }}
                      >
                        <Image
                          src={src}
                          alt=""
                          width={28}
                          height={28}
                          className="h-7 w-7 shrink-0 rounded-md object-cover"
                          unoptimized
                        />
                        {row.agentId}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

