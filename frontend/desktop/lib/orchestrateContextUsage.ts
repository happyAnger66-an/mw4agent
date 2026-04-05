import type { ListedAgentLlm, OrchMessage } from "@/lib/gateway";

export type OrchTokenUsage = {
  input?: number;
  output?: number;
  total?: number;
};

export function formatTokenCount(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.round(n));
}

export function parseWsUsage(raw: unknown): OrchTokenUsage | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const o = raw as Record<string, unknown>;
  const pick = (k: string): number | undefined => {
    const v = o[k];
    return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : undefined;
  };
  const input = pick("input");
  const output = pick("output");
  const total = pick("total");
  if (input == null && output == null && total == null) return undefined;
  return { input, output, total };
}

export function agentContextTokenLimit(llm?: ListedAgentLlm): number | undefined {
  if (!llm) return undefined;
  const a = (llm as { context_window?: unknown; contextWindow?: unknown }).context_window;
  const b = (llm as { contextWindow?: unknown }).contextWindow;
  const n = typeof a === "number" ? a : typeof b === "number" ? b : NaN;
  if (!Number.isFinite(n) || n <= 0) return undefined;
  return Math.floor(n);
}

export type OrchestrateAgentUsageRow = {
  agentId: string;
  usage?: OrchTokenUsage;
  contextLimit?: number;
};

/**
 * Per agent: latest assistant message usage in this orchestration, merged with in-flight WS usage.
 */
export function collectOrchestrateAgentUsageRows(opts: {
  participants: string[];
  messages: OrchMessage[];
  listedAgents: Array<{ agentId: string; llm?: ListedAgentLlm }>;
  liveAgentId?: string;
  liveUsage?: OrchTokenUsage | null;
}): OrchestrateAgentUsageRow[] {
  const { participants, messages, listedAgents, liveAgentId, liveUsage } = opts;
  const ids = new Set<string>();
  for (const p of participants) {
    const v = (p || "").trim();
    if (v) ids.add(v);
  }
  for (const m of messages) {
    if (m.role !== "assistant") continue;
    const sp = (m.speaker || "").trim();
    if (sp && sp.toLowerCase() !== "user") ids.add(sp);
  }

  const lastUsageByAgent: Record<string, OrchTokenUsage> = {};
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "assistant") continue;
    const sp = (m.speaker || "").trim();
    if (!sp || lastUsageByAgent[sp]) continue;
    const u = m.usage;
    if (!u) continue;
    if (u.input == null && u.output == null && u.total == null) continue;
    lastUsageByAgent[sp] = { input: u.input, output: u.output, total: u.total };
  }

  const rows: OrchestrateAgentUsageRow[] = [];
  for (const agentId of Array.from(ids).sort((a, b) => a.localeCompare(b))) {
    const row = listedAgents.find((a) => a.agentId === agentId);
    const contextLimit = agentContextTokenLimit(row?.llm);
    const fromMsg = lastUsageByAgent[agentId];
    const fromLive =
      liveAgentId && liveAgentId === agentId && liveUsage && Object.keys(liveUsage).length
        ? liveUsage
        : undefined;
    const usage = fromLive ?? fromMsg;
    rows.push({ agentId, usage, contextLimit });
  }
  return rows;
}
