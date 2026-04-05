"use client";

import { useMemo } from "react";
import { useI18n } from "@/lib/i18n";

const preClass =
  "text-[10px] font-mono text-[var(--text)]/90 whitespace-pre-wrap break-words max-h-64 overflow-y-auto px-2 pb-2";

function strField(p: Record<string, unknown>, key: string): string {
  const v = p[key];
  if (typeof v === "string") return v;
  if (v == null) return "";
  return String(v);
}

function messageContentToString(content: unknown): string {
  if (typeof content === "string") return content;
  if (content == null) return "";
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

type ParseResult =
  | { ok: true; messages: Array<{ role: string; body: string }> }
  | { ok: false };

function parseMessagesJson(raw: string): ParseResult {
  if (!raw.trim()) return { ok: true, messages: [] };
  try {
    const data = JSON.parse(raw) as unknown;
    if (!Array.isArray(data)) return { ok: false };
    const messages: Array<{ role: string; body: string }> = [];
    for (const item of data) {
      if (!item || typeof item !== "object") continue;
      const m = item as Record<string, unknown>;
      const role = String(m.role ?? "?");
      const body = messageContentToString(m.content);
      messages.push({ role, body });
    }
    return { ok: true, messages };
  } catch {
    return { ok: false };
  }
}

export function TraceLlmPromptPayload({ payload }: { payload: Record<string, unknown> }) {
  const { t } = useI18n();
  const phase = strField(payload, "phase");
  const roundRaw = payload.round;
  const roundLabel =
    roundRaw === null || roundRaw === undefined || String(roundRaw).trim() === ""
      ? "—"
      : String(roundRaw);
  const system = strField(payload, "system");
  const user = strField(payload, "user");
  const messagesJson = strField(payload, "messages_json");

  const parsed = useMemo(() => parseMessagesJson(messagesJson), [messagesJson]);

  const meta = t("traceLlmPromptMeta", {
    phase: phase.trim() ? phase : "—",
    round: roundLabel,
    sysLen: String(system.length),
    userLen: String(user.length),
    mjLen: String(messagesJson.length),
  });

  return (
    <div className="border-t border-[var(--border)]/60 px-2 pb-2 pt-2 space-y-2">
      <p className="text-[10px] text-[var(--muted)] leading-relaxed px-1">{meta}</p>

      <details className="rounded-md border border-[var(--border)] bg-[var(--bg)]/40">
        <summary className="cursor-pointer px-2 py-1.5 text-[11px] font-medium text-[var(--text)] hover:bg-[var(--panel)]/60">
          {t("traceLlmPromptSystem")} ({system.length})
        </summary>
        <pre className={preClass}>{system.trim() ? system : t("traceLlmPromptEmpty")}</pre>
      </details>

      <details className="rounded-md border border-[var(--border)] bg-[var(--bg)]/40">
        <summary className="cursor-pointer px-2 py-1.5 text-[11px] font-medium text-[var(--text)] hover:bg-[var(--panel)]/60">
          {t("traceLlmPromptUser")} ({user.length})
        </summary>
        <pre className={preClass}>{user.trim() ? user : t("traceLlmPromptEmpty")}</pre>
      </details>

      <details className="rounded-md border border-[var(--border)] bg-[var(--bg)]/40">
        <summary className="cursor-pointer px-2 py-1.5 text-[11px] font-medium text-[var(--text)] hover:bg-[var(--panel)]/60">
          {parsed.ok
            ? t("traceLlmPromptMessagesList", { count: String(parsed.messages.length) })
            : t("traceLlmPromptMessages")}
        </summary>
        {parsed.ok ? (
          parsed.messages.length > 0 ? (
            <ul className="list-none space-y-1.5 px-1 pb-1">
              {parsed.messages.map((msg, i) => (
                <li key={`${msg.role}-${i}`}>
                  <details className="rounded border border-[var(--border)]/80 bg-[var(--panel)]/35">
                    <summary className="cursor-pointer px-2 py-1 text-[10px] font-mono text-[var(--text)] hover:bg-[var(--panel)]/50">
                      {t("traceLlmPromptMessageN", { n: String(i), role: msg.role })}
                      {msg.body.length ? ` · ${msg.body.length}` : ""}
                    </summary>
                    <pre className={preClass}>
                      {msg.body.trim() ? msg.body : t("traceLlmPromptEmpty")}
                    </pre>
                  </details>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[10px] text-[var(--muted)] px-2 pb-2">{t("traceLlmPromptNoMessages")}</p>
          )
        ) : (
          <p className="text-[10px] text-amber-600/90 dark:text-amber-400/90 px-2 pb-1">
            {t("traceLlmPromptParseError")}
          </p>
        )}
        <details className="mx-1 mb-2 rounded border border-dashed border-[var(--border)] bg-[var(--bg)]/20">
          <summary className="cursor-pointer px-2 py-1 text-[10px] text-[var(--muted)] hover:bg-[var(--panel)]/40">
            {t("traceLlmPromptMessagesRaw")} ({messagesJson.length})
          </summary>
          <pre className={preClass}>
            {messagesJson.trim() ? messagesJson : t("traceLlmPromptEmpty")}
          </pre>
        </details>
      </details>
    </div>
  );
}
