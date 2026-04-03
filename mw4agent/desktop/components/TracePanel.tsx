"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  orchestrateGet,
  orchestrateList,
  orchestrateTraceList,
  type OrchTraceEvent,
  type OrchestrateListItem,
} from "@/lib/gateway";
import {
  formatOrchTraceSummary,
  formatTraceEventTs,
  traceEventTypeClass,
} from "@/lib/orchestrateTraceFormat";
import { useI18n } from "@/lib/i18n";

function payloadPreviewJson(payload: unknown): string {
  if (payload == null) return "";
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

export function TracePanel() {
  const { t } = useI18n();
  const [orches, setOrches] = useState<OrchestrateListItem[]>([]);
  const [selectedOrchId, setSelectedOrchId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [traceEnabled, setTraceEnabled] = useState<boolean | null>(null);
  const [traceSeq, setTraceSeq] = useState(0);
  const [events, setEvents] = useState<OrchTraceEvent[]>([]);
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const afterSeqRef = useRef(-1);

  const loadOrches = useCallback(async () => {
    const r = await orchestrateList();
    if (!r.ok) {
      setError(r.error || t("orchestrateError"));
      return;
    }
    setError(null);
    setOrches(r.orchestrations);
  }, [t]);

  useEffect(() => {
    void loadOrches();
  }, [loadOrches]);

  useEffect(() => {
    const id = selectedOrchId.trim();
    afterSeqRef.current = -1;
    setEvents([]);
    setExpandedSeq(null);
    setTraceEnabled(null);
    setStatus("");
    setTraceSeq(0);
    if (!id) return;

    let cancelled = false;
    const tick = async () => {
      const g = await orchestrateGet(id);
      if (cancelled) return;
      if (!g.ok) {
        setError(g.error || t("orchestrateError"));
        return;
      }
      setError(null);
      setStatus(g.status);
      setTraceEnabled(g.orchTraceEnabled === true);
      setTraceSeq(g.orchTraceSeq ?? 0);

      if (!g.orchTraceEnabled) {
        afterSeqRef.current = -1;
        setEvents([]);
        return;
      }

      const tr = await orchestrateTraceList(id, afterSeqRef.current, 500);
      if (cancelled || !tr.ok) return;
      if (tr.events.length) {
        setEvents((prev) => [...prev, ...tr.events]);
        let maxS = afterSeqRef.current;
        for (const ev of tr.events) {
          const s = Number(ev.seq) || 0;
          if (s > maxS) maxS = s;
        }
        afterSeqRef.current = maxS;
      }
    };

    void tick();
    const timer = window.setInterval(tick, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedOrchId, t]);

  const doRefreshFull = useCallback(() => {
    afterSeqRef.current = -1;
    setEvents([]);
    setExpandedSeq(null);
  }, []);

  const selectedTitle = (() => {
    const o = orches.find((x) => x.orchId === selectedOrchId);
    const n = (o?.name || "").trim();
    return n || (selectedOrchId ? selectedOrchId.slice(0, 8) : "");
  })();

  return (
    <div className="flex h-full min-h-0 w-full">
      <div className="w-64 shrink-0 border-r border-[var(--border)] bg-[var(--panel)] p-3 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Image src="/icons/trace.png" alt="" width={22} height={22} className="h-[22px] w-[22px] object-contain" />
          <div className="text-sm font-semibold">{t("traceNav")}</div>
        </div>
        <p className="text-[10px] text-[var(--muted)] leading-relaxed">{t("tracePageHint")}</p>
        <button
          type="button"
          className="text-xs px-2 py-1 rounded border border-[var(--border)] bg-[var(--bg)] hover:opacity-90"
          onClick={() => void loadOrches()}
        >
          {t("traceRefreshList")}
        </button>
        <div className="min-h-0 flex-1 overflow-auto space-y-1">
          {orches.length === 0 ? (
            <div className="text-xs text-[var(--muted)]">{t("orchestrateEmptyList")}</div>
          ) : (
            orches.map((o) => {
              const active = o.orchId === selectedOrchId;
              const title = (o.name || "").trim() || o.orchId.slice(0, 8);
              return (
                <button
                  key={o.orchId}
                  type="button"
                  onClick={() => setSelectedOrchId(o.orchId)}
                  className={`w-full rounded-lg border border-[var(--border)] px-3 py-2 text-left hover:opacity-90 ${
                    active ? "bg-[var(--accent)] text-white" : "bg-[var(--panel)]"
                  }`}
                >
                  <div className="text-xs font-medium truncate">{title}</div>
                  <div
                    className={`text-[10px] truncate ${active ? "text-white/85" : "text-[var(--muted)]"}`}
                  >
                    {o.status}
                    {o.orchTraceEnabled ? ` · ${t("traceBadgeOn")}` : ` · ${t("traceBadgeOff")}`}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div>
            <h1 className="text-lg font-semibold text-[var(--text)]">{t("tracePageTitle")}</h1>
            {selectedOrchId ? (
              <p className="text-[10px] text-[var(--muted)] font-mono truncate max-w-[60vw]" title={selectedOrchId}>
                {selectedTitle} · {selectedOrchId}
              </p>
            ) : (
              <p className="text-xs text-[var(--muted)]">{t("tracePickOrch")}</p>
            )}
          </div>
          {selectedOrchId && traceEnabled ? (
            <button
              type="button"
              className="text-xs px-3 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
              onClick={doRefreshFull}
            >
              {t("traceRefreshTimeline")}
            </button>
          ) : null}
        </div>

        {error ? <p className="text-xs text-red-500/90 mb-2">{error}</p> : null}

        {!selectedOrchId ? (
          <div className="flex flex-1 items-center justify-center text-sm text-[var(--muted)]">
            {t("tracePickOrch")}
          </div>
        ) : traceEnabled === false ? (
          <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-4 text-sm text-[var(--muted)]">
            {t("traceDisabledHint")}
          </div>
        ) : (
          <div className="min-h-0 flex-1 flex flex-col gap-2">
            <div className="text-[10px] text-[var(--muted)] flex flex-wrap gap-3">
              <span>
                {t("orchestrateStatus")}: {status || "—"}
              </span>
              <span>
                {t("traceNextSeqLabel")}: {traceSeq}
              </span>
              <span>
                {t("traceEventsLoaded")}: {events.length}
              </span>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3 space-y-0">
              {events.length === 0 ? (
                <p className="text-xs text-[var(--muted)] py-6 text-center">{t("orchestrateTraceEmpty")}</p>
              ) : (
                <ul className="relative pl-4 border-l-2 border-[var(--border)] space-y-3 ml-2 py-1">
                  {events.map((ev, i) => {
                    const seq = ev.seq != null ? Number(ev.seq) : i;
                    const typ = String(ev.type ?? "?");
                    const tsRaw = ev.ts;
                    const tsNum = typeof tsRaw === "number" ? tsRaw : undefined;
                    const expanded = expandedSeq === seq;
                    return (
                      <li key={`${String(seq)}-${i}`} className="relative pl-4">
                        <span
                          className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--accent)] ring-2 ring-[var(--panel)]"
                          aria-hidden
                        />
                        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)]/50 overflow-hidden">
                          <button
                            type="button"
                            className="w-full text-left px-3 py-2 flex flex-wrap items-start gap-2 hover:bg-[var(--bg)]/80"
                            onClick={() => setExpandedSeq(expanded ? null : seq)}
                          >
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 font-mono ${traceEventTypeClass(typ)}`}
                            >
                              {typ}
                            </span>
                            <span className="text-[10px] text-[var(--muted)] font-mono shrink-0">#{seq}</span>
                            <span className="text-[10px] text-[var(--muted)] font-mono shrink-0">
                              {formatTraceEventTs(tsNum)}
                            </span>
                            <span className="text-xs text-[var(--text)] flex-1 min-w-0 break-words">
                              {formatOrchTraceSummary(ev)}
                            </span>
                            <span className="text-[10px] text-[var(--muted)] shrink-0">{expanded ? "▼" : "▶"}</span>
                          </button>
                          {expanded && ev.payload != null ? (
                            <pre className="text-[10px] font-mono px-3 pb-3 pt-0 text-[var(--text)]/90 whitespace-pre-wrap break-words max-h-64 overflow-y-auto border-t border-[var(--border)]/60">
                              {payloadPreviewJson(ev.payload)}
                            </pre>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
