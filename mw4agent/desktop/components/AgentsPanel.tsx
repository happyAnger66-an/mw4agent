"use client";

import { useCallback, useEffect, useState } from "react";
import { listAgents, type ListedAgent } from "@/lib/gateway";
import { useI18n } from "@/lib/i18n";

type AgentsPanelProps = {
  onOpenChatWithAgent: (agentId: string) => void;
};

export function AgentsPanel({ onOpenChatWithAgent }: AgentsPanelProps) {
  const { t } = useI18n();
  const [agents, setAgents] = useState<ListedAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const res = await listAgents();
    setLoading(false);
    if (!res.ok) {
      setError(res.error || t("agentsError"));
      setAgents([]);
      return;
    }
    setAgents(res.agents);
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex flex-col h-full min-h-0 p-4 sm:p-6 max-w-5xl">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <h2 className="text-lg font-semibold">{t("myAgents")}</h2>
        <button
          type="button"
          className="text-sm px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
          onClick={() => void load()}
          disabled={loading}
        >
          {t("agentsRefresh")}
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-[var(--muted)]">{t("agentsLoading")}</p>
      ) : null}
      {!loading && error ? (
        <p className="text-sm text-red-500/90">{error}</p>
      ) : null}
      {!loading && !error && agents.length === 0 ? (
        <p className="text-sm text-[var(--muted)]">{t("agentsEmpty")}</p>
      ) : null}

      {!loading && agents.length > 0 ? (
        <div className="overflow-auto rounded-lg border border-[var(--border)] bg-[var(--panel)]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-[var(--border)] text-[var(--muted)] text-xs uppercase tracking-wide">
              <tr>
                <th className="px-3 py-2 font-medium">{t("agentId")}</th>
                <th className="px-3 py-2 font-medium hidden md:table-cell">
                  {t("workspaceDir")}
                </th>
                <th className="px-3 py-2 font-medium">{t("runStatus")}</th>
                <th className="px-3 py-2 font-medium w-[1%] whitespace-nowrap">
                  {t("actions")}
                </th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => {
                const rs = a.runStatus;
                const state = rs?.state ?? "—";
                const n = rs?.activeRuns ?? 0;
                return (
                  <tr
                    key={a.agentId}
                    className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg)]/50"
                  >
                    <td className="px-3 py-2 font-mono text-xs">
                      <div className="flex flex-col gap-0.5">
                        <span>{a.agentId}</span>
                        {a.configured === false ? (
                          <span className="text-[10px] text-amber-500/90">
                            {t("agentNotConfigured")}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs text-[var(--muted)] max-w-xs truncate hidden md:table-cell">
                      {a.workspaceDir || "—"}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span className="text-[var(--text)]">{state}</span>
                      {n > 0 ? (
                        <span className="text-[var(--muted)] ml-1">
                          ({t("activeRuns")}: {n})
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        className="text-xs px-2 py-1 rounded border border-[var(--border)] bg-[var(--accent)] text-white"
                        onClick={() => onOpenChatWithAgent(a.agentId)}
                      >
                        {t("useInChat")}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
