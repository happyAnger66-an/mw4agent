"use client";

import Image from "next/image";
import { useCallback, useEffect, useState } from "react";
import {
  statsAgentsList,
  type AgentLlmUsageStats,
  type StatsAgentListRow,
} from "@/lib/gateway";
import { useI18n } from "@/lib/i18n";

function formatUpdatedAt(ms: number | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) {
    return "—";
  }
  try {
    return new Date(ms).toLocaleString();
  } catch {
    return "—";
  }
}

function hasUsage(u: AgentLlmUsageStats | null | undefined): boolean {
  if (!u) return false;
  return (
    (u.promptTokensTotal !== undefined && u.promptTokensTotal !== null) ||
    (u.completionTokensTotal !== undefined && u.completionTokensTotal !== null) ||
    (u.totalTokensTotal !== undefined && u.totalTokensTotal !== null) ||
    (u.numRequests !== undefined && u.numRequests !== null)
  );
}

export function StatsPanel() {
  const { t } = useI18n();
  const [rows, setRows] = useState<StatsAgentListRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const st = await statsAgentsList();
    setLoading(false);
    if (!st.ok) {
      setError(st.error || t("statsError"));
      setRows([]);
      return;
    }
    const sorted = [...st.agents].sort((a, b) =>
      a.agentId.localeCompare(b.agentId, undefined, { sensitivity: "base" })
    );
    setRows(sorted);
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex flex-col h-full min-h-0 p-4 sm:p-6 max-w-5xl overflow-auto">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-2.5 min-w-0">
          <Image
            src="/icons/stats.png"
            alt=""
            width={28}
            height={28}
            className="h-7 w-7 shrink-0 rounded-md object-contain opacity-95"
          />
          <div>
            <h2 className="text-lg font-semibold leading-tight">{t("statsTitle")}</h2>
            <p className="text-xs text-[var(--muted)] mt-0.5">{t("statsSubtitle")}</p>
          </div>
        </div>
        <button
          type="button"
          className="text-sm px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90 shrink-0"
          onClick={() => void load()}
          disabled={loading}
        >
          {t("agentsRefresh")}
        </button>
      </div>

      <section className="rounded-xl border border-[var(--border)] bg-[var(--panel)] overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h3 className="text-sm font-semibold">{t("statsSectionAgentLlm")}</h3>
          <p className="text-[11px] text-[var(--muted)] mt-1">{t("agentsLlmUsageHint")}</p>
        </div>

        {loading ? (
          <p className="p-4 text-sm text-[var(--muted)]">{t("statsLoading")}</p>
        ) : null}
        {!loading && error ? (
          <p className="p-4 text-sm text-red-500/90">{error}</p>
        ) : null}
        {!loading && !error && rows.length === 0 ? (
          <p className="p-4 text-sm text-[var(--muted)]">{t("statsEmpty")}</p>
        ) : null}

        {!loading && !error && rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-[var(--border)] text-[var(--muted)] text-xs uppercase tracking-wide bg-[var(--bg)]/40">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("agentId")}</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">{t("statsColPrompt")}</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">{t("statsColCompletion")}</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">{t("statsColTotal")}</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">{t("statsColRequests")}</th>
                  <th className="px-3 py-2 font-medium hidden sm:table-cell">{t("statsColUpdated")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const u = row.llmUsage ?? undefined;
                  const show = hasUsage(u);
                  return (
                    <tr
                      key={row.agentId}
                      className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg)]/50"
                    >
                      <td className="px-3 py-2 font-mono text-xs">{row.agentId}</td>
                      <td className="px-3 py-2 text-xs font-mono tabular-nums">
                        {show ? String(u!.promptTokensTotal ?? "—") : "—"}
                      </td>
                      <td className="px-3 py-2 text-xs font-mono tabular-nums">
                        {show ? String(u!.completionTokensTotal ?? "—") : "—"}
                      </td>
                      <td className="px-3 py-2 text-xs font-mono tabular-nums">
                        {show ? String(u!.totalTokensTotal ?? "—") : "—"}
                      </td>
                      <td className="px-3 py-2 text-xs font-mono tabular-nums">
                        {show ? String(u!.numRequests ?? "—") : "—"}
                      </td>
                      <td className="px-3 py-2 text-[11px] text-[var(--muted)] hidden sm:table-cell whitespace-nowrap">
                        {formatUpdatedAt(row.updatedAtMs)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <p className="mt-4 text-[11px] text-[var(--muted)] leading-relaxed max-w-xl">
        {t("statsMoreComing")}
      </p>
    </div>
  );
}
