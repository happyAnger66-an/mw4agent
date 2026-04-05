"use client";

import Image from "next/image";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useState } from "react";
import { getGatewayBaseUrl } from "@/lib/gateway";
import { useI18n } from "@/lib/i18n";
import { AgentsPanel } from "@/components/AgentsPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { OrchestratePanel } from "@/components/OrchestratePanel";
import { TracePanel } from "@/components/TracePanel";
import { SettingsPanel } from "@/components/SettingsPanel";
import { SkillsPanel } from "@/components/SkillsPanel";
import { StatsPanel } from "@/components/StatsPanel";

type MainView = "home" | "agents" | "skills" | "orchestrate" | "trace" | "stats" | "settings";

const NAV_COLLAPSED_STORAGE_KEY = "orbit-desktop-nav-collapsed";

export function AppShell() {
  const { t, locale, setLocale } = useI18n();
  const { theme, setTheme } = useTheme();
  const [mainView, setMainView] = useState<MainView>("home");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatSessionKey, setChatSessionKey] = useState(0);
  const [chatAgentId, setChatAgentId] = useState<string | undefined>(undefined);
  const [orchOpenKey, setOrchOpenKey] = useState(0);
  const [navCollapsed, setNavCollapsed] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(NAV_COLLAPSED_STORAGE_KEY) === "1") {
        setNavCollapsed(true);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const setNavCollapsedPersist = useCallback((collapsed: boolean) => {
    setNavCollapsed(collapsed);
    try {
      localStorage.setItem(NAV_COLLAPSED_STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, []);

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
      {navCollapsed ? (
        <div className="flex w-11 shrink-0 flex-col items-center border-r border-[var(--border)] bg-[var(--panel)] pt-2 pb-2">
          <button
            type="button"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg)] hover:opacity-90"
            title={t("navShowSidebar")}
            aria-label={t("navShowSidebar")}
            onClick={() => setNavCollapsedPersist(false)}
          >
            <Image
              src="/icons/redisplay.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 object-contain"
            />
          </button>
        </div>
      ) : (
        <aside className="flex w-56 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--panel)]">
          <div className="border-b border-[var(--border)] px-3 py-3 sm:px-4 sm:py-4">
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2.5">
                <Image
                  src="/icons/planet.png"
                  alt="Orbit"
                  width={28}
                  height={28}
                  className="h-7 w-7 shrink-0 rounded-md object-contain"
                />
                <div className="truncate text-lg font-semibold tracking-tight text-[var(--text)]">
                  {t("brandOrbit")}
                </div>
              </div>
              <button
                type="button"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg)] hover:opacity-90"
                title={t("navHideSidebar")}
                aria-label={t("navHideSidebar")}
                onClick={() => setNavCollapsedPersist(true)}
              >
                <Image
                  src="/icons/hide.png"
                  alt=""
                  width={20}
                  height={20}
                  className="h-5 w-5 object-contain"
                />
              </button>
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
              setMainView("orchestrate");
              setChatOpen(false);
              setOrchOpenKey((k) => k + 1);
            }}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              mainView === "orchestrate" && !chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/group.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("orchestrateNav")}
          </button>
          <button
            type="button"
            onClick={() => {
              setMainView("trace");
              setChatOpen(false);
            }}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              mainView === "trace" && !chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/trace.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("traceNav")}
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
          <button
            type="button"
            onClick={() => {
              setMainView("stats");
              setChatOpen(false);
            }}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              mainView === "stats" && !chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/stats.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("statsNav")}
          </button>
          <button
            type="button"
            onClick={() => {
              setMainView("settings");
              setChatOpen(false);
            }}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
              mainView === "settings" && !chatOpen
                ? "bg-[var(--accent)] text-white"
                : "text-[var(--text)] hover:bg-[var(--bg)]/80"
            }`}
          >
            <Image
              src="/icons/settings.png"
              alt=""
              width={20}
              height={20}
              className="h-5 w-5 shrink-0 object-contain opacity-90"
            />
            {t("settingsNav")}
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
      )}

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--bg)]">
        {chatOpen ? (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <ChatPanel
              sessionResetKey={chatSessionKey}
              initialAgentId={chatAgentId}
              showTopBar
              onClose={closeChat}
            />
          </div>
        ) : mainView === "home" ? (
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
        ) : mainView === "agents" ? (
          <AgentsPanel onOpenChatWithAgent={openChatWithAgent} />
        ) : mainView === "orchestrate" ? (
          <OrchestratePanel autoOpenKey={orchOpenKey} />
        ) : mainView === "trace" ? (
          <TracePanel />
        ) : mainView === "stats" ? (
          <StatsPanel />
        ) : mainView === "settings" ? (
          <SettingsPanel />
        ) : (
          <SkillsPanel />
        )}
      </section>
    </div>
  );
}
