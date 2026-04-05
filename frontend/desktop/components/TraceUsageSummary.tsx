"use client";

import { useI18n } from "@/lib/i18n";
import type { TraceUsageStats } from "@/lib/traceUsageStats";

export function TraceUsageSummary({ stats }: { stats: TraceUsageStats }) {
  const { t } = useI18n();
  const { toolCounts, skillCounts } = stats;
  const emptyTools = toolCounts.length === 0;
  const emptySkills = skillCounts.length === 0;

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)]/80 p-3 shrink-0">
      <div className="text-xs font-semibold text-[var(--text)] mb-2">{t("traceSummaryTitle")}</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-[var(--muted)] mb-1.5">
            {t("traceSummaryTools")}
          </div>
          {emptyTools ? (
            <span className="text-[10px] text-[var(--muted)]">{t("traceSummaryNoTools")}</span>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {toolCounts.map(({ name, count }) => (
                <li
                  key={name}
                  className="rounded border border-[var(--border)] bg-[var(--bg)]/60 px-2 py-0.5 text-[10px] font-mono"
                  title={name}
                >
                  <span className="text-[var(--text)]">{name}</span>
                  <span className="text-[var(--muted)] mx-1">×</span>
                  <span className="text-[var(--accent)] font-medium tabular-nums">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-[var(--muted)] mb-1.5">
            {t("traceSummarySkills")}
          </div>
          {emptySkills ? (
            <span className="text-[10px] text-[var(--muted)]">{t("traceSummaryNoSkills")}</span>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {skillCounts.map(({ name, count }) => (
                <li
                  key={name}
                  className="rounded border border-[var(--border)] bg-[var(--bg)]/60 px-2 py-0.5 text-[10px] font-mono"
                  title={name}
                >
                  <span className="text-[var(--text)]">{name}</span>
                  <span className="text-[var(--muted)] mx-1">×</span>
                  <span className="text-fuchsia-400/90 font-medium tabular-nums">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <p className="text-[9px] text-[var(--muted)] mt-2 leading-snug">{t("traceSummarySkillHint")}</p>
    </div>
  );
}
