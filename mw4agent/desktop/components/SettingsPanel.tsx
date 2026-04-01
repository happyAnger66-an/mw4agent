"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  agentsUpdateSkills,
  configSectionGet,
  configSectionSet,
  configSectionsList,
  listAgents,
  llmProvidersList,
  llmTest,
  type LlmProviderInfo,
} from "@/lib/gateway";
import { useI18n } from "@/lib/i18n";

type JsonObject = Record<string, unknown>;
type SettingsMode = "form" | "json";

const FORM_SECTIONS = ["llm", "tools", "memory", "skills", "session", "channels"] as const;

function safeJsonParseObject(text: string): { ok: true; value: JsonObject } | { ok: false } {
  try {
    const v = JSON.parse(text || "{}");
    if (!v || typeof v !== "object" || Array.isArray(v)) return { ok: false };
    return { ok: true, value: v as JsonObject };
  } catch {
    return { ok: false };
  }
}

function stringifyObject(value: JsonObject): string {
  return JSON.stringify(value || {}, null, 2);
}

function readBool(v: unknown, fallback = false): boolean {
  return typeof v === "boolean" ? v : fallback;
}
function readString(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function readNumber(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "string" && v.trim()) return v;
  return "";
}

function parseCsvList(value: string): string[] {
  const parts = (value || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return Array.from(new Set(parts));
}

function formatCsvList(list: unknown): string {
  if (!Array.isArray(list)) return "";
  return list
    .map((x) => String(x || "").trim())
    .filter(Boolean)
    .join(", ");
}

export function SettingsPanel() {
  const { t } = useI18n();
  const [sections, setSections] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedBanner, setSavedBanner] = useState<string | null>(null);
  const [editor, setEditor] = useState<string>("{}");
  const [mode, setMode] = useState<SettingsMode>("form");
  const [selectedChannelType, setSelectedChannelType] = useState<string>("");
  const [channelJsonEditor, setChannelJsonEditor] = useState<string>("{}");
  const parsedEditor = useMemo(() => safeJsonParseObject(editor), [editor]);
  const [llmProviders, setLlmProviders] = useState<LlmProviderInfo[]>([]);
  const [llmTestResult, setLlmTestResult] = useState<{ ok: boolean; message: string; preview?: string | null } | null>(
    null
  );
  const [llmTesting, setLlmTesting] = useState(false);

  const [listedAgents, setListedAgents] = useState<{ agentId: string; skills?: string[] | null }[]>([]);
  const [skillsAgentId, setSkillsAgentId] = useState("");
  const [skillsAgentCsv, setSkillsAgentCsv] = useState("");
  const [skillsAgentBusy, setSkillsAgentBusy] = useState(false);
  const [skillsAgentBanner, setSkillsAgentBanner] = useState<string | null>(null);
  const [skillsAgentsError, setSkillsAgentsError] = useState<string | null>(null);

  const loadSections = useCallback(async () => {
    setError(null);
    const r = await configSectionsList();
    if (!r.ok) {
      setError(r.error || t("settingsLoadError"));
      return;
    }
    const existing = (r.sections || []).filter((s) => typeof s === "string" && s.trim());
    const merged = Array.from(new Set([...FORM_SECTIONS, ...existing])).sort((a, b) =>
      a.localeCompare(b)
    );
    setSections(merged);
    if (!selected && merged.length) {
      // Prefer first form section for empty config.
      setSelected(FORM_SECTIONS[0]);
    }
  }, [selected, t]);

  const loadSelected = useCallback(
    async (section: string) => {
      const sec = section.trim();
      if (!sec) return;
      setLoading(true);
      setError(null);
      try {
        const r = await configSectionGet(sec);
        if (!r.ok) {
          setError(r.error || t("settingsLoadError"));
          return;
        }
        setEditor(stringifyObject(r.value || {}));
      } finally {
        setLoading(false);
      }
    },
    [t]
  );

  useEffect(() => {
    void loadSections();
  }, [loadSections]);

  useEffect(() => {
    // Load provider catalog for dropdown/default base_url.
    void (async () => {
      const r = await llmProvidersList();
      if (!r.ok) return;
      setLlmProviders(r.providerInfos || []);
    })();
  }, []);

  useEffect(() => {
    if (!selected) return;
    void loadSelected(selected);
  }, [loadSelected, selected]);

  useEffect(() => {
    if (selected.trim() !== "skills") return;
    void (async () => {
      setSkillsAgentsError(null);
      const r = await listAgents();
      if (!r.ok) {
        setSkillsAgentsError(r.error || "agents.list failed");
        setListedAgents([]);
        return;
      }
      setListedAgents(r.agents);
      setSkillsAgentId((prev) => prev || r.agents[0]?.agentId || "main");
    })();
  }, [selected]);

  useEffect(() => {
    if (selected.trim() !== "skills") return;
    const a = listedAgents.find((x) => x.agentId === skillsAgentId);
    if (!a) return;
    setSkillsAgentCsv(formatCsvList(a.skills ?? []));
  }, [selected, skillsAgentId, listedAgents]);

  useEffect(() => {
    if (selected.trim() !== "channels") return;
    if (!parsedEditor.ok) return;
    const obj = parsedEditor.value;
    const keys = Object.keys(obj);
    const nextType = selectedChannelType || (keys.includes("feishu") ? "feishu" : keys[0] || "");
    setSelectedChannelType(nextType);
  }, [parsedEditor, selected, selectedChannelType]);

  useEffect(() => {
    if (selected.trim() !== "channels") return;
    if (!parsedEditor.ok) return;
    const obj = parsedEditor.value;
    const active = selectedChannelType.trim();
    const raw = active ? (obj as JsonObject)[active] : undefined;
    const cfg =
      raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as JsonObject) : ({} as JsonObject);
    setChannelJsonEditor(stringifyObject(cfg));
  }, [parsedEditor, selected, selectedChannelType]);

  const canSave = useMemo(() => selected.trim().length > 0 && !saving, [saving, selected]);

  const doSave = useCallback(async () => {
    if (!selected.trim()) return;
    setSavedBanner(null);
    setError(null);
    const parsed = safeJsonParseObject(editor);
    if (!parsed.ok) {
      setError(t("settingsInvalidJson"));
      return;
    }
    setSaving(true);
    try {
      const r = await configSectionSet(selected, parsed.value);
      if (!r.ok) {
        setError(r.error || t("settingsSaveError"));
        return;
      }
      setSavedBanner(t("settingsSaved"));
      setTimeout(() => setSavedBanner(null), 1500);
    } finally {
      setSaving(false);
    }
  }, [editor, selected, t]);
  const formSupported = useMemo(() => {
    const sec = selected.trim();
    return (
      sec === "llm" ||
      sec === "tools" ||
      sec === "memory" ||
      sec === "skills" ||
      sec === "session" ||
      sec === "channels"
    );
  }, [selected]);

  useEffect(() => {
    if (formSupported) return;
    setMode("json");
  }, [formSupported]);

  const setEditorFromObject = useCallback((value: JsonObject) => {
    setEditor(stringifyObject(value));
  }, []);

  const updateSection = useCallback(
    (patch: (cur: JsonObject) => JsonObject) => {
      const cur = parsedEditor.ok ? parsedEditor.value : {};
      const next = patch(cur);
      setEditorFromObject(next);
    },
    [parsedEditor, setEditorFromObject]
  );

  const renderForm = useCallback(() => {
    const sec = selected.trim();
    if (!formSupported) {
      return (
        <div className="text-xs text-[var(--muted)] border border-[var(--border)] bg-[var(--panel)] rounded-lg px-3 py-2">
          {t("settingsFormNotAvailable")}
        </div>
      );
    }
    if (!parsedEditor.ok) {
      return (
        <div className="text-xs text-red-400 border border-red-500/30 bg-red-500/10 rounded-lg px-3 py-2">
          {t("settingsInvalidJson")}
        </div>
      );
    }
    const obj = parsedEditor.value;

    if (sec === "llm") {
      const provider = readString(obj.provider);
      const modelId = readString(obj.model_id);
      const baseUrl = readString(obj.base_url);
      const apiKey = readString(obj.api_key);
      const thinking = readString(obj.thinking_level);
      const contextWindow = readNumber((obj as JsonObject).contextWindow ?? (obj as JsonObject).context_window);
      const maxTokens = readNumber((obj as JsonObject).maxTokens ?? (obj as JsonObject).max_tokens);
      const providerInfo = llmProviders.find((p) => (p.id || "").toLowerCase() === provider.toLowerCase());
      const defaultBase = (providerInfo?.default_base_url || "").trim();
      return (
        <div className="space-y-3">
          {llmTestResult ? (
            <div
              className={`text-xs rounded-lg px-3 py-2 border ${
                llmTestResult.ok
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                  : "border-red-500/30 bg-red-500/10 text-red-200"
              }`}
            >
              <div className="font-medium">{llmTestResult.message}</div>
              {llmTestResult.preview ? (
                <div className="mt-1 text-[11px] font-mono opacity-90 whitespace-pre-wrap">
                  {llmTestResult.preview}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmProvider")}</div>
              <select
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={provider}
                onChange={(e) =>
                  updateSection((cur) => {
                    const nextProvider = e.target.value.trim();
                    const next: JsonObject = { ...cur, provider: nextProvider };
                    const info = llmProviders.find(
                      (p) => (p.id || "").toLowerCase() === nextProvider.toLowerCase()
                    );
                    const defBase = (info?.default_base_url || "").trim();
                    const curBase = readString((cur as JsonObject).base_url);
                    if (!curBase && defBase) {
                      next.base_url = defBase;
                    }
                    const curModel = readString((cur as JsonObject).model_id);
                    const defModel = String(info?.default_model || "").trim();
                    if (!curModel && defModel) {
                      next.model_id = defModel;
                    }
                    setLlmTestResult(null);
                    return next;
                  })
                }
              >
                {(llmProviders.length ? llmProviders : [{ id: "echo" } as LlmProviderInfo]).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmModel")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={modelId}
                onChange={(e) => updateSection((cur) => ({ ...cur, model_id: e.target.value }))}
                placeholder="gpt-4o-mini"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmBaseUrl")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={baseUrl}
                onChange={(e) => updateSection((cur) => ({ ...cur, base_url: e.target.value }))}
                placeholder={defaultBase || "https://api.openai.com"}
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmThinking")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={thinking}
                onChange={(e) =>
                  updateSection((cur) => ({ ...cur, thinking_level: e.target.value }))
                }
                placeholder="low / medium / high"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmContextWindow")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={contextWindow}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value.trim();
                    const next: JsonObject = { ...cur };
                    if (!v) {
                      delete next.contextWindow;
                      delete next.context_window;
                    } else {
                      next.contextWindow = Number(v);
                    }
                    return next;
                  })
                }
                placeholder="128000"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmMaxTokens")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={maxTokens}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value.trim();
                    const next: JsonObject = { ...cur };
                    if (!v) {
                      delete next.maxTokens;
                      delete next.max_tokens;
                    } else {
                      next.maxTokens = Number(v);
                    }
                    return next;
                  })
                }
                placeholder="4096"
              />
            </div>
          </div>
          <div className="space-y-1">
            <div className="text-[10px] text-[var(--muted)]">{t("settingsLlmApiKey")}</div>
            <input
              className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
              value={apiKey}
              onChange={(e) => updateSection((cur) => ({ ...cur, api_key: e.target.value }))}
              placeholder="(optional)"
            />
            <div className="text-[10px] text-[var(--muted)]">{t("settingsApiKeyHint")}</div>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="text-xs px-3 py-2 rounded-lg bg-[var(--accent)] text-white hover:opacity-95 disabled:opacity-60"
              disabled={llmTesting}
              onClick={async () => {
                setLlmTesting(true);
                setLlmTestResult(null);
                try {
                  const r = await llmTest({
                    provider,
                    model_id: modelId,
                    base_url: baseUrl,
                    api_key: apiKey,
                    thinking_level: thinking,
                  });
                  if (!r.ok) {
                    setLlmTestResult({ ok: false, message: r.error || t("settingsLlmTestFailed") });
                  } else {
                    setLlmTestResult({ ok: Boolean(r.success), message: r.message, preview: r.preview });
                  }
                } finally {
                  setLlmTesting(false);
                }
              }}
            >
              {llmTesting ? t("settingsLlmTesting") : t("settingsLlmTest")}
            </button>
          </div>
        </div>
      );
    }

    if (sec === "memory") {
      const enabled = readBool(obj.enabled, false);
      return (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => updateSection((cur) => ({ ...cur, enabled: e.target.checked }))}
            />
            {t("settingsMemoryEnabled")}
          </label>
        </div>
      );
    }

    if (sec === "skills") {
      const filterCsv = formatCsvList((obj as JsonObject).filter);
      const limRaw = (obj as JsonObject).limits;
      const lim =
        limRaw && typeof limRaw === "object" && !Array.isArray(limRaw) ? (limRaw as JsonObject) : {};
      const maxIn = readNumber(lim.maxSkillsInPrompt ?? lim.max_skills_in_prompt);
      const maxChars = readNumber(lim.maxSkillsPromptChars ?? lim.max_skills_prompt_chars);
      return (
        <div className="space-y-4">
          <p className="text-[10px] text-[var(--muted)] leading-relaxed">{t("settingsSkillsGlobalHint")}</p>
          <div className="space-y-1">
            <div className="text-[10px] text-[var(--muted)]">{t("settingsSkillsGlobalFilter")}</div>
            <input
              className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
              value={filterCsv}
              onChange={(e) =>
                updateSection((cur) => ({
                  ...cur,
                  filter: parseCsvList(e.target.value),
                }))
              }
              placeholder="skill-a, skill-b"
            />
            <div className="text-[10px] text-[var(--muted)]">{t("settingsSkillsGlobalFilterHint")}</div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsSkillsMaxInPrompt")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={maxIn}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value.trim();
                    const nextLim: JsonObject = { ...lim };
                    if (!v) {
                      delete nextLim.maxSkillsInPrompt;
                      delete nextLim.max_skills_in_prompt;
                    } else {
                      nextLim.maxSkillsInPrompt = Number(v);
                    }
                    return { ...cur, limits: nextLim };
                  })
                }
                placeholder="150"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsSkillsMaxPromptChars")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={maxChars}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value.trim();
                    const nextLim: JsonObject = { ...lim };
                    if (!v) {
                      delete nextLim.maxSkillsPromptChars;
                      delete nextLim.max_skills_prompt_chars;
                    } else {
                      nextLim.maxSkillsPromptChars = Number(v);
                    }
                    return { ...cur, limits: nextLim };
                  })
                }
                placeholder="30000"
              />
            </div>
          </div>

          <div className="border-t border-[var(--border)] pt-4 space-y-3">
            <div className="text-xs font-semibold">{t("settingsSkillsPerAgentTitle")}</div>
            <p className="text-[10px] text-[var(--muted)] leading-relaxed">{t("settingsSkillsPerAgentHint")}</p>
            {skillsAgentsError ? (
              <div className="text-[10px] text-red-400">{skillsAgentsError}</div>
            ) : null}
            {skillsAgentBanner ? (
              <div className="text-[10px] text-emerald-400 border border-emerald-500/30 bg-emerald-500/10 rounded-lg px-2 py-1">
                {skillsAgentBanner}
              </div>
            ) : null}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <div className="text-[10px] text-[var(--muted)]">{t("settingsSkillsAgentPick")}</div>
                <select
                  className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
                  value={skillsAgentId}
                  onChange={(e) => setSkillsAgentId(e.target.value)}
                >
                  {listedAgents.length ? (
                    listedAgents.map((a) => (
                      <option key={a.agentId} value={a.agentId}>
                        {a.agentId}
                      </option>
                    ))
                  ) : (
                    <option value="main">main</option>
                  )}
                </select>
              </div>
              <div className="space-y-1 sm:col-span-2">
                <div className="text-[10px] text-[var(--muted)]">{t("settingsSkillsAgentAllowlist")}</div>
                <input
                  className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
                  value={skillsAgentCsv}
                  onChange={(e) => setSkillsAgentCsv(e.target.value)}
                  placeholder="skill-a, skill-b"
                />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="text-xs px-3 py-2 rounded-lg bg-[var(--accent)] text-white hover:opacity-95 disabled:opacity-50"
                disabled={skillsAgentBusy || !skillsAgentId}
                onClick={() => {
                  void (async () => {
                    setSkillsAgentBusy(true);
                    setSkillsAgentBanner(null);
                    const r = await agentsUpdateSkills(skillsAgentId, parseCsvList(skillsAgentCsv));
                    setSkillsAgentBusy(false);
                    if (!r.ok) {
                      setSkillsAgentBanner(r.error || t("settingsSkillsAgentSaveFailed"));
                      return;
                    }
                    const list = await listAgents();
                    if (list.ok) setListedAgents(list.agents);
                    setSkillsAgentBanner(t("settingsSkillsAgentSaved"));
                    setTimeout(() => setSkillsAgentBanner(null), 2000);
                  })();
                }}
              >
                {skillsAgentBusy ? t("settingsSaving") : t("settingsSkillsAgentSave")}
              </button>
              <button
                type="button"
                className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90 disabled:opacity-50"
                disabled={skillsAgentBusy || !skillsAgentId}
                onClick={() => {
                  void (async () => {
                    setSkillsAgentBusy(true);
                    setSkillsAgentBanner(null);
                    const r = await agentsUpdateSkills(skillsAgentId, null);
                    setSkillsAgentBusy(false);
                    if (!r.ok) {
                      setSkillsAgentBanner(r.error || t("settingsSkillsAgentSaveFailed"));
                      return;
                    }
                    setSkillsAgentCsv("");
                    const list = await listAgents();
                    if (list.ok) setListedAgents(list.agents);
                    setSkillsAgentBanner(t("settingsSkillsAgentCleared"));
                    setTimeout(() => setSkillsAgentBanner(null), 2000);
                  })();
                }}
              >
                {t("settingsSkillsAgentClearOverride")}
              </button>
              <button
                type="button"
                className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
                onClick={() => {
                  const a = listedAgents.find((x) => x.agentId === skillsAgentId);
                  setSkillsAgentCsv(formatCsvList(a?.skills ?? []));
                  setSkillsAgentBanner(t("settingsSkillsAgentReloaded"));
                  setTimeout(() => setSkillsAgentBanner(null), 1500);
                }}
              >
                {t("settingsSkillsAgentReload")}
              </button>
            </div>
          </div>
        </div>
      );
    }

    if (sec === "session") {
      const comp = (obj.compaction && typeof obj.compaction === "object" && !Array.isArray(obj.compaction)
        ? (obj.compaction as JsonObject)
        : {}) as JsonObject;
      const enabled = readBool(comp.enabled, true);
      return (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) =>
                updateSection((cur) => ({
                  ...cur,
                  compaction: { ...(cur.compaction as JsonObject), enabled: e.target.checked },
                }))
              }
            />
            {t("settingsSessionCompactionEnabled")}
          </label>
        </div>
      );
    }

    if (sec === "channels") {
      if (!parsedEditor.ok) {
        return (
          <div className="text-xs text-red-400 border border-red-500/30 bg-red-500/10 rounded-lg px-3 py-2">
            {t("settingsInvalidJson")}
          </div>
        );
      }
      const channelsObj = obj;
      const existingTypes = Object.keys(channelsObj).filter((k) => k && k !== "__proto__");
      const knownTypes = ["console", "feishu", "telegram", "webhook"];
      const types = Array.from(new Set([...knownTypes, ...existingTypes])).sort((a, b) =>
        a.localeCompare(b)
      );

      const activeType = selectedChannelType || "";
      const activeCfgRaw = activeType && typeof (channelsObj as JsonObject)[activeType] === "object"
        ? ((channelsObj as JsonObject)[activeType] as unknown)
        : undefined;
      const activeCfg =
        activeCfgRaw && typeof activeCfgRaw === "object" && !Array.isArray(activeCfgRaw)
          ? (activeCfgRaw as JsonObject)
          : ({} as JsonObject);

      const updateChannel = (type: string, value: JsonObject) => {
        updateSection((cur) => ({
          ...cur,
          [type]: value,
        }));
      };

      const removeChannel = (type: string) => {
        updateSection((cur) => {
          const next: JsonObject = { ...cur };
          delete next[type];
          return next;
        });
      };

      const renderChannelForm = () => {
        if (!activeType) {
          return (
            <div className="text-xs text-[var(--muted)] border border-[var(--border)] bg-[var(--panel)] rounded-lg px-3 py-2">
              {t("settingsChannelsPickOne")}
            </div>
          );
        }

        if (activeType === "console") {
          return (
            <div className="space-y-3">
              <div className="text-xs text-[var(--muted)]">
                {t("settingsChannelsConsoleNoConfig")}
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
                  onClick={() => updateChannel("console", {})}
                >
                  {t("settingsChannelsEnable")}
                </button>
                <button
                  type="button"
                  className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
                  onClick={() => removeChannel("console")}
                >
                  {t("settingsChannelsRemove")}
                </button>
              </div>
            </div>
          );
        }

        if (activeType === "feishu") {
          const appId = readString(activeCfg.app_id);
          const appSecret = readString(activeCfg.app_secret);
          const mode0 = readString(activeCfg.connection_mode) || "webhook";
          const uat = readString(
            activeCfg.mcp_user_access_token || activeCfg.user_access_token || activeCfg.mcp_uat
          );
          return (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <div className="text-[10px] text-[var(--muted)]">{t("settingsChannelsFeishuAppId")}</div>
                  <input
                    className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
                    value={appId}
                    onChange={(e) =>
                      updateChannel("feishu", { ...activeCfg, app_id: e.target.value })
                    }
                    placeholder="cli_xxx"
                  />
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] text-[var(--muted)]">{t("settingsChannelsFeishuAppSecret")}</div>
                  <input
                    className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
                    value={appSecret}
                    onChange={(e) =>
                      updateChannel("feishu", { ...activeCfg, app_secret: e.target.value })
                    }
                    placeholder="••••••••"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <div className="text-[10px] text-[var(--muted)]">{t("settingsChannelsFeishuMode")}</div>
                  <select
                    className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                    value={mode0}
                    onChange={(e) =>
                      updateChannel("feishu", { ...activeCfg, connection_mode: e.target.value })
                    }
                  >
                    <option value="webhook">webhook</option>
                    <option value="websocket">websocket</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <div className="text-[10px] text-[var(--muted)]">{t("settingsChannelsFeishuUat")}</div>
                  <input
                    className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs font-mono"
                    value={uat}
                    onChange={(e) =>
                      updateChannel("feishu", { ...activeCfg, mcp_user_access_token: e.target.value })
                    }
                    placeholder="(optional)"
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
                  onClick={() => removeChannel("feishu")}
                >
                  {t("settingsChannelsRemove")}
                </button>
              </div>
            </div>
          );
        }

        // Fallback: per-channel JSON editor
        return (
          <div className="space-y-2">
            <div className="text-xs text-[var(--muted)]">{t("settingsChannelsAdvancedHint")}</div>
            <textarea
              className="min-h-[200px] w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-[11px] font-mono resize-y"
              value={channelJsonEditor}
              onChange={(e) => setChannelJsonEditor(e.target.value)}
              spellCheck={false}
            />
            <div className="flex gap-2">
              <button
                type="button"
                className="text-xs px-3 py-2 rounded-lg bg-[var(--accent)] text-white hover:opacity-95"
                onClick={() => {
                  const parsed = safeJsonParseObject(channelJsonEditor);
                  if (!parsed.ok) return;
                  updateChannel(activeType, parsed.value);
                }}
              >
                {t("settingsChannelsApply")}
              </button>
              <button
                type="button"
                className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
                onClick={() => removeChannel(activeType)}
              >
                {t("settingsChannelsRemove")}
              </button>
            </div>
          </div>
        );
      };

      return (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 items-end">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsChannelsType")}</div>
              <select
                className="w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-xs"
                value={activeType}
                onChange={(e) => setSelectedChannelType(e.target.value)}
              >
                <option value="">{t("settingsChannelsSelectType")}</option>
                {types.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsChannelsStatus")}</div>
              <div className="text-xs font-mono text-[var(--muted)]">
                {activeType ? (Object.prototype.hasOwnProperty.call(channelsObj, activeType) ? "configured" : "not set") : "—"}
              </div>
            </div>
          </div>

          {renderChannelForm()}
        </div>
      );
    }

    // tools
    const tools = obj;
    const toolsProfile = readString((tools as JsonObject).profile);
    const toolsAllow = formatCsvList((tools as JsonObject).allow);
    const toolsDeny = formatCsvList((tools as JsonObject).deny);
    const web = (tools.web && typeof tools.web === "object" && !Array.isArray(tools.web)
      ? (tools.web as JsonObject)
      : {}) as JsonObject;
    const search = (web.search && typeof web.search === "object" && !Array.isArray(web.search)
      ? (web.search as JsonObject)
      : {}) as JsonObject;
    const fetch = (web.fetch && typeof web.fetch === "object" && !Array.isArray(web.fetch)
      ? (web.fetch as JsonObject)
      : {}) as JsonObject;

    const searchEnabled = readBool(search.enabled, false);
    const searchProvider = readString(search.provider);
    const searchTimeout = readNumber(search.timeoutSeconds);
    const searchCache = readNumber(search.cacheTtlMinutes);
    const searchProxy = readString(search.proxy);
    const searchApiKey = readString(search.apiKey) || readString(search.api_key);
    const provNorm = searchProvider.trim().toLowerCase();
    const provObj =
      provNorm &&
      search[provNorm] &&
      typeof search[provNorm] === "object" &&
      !Array.isArray(search[provNorm])
        ? (search[provNorm] as JsonObject)
        : ({} as JsonObject);
    const searchProviderApiKey = readString(provObj.apiKey) || readString(provObj.api_key);

    const fetchEnabled = readBool(fetch.enabled, false);
    const fetchTimeout = readNumber(fetch.timeoutSeconds);
    const fetchCache = readNumber(fetch.cacheTtlMinutes);
    const fetchMaxRedirects = readNumber(fetch.maxRedirects);
    const fetchMaxResp = readNumber(fetch.maxResponseBytes);
    const fetchMaxCharsCap = readNumber(fetch.maxCharsCap);
    const fetchUA = readString(fetch.userAgent);

    return (
      <div className="space-y-5">
        <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3">
          <div className="text-xs font-semibold mb-2">{t("settingsToolsPolicy")}</div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsToolsProfile")}</div>
              <select
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={toolsProfile || ""}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value.trim();
                    const next: JsonObject = { ...cur };
                    if (!v) delete next.profile;
                    else next.profile = v;
                    return next;
                  })
                }
              >
                <option value="">{t("settingsToolsProfileDefault")}</option>
                <option value="standard">standard</option>
                <option value="full">full</option>
              </select>
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsToolsAllow")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs font-mono"
                value={toolsAllow}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value;
                    const list = parseCsvList(v);
                    const next: JsonObject = { ...cur };
                    if (!list.length) delete next.allow;
                    else next.allow = list;
                    return next;
                  })
                }
                placeholder="web_search, web_fetch"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsToolsDeny")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs font-mono"
                value={toolsDeny}
                onChange={(e) =>
                  updateSection((cur) => {
                    const v = e.target.value;
                    const list = parseCsvList(v);
                    const next: JsonObject = { ...cur };
                    if (!list.length) delete next.deny;
                    else next.deny = list;
                    return next;
                  })
                }
                placeholder="exec, process"
              />
            </div>
          </div>
          <div className="text-[10px] text-[var(--muted)] mt-2">
            {t("settingsToolsPolicyHint")}
          </div>
        </div>

        <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3">
          <div className="text-xs font-semibold mb-2">{t("settingsToolsWebSearch")}</div>
          <label className="flex items-center gap-2 text-xs mb-3">
            <input
              type="checkbox"
              checked={searchEnabled}
              onChange={(e) =>
                updateSection((cur) => ({
                  ...cur,
                  web: {
                    ...(cur.web as JsonObject),
                    search: { ...(((cur.web as JsonObject)?.search as JsonObject) || {}), enabled: e.target.checked },
                  },
                }))
              }
            />
            {t("settingsEnabled")}
          </label>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsProvider")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={searchProvider}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      search: {
                        ...(((cur.web as JsonObject)?.search as JsonObject) || {}),
                        provider: e.target.value.trim(),
                      },
                    },
                  }))
                }
                placeholder="perplexity / brave / serper"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsTimeoutSeconds")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={searchTimeout}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      search: {
                        ...(((cur.web as JsonObject)?.search as JsonObject) || {}),
                        timeoutSeconds: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="10"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsCacheTtlMinutes")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={searchCache}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      search: {
                        ...(((cur.web as JsonObject)?.search as JsonObject) || {}),
                        cacheTtlMinutes: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="5"
              />
            </div>
          </div>
          <div className="space-y-1 mt-3">
            <div className="text-[10px] text-[var(--muted)]">{t("settingsWebSearchProxy")}</div>
            <input
              className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs font-mono"
              value={searchProxy}
              onChange={(e) =>
                updateSection((cur) => {
                  const v = e.target.value.trim();
                  const nextSearch = {
                    ...(((cur.web as JsonObject)?.search as JsonObject) || {}),
                  } as JsonObject;
                  if (!v) delete nextSearch.proxy;
                  else nextSearch.proxy = v;
                  return {
                    ...cur,
                    web: { ...(cur.web as JsonObject), search: nextSearch },
                  };
                })
              }
              placeholder="http://127.0.0.1:7890"
            />
          </div>
          <div className="space-y-1 mt-3">
            <div className="text-[10px] text-[var(--muted)]">{t("settingsWebSearchApiKey")}</div>
            <input
              type="password"
              autoComplete="off"
              className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs font-mono"
              value={searchApiKey}
              onChange={(e) =>
                updateSection((cur) => {
                  const v = e.target.value;
                  const nextSearch = {
                    ...(((cur.web as JsonObject)?.search as JsonObject) || {}),
                  } as JsonObject;
                  if (!v.trim()) {
                    delete nextSearch.apiKey;
                    delete nextSearch.api_key;
                  } else {
                    nextSearch.apiKey = v;
                  }
                  return {
                    ...cur,
                    web: { ...(cur.web as JsonObject), search: nextSearch },
                  };
                })
              }
              placeholder="tools.web.search.apiKey"
            />
          </div>
          {provNorm ? (
            <div className="space-y-1 mt-3">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsWebSearchProviderApiKey")}</div>
              <input
                type="password"
                autoComplete="off"
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs font-mono"
                value={searchProviderApiKey}
                onChange={(e) =>
                  updateSection((cur) => {
                    const p = provNorm;
                    const v = e.target.value;
                    const nextSearch = {
                      ...(((cur.web as JsonObject)?.search as JsonObject) || {}),
                    } as JsonObject;
                    const prevSub =
                      nextSearch[p] &&
                      typeof nextSearch[p] === "object" &&
                      !Array.isArray(nextSearch[p])
                        ? ({ ...(nextSearch[p] as JsonObject) } as JsonObject)
                        : ({} as JsonObject);
                    if (!v.trim()) {
                      delete prevSub.apiKey;
                      delete prevSub.api_key;
                    } else {
                      prevSub.apiKey = v;
                    }
                    if (Object.keys(prevSub).length === 0) {
                      delete nextSearch[p];
                    } else {
                      nextSearch[p] = prevSub;
                    }
                    return {
                      ...cur,
                      web: { ...(cur.web as JsonObject), search: nextSearch },
                    };
                  })
                }
                placeholder={`tools.web.search.${provNorm}.apiKey`}
              />
              <div className="text-[10px] text-[var(--muted)]">{t("settingsWebSearchProviderApiKeyHint")}</div>
            </div>
          ) : null}
          <div className="text-[10px] text-[var(--muted)] mt-2">{t("settingsApiKeyHint")}</div>
        </div>

        <div className="rounded-lg border border-[var(--border)] bg-[var(--panel)] p-3">
          <div className="text-xs font-semibold mb-2">{t("settingsToolsWebFetch")}</div>
          <label className="flex items-center gap-2 text-xs mb-3">
            <input
              type="checkbox"
              checked={fetchEnabled}
              onChange={(e) =>
                updateSection((cur) => ({
                  ...cur,
                  web: {
                    ...(cur.web as JsonObject),
                    fetch: { ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}), enabled: e.target.checked },
                  },
                }))
              }
            />
            {t("settingsEnabled")}
          </label>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsTimeoutSeconds")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={fetchTimeout}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      fetch: {
                        ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}),
                        timeoutSeconds: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="10"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsCacheTtlMinutes")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={fetchCache}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      fetch: {
                        ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}),
                        cacheTtlMinutes: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="5"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsUserAgent")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={fetchUA}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      fetch: {
                        ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}),
                        userAgent: e.target.value,
                      },
                    },
                  }))
                }
                placeholder="mw4agent-web-fetch/0.1"
              />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3 mt-3">
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsMaxRedirects")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={fetchMaxRedirects}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      fetch: {
                        ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}),
                        maxRedirects: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="3"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsMaxResponseBytes")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={fetchMaxResp}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      fetch: {
                        ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}),
                        maxResponseBytes: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="2000000"
              />
            </div>
            <div className="space-y-1">
              <div className="text-[10px] text-[var(--muted)]">{t("settingsMaxCharsCap")}</div>
              <input
                className="w-full px-3 py-2 rounded-lg bg-[var(--bg)] border border-[var(--border)] text-xs"
                value={fetchMaxCharsCap}
                onChange={(e) =>
                  updateSection((cur) => ({
                    ...cur,
                    web: {
                      ...(cur.web as JsonObject),
                      fetch: {
                        ...(((cur.web as JsonObject)?.fetch as JsonObject) || {}),
                        maxCharsCap: e.target.value ? Number(e.target.value) : undefined,
                      },
                    },
                  }))
                }
                placeholder="50000"
              />
            </div>
          </div>
        </div>
      </div>
    );
  }, [
    channelJsonEditor,
    formSupported,
    listedAgents,
    llmProviders,
    llmTestResult,
    llmTesting,
    parsedEditor,
    selected,
    selectedChannelType,
    skillsAgentBanner,
    skillsAgentBusy,
    skillsAgentCsv,
    skillsAgentId,
    skillsAgentsError,
    t,
    updateSection,
  ]);

  return (
    <div className="flex h-full min-h-0 w-full">
      <div className="w-64 shrink-0 border-r border-[var(--border)] bg-[var(--panel)] p-3 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold">{t("settingsTitle")}</div>
          <button
            type="button"
            className="text-[10px] px-2 py-1 rounded border border-[var(--border)] bg-[var(--panel)] hover:opacity-90"
            onClick={() => void loadSections()}
          >
            {t("settingsRefresh")}
          </button>
        </div>
        <div className="text-[10px] text-[var(--muted)]">{t("settingsSections")}</div>
        <div className="min-h-0 flex-1 overflow-auto space-y-1">
          {sections.length ? (
            sections.map((s) => {
              const active = s === selected;
              return (
                <button
                  key={s}
                  type="button"
                  className={`w-full text-left rounded-lg border border-[var(--border)] px-3 py-2 text-xs font-mono truncate hover:opacity-90 ${
                    active ? "bg-[var(--accent)] text-white" : "bg-[var(--panel)] text-[var(--text)]"
                  }`}
                  onClick={() => setSelected(s)}
                  title={s}
                >
                  {s}
                </button>
              );
            })
          ) : (
            <div className="text-xs text-[var(--muted)]">{t("settingsNoSections")}</div>
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden p-4">
        {savedBanner ? (
          <div className="mb-2 text-xs text-emerald-400 border border-emerald-500/30 bg-emerald-500/10 rounded-lg px-3 py-2">
            {savedBanner}
          </div>
        ) : null}
        {error ? (
          <div className="mb-2 text-xs text-red-400 border border-red-500/30 bg-red-500/10 rounded-lg px-3 py-2">
            {error}
          </div>
        ) : null}

        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="min-w-0">
            <div className="text-sm font-semibold truncate">{selected || "—"}</div>
            <div className="text-[10px] text-[var(--muted)]">{t("settingsSectionHint")}</div>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              type="button"
              className="text-xs px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--panel)] hover:opacity-90 disabled:opacity-50"
              onClick={() => void loadSelected(selected)}
              disabled={!selected || loading || saving}
            >
              {loading ? t("settingsLoading") : t("settingsReload")}
            </button>
            <button
              type="button"
              className="text-xs px-3 py-2 rounded-lg bg-[var(--accent)] text-white hover:opacity-95 disabled:opacity-50"
              onClick={() => void doSave()}
              disabled={!canSave}
            >
              {saving ? t("settingsSaving") : t("settingsSave")}
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 mb-2">
          <button
            type="button"
            className={`text-xs px-3 py-2 rounded-lg border border-[var(--border)] ${
              mode === "form" ? "bg-[var(--accent)] text-white" : "bg-[var(--panel)]"
            } ${!formSupported ? "opacity-50 cursor-not-allowed" : "hover:opacity-90"}`}
            disabled={!formSupported}
            onClick={() => setMode("form")}
          >
            {t("settingsModeForm")}
          </button>
          <button
            type="button"
            className={`text-xs px-3 py-2 rounded-lg border border-[var(--border)] ${
              mode === "json" ? "bg-[var(--accent)] text-white" : "bg-[var(--panel)]"
            } hover:opacity-90`}
            onClick={() => setMode("json")}
          >
            {t("settingsModeJson")}
          </button>
          {!parsedEditor.ok ? (
            <div className="text-[10px] text-red-400">{t("settingsInvalidJson")}</div>
          ) : null}
        </div>

        {mode === "form" ? (
          <div className="min-h-0 flex-1 overflow-auto">{renderForm()}</div>
        ) : (
          <textarea
            className="min-h-0 flex-1 w-full px-3 py-2 rounded-lg bg-[var(--panel)] border border-[var(--border)] text-[var(--text)] text-[11px] font-mono resize-none"
            value={editor}
            onChange={(e) => setEditor(e.target.value)}
            spellCheck={false}
          />
        )}
      </div>
    </div>
  );
}

