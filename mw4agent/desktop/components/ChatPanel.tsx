"use client";

import { useTheme } from "next-themes";
import { useCallback, useEffect, useState } from "react";
import { callRpc, getGatewayBaseUrl, type AgentWsEvent } from "@/lib/gateway";
import { useGatewayWs } from "@/lib/gateway-ws-context";
import { useI18n } from "@/lib/i18n";
import { isTauri } from "@/lib/tauri";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  reasoning?: string;
  step?: string;
  runId?: string;
  streaming?: boolean;
};

function newId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `m-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

type ChatPanelProps = {
  /** Bump to reset session + messages when opening a fresh task dialog */
  sessionResetKey?: number;
  /** Pre-fill target agent (from My Agents) */
  initialAgentId?: string;
  /** Optional title row (e.g. dialog header lives outside) */
  showTopBar?: boolean;
  onClose?: () => void;
};

export function ChatPanel({
  sessionResetKey = 0,
  initialAgentId,
  showTopBar = true,
  onClose,
}: ChatPanelProps) {
  const { t, locale, setLocale } = useI18n();
  const { theme, setTheme } = useTheme();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const { subscribe, connectionState } = useGatewayWs();
  const [agentId, setAgentId] = useState(initialAgentId?.trim() || "main");
  const [sessionKey, setSessionKey] = useState("desktop-app");
  const [sessionId, setSessionId] = useState("");

  useEffect(() => {
    setSessionId(newId());
    setMessages([]);
    setInput("");
    setBusy(false);
    setAgentId(initialAgentId?.trim() || "main");
  }, [sessionResetKey, initialAgentId]);

  /**
   * Apply an update to the assistant row for this run_id.
   * WS events often arrive before React applies runId from the RPC response; in that case
   * attach to the latest streaming assistant row that has no runId yet.
   */
  const updateAssistantForRun = useCallback(
    (runId: string, fn: (m: ChatMessage) => ChatMessage) => {
      if (!runId) return;
      setMessages((prev) => {
        const withIndex = [...prev].map((m, i) => ({ m, i }));
        const rev = [...withIndex].reverse();
        const byRun = rev.find(
          (x) => x.m.role === "assistant" && x.m.runId === runId
        );
        const pending = rev.find(
          (x) =>
            x.m.role === "assistant" &&
            x.m.streaming === true &&
            !String(x.m.runId || "").trim()
        );
        const hit = byRun ?? pending;
        if (!hit) return prev;
        const idx = hit.i;
        const next = [...prev];
        const merged: ChatMessage = { ...next[idx], runId };
        next[idx] = fn(merged);
        return next;
      });
    },
    []
  );

  useEffect(() => {
    return subscribe((payload: AgentWsEvent) => {
      const runId = payload.run_id || (payload.data?.run_id as string) || "";
      const stream = payload.stream;
      const data = payload.data || {};

      if (stream === "lifecycle" && runId) {
        const phase = data.phase as string;
        if (phase === "start") {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            step: t("stepThinking"),
            text: "",
            streaming: true,
          }));
        }
        if (phase === "end" || phase === "error") {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            streaming: false,
            step: undefined,
          }));
          setBusy(false);
        }
        return;
      }

      if (stream === "tool" && runId) {
        const typ = data.type as string;
        const name = String(data.tool_name || "?");
        if (typ === "start") {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            step: t("stepCallingTool", { name }),
          }));
        } else if (typ === "end" || typ === "error") {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            step: t("stepToolDone", { name }),
          }));
        }
        return;
      }

      if (stream === "assistant" && runId) {
        if (data.reasoning != null) {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            reasoning: String(data.reasoning).trim(),
          }));
        }
        const text =
          data.text != null
            ? String(data.text)
            : data.delta != null
              ? String(data.delta)
              : "";
        const textTrimmed = text.trim();
        const isFinal = data.final === true;
        if (!textTrimmed) {
          if (isFinal) {
            updateAssistantForRun(runId, (m) => ({
              ...m,
              step: undefined,
              streaming: false,
            }));
          }
          return;
        }
        if (textTrimmed === "Processing..." || textTrimmed === "思考中…") {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            step: t("stepThinking"),
          }));
          return;
        }
        updateAssistantForRun(runId, (m) => ({
          ...m,
          step: undefined,
          text: isFinal ? textTrimmed : (m.text || "") + textTrimmed,
        }));
      }

      if (stream === "llm" && runId) {
        if (data.thinking != null && String(data.thinking).trim()) {
          updateAssistantForRun(runId, (m) => ({
            ...m,
            reasoning: String(data.thinking).trim(),
          }));
        }
        const content =
          data.content != null
            ? String(data.content)
            : data.text != null
              ? String(data.text)
              : "";
        const c = content.trim();
        if (c) {
          updateAssistantForRun(runId, (m) => {
            const hasText = Boolean((m.text || "").trim());
            return {
              ...m,
              step: undefined,
              text: hasText ? m.text : c,
            };
          });
        }
      }
    });
  }, [subscribe, t, updateAssistantForRun]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", text, runId: undefined },
    ]);

    const idem = `desktop-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;

    const params: Record<string, unknown> = {
      message: text,
      sessionKey: sessionKey.trim() || "desktop-app",
      agentId: agentId.trim() || "main",
      idempotencyKey: idem,
      channel: "desktop",
      reasoningLevel: "off",
    };
    if (sessionId.trim()) {
      params.sessionId = sessionId.trim();
    }

    setMessages((prev) => [
      ...prev,
      {
        id: newId(),
        role: "assistant",
        text: "",
        runId: undefined,
        streaming: true,
        step: t("stepThinking"),
      },
    ]);

    try {
      const json = await callRpc("agent", params);

      const runId =
        (json.runId as string) ||
        (json.payload && (json.payload.runId as string)) ||
        "";
      if (!json.ok || !runId) {
        const err = json.error?.message || t("errorRpc");
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant" && last.streaming) {
            next[next.length - 1] = {
              ...last,
              text: err,
              streaming: false,
              step: undefined,
            };
          }
          return next;
        });
        setBusy(false);
        return;
      }

      const sidBack = json.payload?.sessionId;
      if (typeof sidBack === "string" && sidBack.trim()) {
        setSessionId(sidBack.trim());
      }

      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = { ...last, runId };
        }
        return next;
      });

      // Fallback: if the UI missed WS events (late mount / reconnect), block until run completes.
      void (async () => {
        const rid = runId;
        try {
          const w = await callRpc("agent.wait", {
            runId: rid,
            timeoutMs: 180_000,
          });
          if (!w.ok || !w.payload) {
            setBusy(false);
            return;
          }
          const reply = w.payload.replyText;
          if (typeof reply === "string" && reply.trim()) {
            setMessages((prev) => {
              const withIndex = [...prev].map((m, i) => ({ m, i }));
              const rev = [...withIndex].reverse();
              const hit = rev.find(
                (x) => x.m.role === "assistant" && x.m.runId === rid
              );
              if (!hit) return prev;
              const cur = hit.m;
              if ((cur.text || "").trim() && cur.streaming === false) {
                return prev;
              }
              const next = [...prev];
              next[hit.i] = {
                ...cur,
                text: reply.trim(),
                streaming: false,
                step: undefined,
              };
              return next;
            });
          }
          const st = String(w.payload.status || "");
          if (st === "ok" || st === "error" || st === "timeout") {
            setBusy(false);
            if (st === "error" && w.payload.error != null) {
              const er = w.payload.error;
              const errMsg =
                typeof er === "string"
                  ? er
                  : typeof er === "object" &&
                      er !== null &&
                      "message" in er
                    ? String((er as { message?: unknown }).message || "")
                    : t("errorRpc");
              if (errMsg) {
                setMessages((prev) => {
                  const withIndex = [...prev].map((m, i) => ({ m, i }));
                  const rev = [...withIndex].reverse();
                  const hit = rev.find(
                    (x) => x.m.role === "assistant" && x.m.runId === rid
                  );
                  if (!hit) return prev;
                  const cur = hit.m;
                  if ((cur.text || "").trim()) return prev;
                  const next = [...prev];
                  next[hit.i] = {
                    ...cur,
                    text: errMsg,
                    streaming: false,
                    step: undefined,
                  };
                  return next;
                });
              }
            }
          }
        } catch {
          setBusy(false);
        }
      })();
    } catch (e) {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = {
            ...last,
            text: String(e),
            streaming: false,
            step: undefined,
          };
        }
        return next;
      });
      setBusy(false);
    }
  };

  const newSession = () => {
    setSessionId(newId());
    setMessages([]);
  };

  const base = getGatewayBaseUrl();
  const connLabel =
    connectionState === "connected"
      ? t("connected")
      : connectionState === "reconnecting"
        ? t("reconnecting")
        : connectionState === "connecting"
          ? t("reconnecting")
          : t("disconnected");

  return (
    <div className="flex flex-col h-full min-h-0 w-full max-w-5xl mx-auto px-3 sm:px-4">
      {showTopBar ? (
        <header className="flex flex-wrap items-center gap-2 py-3 border-b border-[var(--border)] shrink-0">
          <div className="flex flex-col min-w-0 flex-1">
            <h2 className="text-base font-semibold tracking-tight truncate">
              {t("newTask")}
            </h2>
            <p className="text-xs text-[var(--muted)] truncate">{t("subtitle")}</p>
          </div>
          <span
            className={`text-xs px-2 py-0.5 rounded-full border border-[var(--border)] ${
              connectionState === "connected"
                ? "text-emerald-500"
                : "text-[var(--muted)]"
            }`}
            title={base}
          >
            {connLabel}
          </span>
          {isTauri() && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--panel)] border border-[var(--border)] text-[var(--muted)]">
              Native
            </span>
          )}
          <div className="flex items-center gap-1">
            <span className="text-xs text-[var(--muted)]">{t("language")}</span>
            <button
              type="button"
              className={`text-xs px-2 py-1 rounded border border-[var(--border)] ${
                locale === "en" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setLocale("en")}
            >
              EN
            </button>
            <button
              type="button"
              className={`text-xs px-2 py-1 rounded border border-[var(--border)] ${
                locale === "zh-CN" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setLocale("zh-CN")}
            >
              中文
            </button>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className={`text-xs px-2 py-1 rounded border border-[var(--border)] ${
                theme === "light" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setTheme("light")}
            >
              {t("themeLight")}
            </button>
            <button
              type="button"
              className={`text-xs px-2 py-1 rounded border border-[var(--border)] ${
                theme === "dark" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setTheme("dark")}
            >
              {t("themeDark")}
            </button>
          </div>
          <button
            type="button"
            className="text-xs px-2 py-1 rounded border border-[var(--border)] text-[var(--muted)]"
            onClick={newSession}
          >
            {t("newChat")}
          </button>
          {onClose ? (
            <button
              type="button"
              className="text-xs px-2 py-1 rounded border border-[var(--border)] text-[var(--muted)]"
              onClick={onClose}
            >
              {t("closeDialog")}
            </button>
          ) : null}
        </header>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 py-2 text-xs shrink-0">
        <label className="flex flex-col gap-0.5 sm:col-span-1">
          <span className="text-[var(--muted)]">{t("gatewayUrl")}</span>
          <code className="truncate px-2 py-1 rounded bg-[var(--panel)] border border-[var(--border)]">
            {base}
          </code>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[var(--muted)]">{t("agentId")}</span>
          <input
            className="px-2 py-1 rounded bg-[var(--panel)] border border-[var(--border)] text-[var(--text)]"
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            disabled={busy}
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[var(--muted)]">{t("sessionKey")}</span>
          <input
            className="px-2 py-1 rounded bg-[var(--panel)] border border-[var(--border)] text-[var(--text)]"
            value={sessionKey}
            onChange={(e) => setSessionKey(e.target.value)}
            disabled={busy}
          />
        </label>
      </div>

      <main className="flex-1 overflow-y-auto py-3 space-y-3 min-h-0">
        {messages.length === 0 && (
          <p className="text-sm text-[var(--muted)] text-center py-8">
            {t("placeholder")}
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`rounded-lg px-3 py-2 max-w-[min(100%,42rem)] ${
              m.role === "user"
                ? "ml-auto bg-[var(--user-bg)] text-[var(--text)]"
                : "mr-auto bg-[var(--assistant-bg)] border border-[var(--border)]"
            }`}
          >
            <div className="text-[10px] uppercase tracking-wide text-[var(--muted)] mb-1">
              {m.role === "user" ? t("metaYou") : t("metaAssistant")}
              {m.runId ? ` · ${m.runId.slice(0, 8)}…` : ""}
            </div>
            {m.step ? (
              <div className="text-xs text-[var(--muted)] mb-1">{m.step}</div>
            ) : null}
            {m.reasoning ? (
              <div className="text-xs text-[var(--muted)] border-l-2 border-[var(--accent)] pl-2 mb-2 whitespace-pre-wrap">
                <span className="font-medium">{t("reasoning")}: </span>
                {m.reasoning}
              </div>
            ) : null}
            <div className="text-sm whitespace-pre-wrap">{m.text}</div>
          </div>
        ))}
      </main>

      <footer className="py-3 border-t border-[var(--border)] shrink-0">
        <div className="flex gap-2">
          <textarea
            className="flex-1 min-h-[44px] max-h-40 px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-sm resize-y"
            placeholder={t("placeholder")}
            value={input}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendMessage();
              }
            }}
          />
          <button
            type="button"
            className="px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium disabled:opacity-50"
            disabled={busy || !input.trim()}
            onClick={() => void sendMessage()}
          >
            {t("send")}
          </button>
        </div>
      </footer>
    </div>
  );
}
