"use client";

import Image from "next/image";
import { useTheme } from "next-themes";
import { useCallback, useState } from "react";
import { getGatewayBaseUrl } from "@/lib/gateway";
import { useI18n } from "@/lib/i18n";
import { AgentsPanel } from "@/components/AgentsPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { SkillsPanel } from "@/components/SkillsPanel";

type MainView = "home" | "agents" | "skills";

export function AppShell() {
  const { t, locale, setLocale } = useI18n();
  const { theme, setTheme } = useTheme();
  const [mainView, setMainView] = useState<MainView>("home");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatSessionKey, setChatSessionKey] = useState(0);
  const [chatAgentId, setChatAgentId] = useState<string | undefined>(undefined);

  const openNewTask = useCallback(() => {
    setChatAgentId(undefined);
    setChatSessionKey((k) => k + 1);
    setChatOpen(true);
  }, []);

  const openChatWithAgent = useCallback((agentId: string) => {
    setChatAgentId(agentId);
    setChatSessionKey((k) => k + 1);
    setChatOpen(true);
  }, []);

  const closeChat = useCallback(() => {
    setChatOpen(false);
  }, []);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[var(--bg)] text-[var(--text)]">
      <aside className="flex w-56 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--panel)]">
        <div className="border-b border-[var(--border)] px-4 py-4">
          <div className="flex items-center gap-2.5">
            <Image
              src="/icons/planet.png"
              alt="Orbit"
              width={28}
              height={28}
              className="h-7 w-7 shrink-0 rounded-md object-contain"
            />
            <div className="text-lg font-semibold tracking-tight text-[var(--text)]">
              {t("brandOrbit")}
            </div>
          </div>
        </div>

        <nav className="flex flex-col gap-0.5 p-2">
          <button
            type="button"
            onClick={openNewTask}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/session.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("newTask")}
          </button>
          <button
            type="button"
            onClick={() => {
              setMainView("agents");
              setChatOpen(false);
            }}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              mainView === "agents" && !chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/robot.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("myAgents")}
          </button>
          <button
            type="button"
            onClick={() => {
              setMainView("skills");
              setChatOpen(false);
            }}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              mainView === "skills" && !chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/skill.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("skillsNav")}
          </button>
        </nav>

        <div className="mt-auto border-t border-[var(--border)] p-3 space-y-2">
          <div className="text-[10px] text-[var(--muted)] truncate" title={getGatewayBaseUrl()}>
            {t("gatewayUrl")}
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              className={`flex-1 text-[10px] py-1 rounded border border-[var(--border)] ${
                locale === "en" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setLocale("en")}
            >
              EN
            </button>
            <button
              type="button"
              className={`flex-1 text-[10px] py-1 rounded border border-[var(--border)] ${
                locale === "zh-CN" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setLocale("zh-CN")}
            >
              中文
            </button>
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              className={`flex-1 text-[10px] py-1 rounded border border-[var(--border)] ${
                theme === "light" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setTheme("light")}
            >
              {t("themeLight")}
            </button>
            <button
              type="button"
              className={`flex-1 text-[10px] py-1 rounded border border-[var(--border)] ${
                theme === "dark" ? "bg-[var(--accent)] text-white" : ""
              }`}
              onClick={() => setTheme("dark")}
            >
              {t("themeDark")}
            </button>
          </div>
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {mainView === "home" && !chatOpen && (
          <div className="flex flex-1 flex-col items-center justify-center p-8 text-center">
            <div className="flex items-center gap-3">
              <Image
                src="/icons/planet.png"
                alt="Orbit"
                width={40}
                height={40}
                className="h-10 w-10 shrink-0 rounded-lg object-contain"
              />
              <h1 className="text-2xl font-semibold text-[var(--text)]">
                {t("brandOrbit")}
              </h1>
            </div>
            <p className="mt-2 max-w-md text-sm text-[var(--muted)]">
              {t("homeBlurb")}
            </p>
            <button
              type="button"
              className="mt-6 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-medium text-white"
              onClick={openNewTask}
            >
              {t("newTask")}
            </button>
          </div>
        )}
        {mainView === "agents" && (
          <AgentsPanel onOpenChatWithAgent={openChatWithAgent} />
        )}
        {mainView === "skills" && <SkillsPanel />}
      </section>

      {chatOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4"
          role="presentation"
          onClick={closeChat}
        >
          <div
            className="flex h-[min(90vh,820px)] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg)] shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby="orbit-chat-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div
              id="orbit-chat-title"
              className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-4 py-3"
            >
              <span className="text-sm font-semibold">{t("newTask")}</span>
              <button
                type="button"
                className="rounded-md px-2 py-1 text-xs text-[var(--muted)] hover:bg-[var(--panel)]"
                onClick={closeChat}
              >
                {t("closeDialog")}
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <ChatPanel
                sessionResetKey={chatSessionKey}
                initialAgentId={chatAgentId}
                showTopBar={false}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
