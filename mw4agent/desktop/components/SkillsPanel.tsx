"use client";

import { useCallback, useEffect, useState } from "react";
import { listSkills, type ListedSkill } from "@/lib/gateway";
import { useI18n } from "@/lib/i18n";

export function SkillsPanel() {
  const { t } = useI18n();
  const [skills, setSkills] = useState<ListedSkill[]>([]);
  const [count, setCount] = useState(0);
  const [version, setVersion] = useState<string | undefined>(undefined);
  const [sources, setSources] = useState<{ name: string; count: number }[]>(
    []
  );
  const [filteredOut, setFilteredOut] = useState<string[]>([]);
  const [promptTruncated, setPromptTruncated] = useState(false);
  const [promptCompact, setPromptCompact] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const res = await listSkills();
    setLoading(false);
    if (!res.ok) {
      setError(res.error || t("skillsError"));
      setSkills([]);
      setCount(0);
      setVersion(undefined);
      setSources([]);
      setFilteredOut([]);
      setPromptTruncated(false);
      setPromptCompact(false);
      return;
    }
    setSkills(res.skills);
    setCount(res.count);
    setVersion(res.version);
    setSources(res.sources);
    setFilteredOut(res.filteredOut ?? []);
    setPromptTruncated(res.promptTruncated ?? false);
    setPromptCompact(res.promptCompact ?? false);
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex flex-col h-full min-h-0 p-4 sm:p-6 max-w-5xl">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <div>
          <h2 className="text-lg font-semibold">{t("skillsTitle")}</h2>
          {!loading && !error ? (
            <p className="mt-0.5 text-xs text-[var(--muted)]">
              {t("skillsSummary", { count: String(count) })}
              {version ? ` · ${t("skillsVersion")}: ${version}` : ""}
              {promptCompact ? (
                <span className="ml-1">· {t("skillsPromptCompact")}</span>
              ) : null}
              {promptTruncated ? (
                <span className="ml-1">· {t("skillsPromptTruncated")}</span>
              ) : null}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className="text-sm px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
          onClick={() => void load()}
          disabled={loading}
        >
          {t("skillsRefresh")}
        </button>
      </div>

      {!loading && sources.length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
          {sources.map((s) => (
            <span
              key={s.name}
              className="rounded-md border border-[var(--border)] bg-[var(--panel)] px-2 py-0.5"
            >
              {s.name}: {s.count}
            </span>
          ))}
        </div>
      ) : null}

      {!loading && filteredOut.length > 0 ? (
        <p className="mb-3 text-xs text-amber-600/90 dark:text-amber-400/90">
          {t("skillsFilteredOut", { n: String(filteredOut.length) })}
        </p>
      ) : null}

      {loading ? (
        <p className="text-sm text-[var(--muted)]">{t("skillsLoading")}</p>
      ) : null}
      {!loading && error ? (
        <p className="text-sm text-red-500/90">{error}</p>
      ) : null}
      {!loading && !error && skills.length === 0 ? (
        <p className="text-sm text-[var(--muted)]">{t("skillsEmpty")}</p>
      ) : null}

      {!loading && skills.length > 0 ? (
        <div className="overflow-auto rounded-lg border border-[var(--border)] bg-[var(--panel)]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-[var(--border)] text-[var(--muted)] text-xs uppercase tracking-wide">
              <tr>
                <th className="px-3 py-2 font-medium">{t("skillName")}</th>
                <th className="px-3 py-2 font-medium hidden sm:table-cell">
                  {t("skillSource")}
                </th>
                <th className="px-3 py-2 font-medium hidden lg:table-cell">
                  {t("skillDescription")}
                </th>
                <th className="px-3 py-2 font-medium hidden md:table-cell">
                  {t("skillLocation")}
                </th>
              </tr>
            </thead>
            <tbody>
              {skills.map((s) => (
                <tr
                  key={s.name}
                  className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg)]/50"
                >
                  <td className="px-3 py-2 font-mono text-xs align-top">
                    {s.name}
                  </td>
                  <td className="px-3 py-2 text-xs align-top hidden sm:table-cell">
                    {s.source || "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-[var(--muted)] max-w-md align-top hidden lg:table-cell">
                    {s.description || "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-[var(--muted)] max-w-xs truncate align-top hidden md:table-cell">
                    {s.location || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
