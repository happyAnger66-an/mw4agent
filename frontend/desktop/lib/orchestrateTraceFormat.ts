import type { OrchTraceEvent } from "@/lib/gateway";

/** Top-level agent id on trace rows (camelCase in JSON; ``user_message`` uses ``user``). */
export function traceEventAgentId(ev: OrchTraceEvent): string {
  const o = ev as Record<string, unknown>;
  const raw = o.agentId ?? o.agent_id;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  return "";
}

export function formatOrchTraceSummary(ev: OrchTraceEvent): string {
  const typ = String(ev.type ?? "");
  const agent = traceEventAgentId(ev);
  const round = ev.orchRound != null ? `r${Number(ev.orchRound)}` : "";
  const node = ev.nodeId != null ? ` · ${String(ev.nodeId)}` : "";
  const pl = ev.payload;
  if (typ === "user_message" && pl && typeof pl === "object" && "text" in pl) {
    const tx = String((pl as { text?: string }).text ?? "").slice(0, 120);
    return `user · ${round} · ${tx}${tx.length >= 120 ? "…" : ""}`;
  }
  if (typ === "agent_input" || typ === "agent_output") {
    const tx =
      pl && typeof pl === "object" && "text" in pl
        ? String((pl as { text?: string }).text ?? "").slice(0, 80)
        : "";
    return `${typ} · ${agent} · ${round}${node} · ${tx}${tx.length >= 80 ? "…" : ""}`;
  }
  if (typ.startsWith("tool_")) {
    const p = pl && typeof pl === "object" ? (pl as Record<string, unknown>) : {};
    const name = String(p.tool_name ?? "?");
    return `${typ} · ${agent} · ${name}`;
  }
  if (typ === "llm_round") return `llm_round · ${agent} · ${round}`;
  if (typ === "llm_prompt") {
    const p = pl && typeof pl === "object" ? (pl as Record<string, unknown>) : {};
    const ph = String(p.phase ?? "?");
    const rd = p.round != null ? ` · r${Number(p.round)}` : "";
    return `llm_prompt · ${agent} · ${round} · ${ph}${rd}`;
  }
  if (typ.startsWith("lifecycle")) return `${typ} · ${agent}`;
  return `${typ} · ${agent} · ${round}`;
}

export function formatTraceEventTs(ts?: number): string {
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

export function traceEventTypeClass(type: string): string {
  const t = (type || "").toLowerCase();
  if (t === "user_message") return "bg-sky-500/20 text-sky-300 border-sky-500/40";
  if (t === "agent_input" || t === "agent_output") return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
  if (t.startsWith("tool_")) return "bg-amber-500/20 text-amber-300 border-amber-500/40";
  if (t === "llm_round") return "bg-violet-500/20 text-violet-300 border-violet-500/40";
  if (t === "llm_prompt") return "bg-fuchsia-500/20 text-fuchsia-300 border-fuchsia-500/40";
  if (t.startsWith("lifecycle")) return "bg-zinc-500/20 text-zinc-300 border-zinc-500/40";
  return "bg-[var(--panel)] text-[var(--muted)] border-[var(--border)]";
}
