import { t, tFormat, getLocale, setLocale, applyToPage } from "./i18n.js";
import { getTheme, setTheme, applyTheme, getThemes } from "./theme.js";

const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusLabel = document.getElementById("status-label");
const statusUrl = document.getElementById("status-url");
const metaWs = document.getElementById("meta-ws");
const metaRpc = document.getElementById("meta-rpc");
const metaRun = document.getElementById("meta-run");
const metaEventsTotal = document.getElementById("meta-events-total");
const logListEl = document.getElementById("log-list");
const logEmptyEl = document.getElementById("log-empty");

const MAX_LOG_ENTRIES = 500;
const logEntries = [];

let eventsTotal = 0;
let ws = null;
/** Auto-reconnect state (OpenClaw-style): backoff delay and timer. */
let reconnectDelayMs = 1000;
const RECONNECT_INITIAL_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const RECONNECT_BACKOFF_FACTOR = 1.7;
let reconnectTimer = null;
let wsUrl = "";

/** Current run's assistant bubble (one per run); updated in place for steps + streaming reply. */
let currentRunId = null;
let currentStepEl = null;
let currentReasoningEl = null;
let currentBodyEl = null;

/** When true, send reasoningLevel: "on" so backend emits reasoning blocks for <think>...</think>. */
let showReasoning = false;

function formatLogTime(ts) {
  const d = ts && typeof ts === "number" ? new Date(ts) : new Date();
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${h}:${m}:${s}.${ms}`;
}

function appendLogEntry(payload) {
  if (!logListEl) return;
  const stream = payload.stream || "?";
  const data = payload.data || {};
  const runId = payload.run_id || data.run_id || "";
  const type = data.type || payload.type || "";
  const ts = payload.ts || payload.timestamp || Date.now();
  let summary = "";
  if (stream === "lifecycle" && data.phase) summary = `phase=${data.phase}`;
  else if (stream === "tool") summary = (data.tool_name || "") + " " + (data.type || "");
  else if (stream === "assistant") {
    if (data.reasoning !== undefined && data.reasoning !== null) summary = "reasoning";
    else if (data.text !== undefined && data.text !== null) summary = String(data.text).slice(0, 60) + (String(data.text).length > 60 ? "…" : "");
    else if (data.delta) summary = String(data.delta).slice(0, 40) + "…";
    else summary = type || "delta";
  } else summary = type || JSON.stringify(data).slice(0, 80);
  const entry = document.createElement("div");
  entry.className = "log-entry";
  entry.setAttribute("data-stream", stream);
  entry.innerHTML = `<span class="log-entry-time">${formatLogTime(ts)}</span><span class="log-entry-stream">${escapeHtml(stream)}</span>${runId ? ` <span class="log-entry-run">${escapeHtml(runId.slice(0, 8))}</span>` : ""} ${escapeHtml(summary)}`;
  logListEl.appendChild(entry);
  logEntries.push(entry);
  if (logEntries.length > MAX_LOG_ENTRIES) {
    const old = logEntries.shift();
    if (old && old.parentNode) old.remove();
  }
  logListEl.scrollTop = logListEl.scrollHeight;
  if (logEmptyEl) logEmptyEl.classList.add("hidden");
}

function escapeHtml(s) {
  if (s == null) return "";
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function appendMessage(kind, text, meta) {
  if (!text && kind !== "assistant") return;
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${kind === "user" ? "msg-user" : "msg-assistant"}`;

  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "msg-meta";
    metaEl.textContent = meta;
    wrapper.appendChild(metaEl);
  }

  const body = document.createElement("div");
  body.className = "msg-body";
  if (text) body.textContent = text;
  wrapper.appendChild(body);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

/** Ensure there is an assistant placeholder for this run; return { stepEl, reasoningEl, bodyEl }. */
function ensureAssistantPlaceholder(runId, meta) {
  if (!messagesEl) return { stepEl: null, reasoningEl: null, bodyEl: null };
  if (currentRunId === runId && currentStepEl && currentBodyEl) {
    return { stepEl: currentStepEl, reasoningEl: currentReasoningEl, bodyEl: currentBodyEl };
  }
  currentRunId = runId;
  const wrapper = document.createElement("div");
  wrapper.className = "msg msg-assistant";
  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "msg-meta";
    metaEl.textContent = meta;
    wrapper.appendChild(metaEl);
  }
  const stepEl = document.createElement("div");
  stepEl.className = "msg-step";
  stepEl.setAttribute("aria-live", "polite");
  wrapper.appendChild(stepEl);
  const reasoningEl = document.createElement("div");
  reasoningEl.className = "msg-reasoning";
  reasoningEl.setAttribute("aria-label", t("reasoningLabel") || "Reasoning");
  wrapper.appendChild(reasoningEl);
  const bodyEl = document.createElement("div");
  bodyEl.className = "msg-body";
  wrapper.appendChild(bodyEl);
  messagesEl.appendChild(wrapper);
  currentStepEl = stepEl;
  currentReasoningEl = reasoningEl;
  currentBodyEl = bodyEl;
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return { stepEl, reasoningEl, bodyEl };
}

function setAssistantStep(runId, stepText) {
  const { stepEl } = ensureAssistantPlaceholder(
    runId,
    runId ? `${t("metaAssistant")} · run ${runId}` : t("metaAssistant")
  );
  if (stepEl) {
    stepEl.textContent = stepText || "";
    stepEl.style.display = stepText ? "" : "none";
  }
}

function appendAssistantBody(runId, text) {
  const { bodyEl } = ensureAssistantPlaceholder(
    runId,
    runId ? `${t("metaAssistant")} · run ${runId}` : t("metaAssistant")
  );
  if (bodyEl) {
    bodyEl.textContent = (bodyEl.textContent || "") + (text || "");
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function setAssistantBody(runId, text) {
  const { bodyEl } = ensureAssistantPlaceholder(
    runId,
    runId ? `${t("metaAssistant")} · run ${runId}` : t("metaAssistant")
  );
  if (bodyEl) {
    bodyEl.textContent = text || "";
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function setAssistantReasoning(runId, text) {
  const { reasoningEl } = ensureAssistantPlaceholder(
    runId,
    runId ? `${t("metaAssistant")} · run ${runId}` : t("metaAssistant")
  );
  if (reasoningEl) {
    reasoningEl.textContent = text || "";
    reasoningEl.style.display = text ? "" : "none";
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

function finalizeAssistantRun(runId) {
  if (currentRunId === runId) {
    currentRunId = null;
    currentStepEl = null;
    currentReasoningEl = null;
    currentBodyEl = null;
  }
}

function setWsStatus(connected, reconnecting = false) {
  if (connected) {
    statusDot.classList.remove("err");
    statusDot.classList.add("ok");
    statusLabel.textContent = t("statusConnected");
    metaWs.textContent = t("connected");
  } else {
    statusDot.classList.remove("ok");
    statusDot.classList.add("err");
    statusLabel.textContent = reconnecting ? (t("statusReconnecting") || "Reconnecting…") : t("statusDisconnected");
    metaWs.textContent = reconnecting ? (t("reconnecting") || "Reconnecting…") : t("notConnected");
  }
}

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect() {
  clearReconnectTimer();
  setWsStatus(false, true);
  const delay = reconnectDelayMs;
  reconnectDelayMs = Math.min(reconnectDelayMs * RECONNECT_BACKOFF_FACTOR, RECONNECT_MAX_MS);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (wsUrl) connectWs(wsUrl);
  }, delay);
}

function connectWs(url) {
  clearReconnectTimer();
  if (ws && ws.readyState !== WebSocket.CLOSED) {
    try { ws.close(); } catch (_) {}
  }
  wsUrl = url;
  ws = new WebSocket(url);
  ws.addEventListener("open", () => {
    reconnectDelayMs = RECONNECT_INITIAL_MS;
    setWsStatus(true);
  });
  ws.addEventListener("close", () => {
    setWsStatus(false);
    scheduleReconnect();
  });
  ws.addEventListener("error", () => {
    setWsStatus(false);
  });
  ws.addEventListener("message", (event) => {
    eventsTotal += 1;
    metaEventsTotal.textContent = String(eventsTotal);
    let payload;
    try {
      payload = JSON.parse(event.data);
      appendLogEntry(payload);
    } catch {
      return;
    }
    if (!messagesEl) return;
    try {
      const stream = payload.stream;
      const data = payload.data || {};
      const runId = payload.run_id || data.run_id || "";
      if (runId) metaRun.textContent = String(runId);
      const run = runId || currentRunId;
      if (stream === "lifecycle" && data) {
        const phase = data.phase;
        if (phase === "start" && run) {
          setAssistantStep(run, t("stepThinking"));
          setAssistantBody(run, "");
        } else if ((phase === "end" || phase === "error") && run) {
          finalizeAssistantRun(run);
        }
        return;
      }
      if (stream === "tool" && data && run) {
        const type = data.type;
        const name = data.tool_name || "?";
        if (type === "start") {
          setAssistantStep(run, tFormat("stepCallingTool", { name }));
        } else if (type === "end" || type === "error") {
          setAssistantStep(run, tFormat("stepToolDone", { name }));
        }
        return;
      }
      if (stream === "assistant" && data) {
        const runForAssistant = run || runId;
        if (!runForAssistant) return;
        if (data.reasoning !== undefined && data.reasoning !== null) {
          setAssistantReasoning(runForAssistant, String(data.reasoning).trim());
        }
        const text = (data.text !== undefined && data.text !== null) ? String(data.text) : (data.delta || "");
        const textTrimmed = text.trim();
        if (textTrimmed) {
          if (textTrimmed === "Processing..." || textTrimmed === "思考中…") {
            setAssistantStep(runForAssistant, t("stepThinking"));
            return;
          }
          setAssistantStep(runForAssistant, "");
          if (data.final === true) {
            setAssistantBody(runForAssistant, textTrimmed);
          } else {
            appendAssistantBody(runForAssistant, textTrimmed);
          }
        }
      }
    } catch {
      // ignore malformed events
    }
  });
}

function init() {
  applyTheme();
  applyToPage();

  const themeSwitcher = document.getElementById("theme-switcher");
  if (themeSwitcher) {
    themeSwitcher.querySelectorAll(".theme-btn").forEach((btn) => {
      const theme = btn.getAttribute("data-theme");
      if (getTheme() === theme) btn.classList.add("active");
      btn.addEventListener("click", () => {
        setTheme(theme);
        applyTheme();
        themeSwitcher.querySelectorAll(".theme-btn").forEach((b) => b.classList.toggle("active", b.getAttribute("data-theme") === theme));
      });
    });
  }

  const langSwitcher = document.getElementById("lang-switcher");
  if (langSwitcher) {
    langSwitcher.querySelectorAll(".lang-btn").forEach((btn) => {
      const lang = btn.getAttribute("data-lang");
      if (getLocale() === lang) btn.classList.add("active");
      btn.addEventListener("click", () => {
        setLocale(lang);
        applyToPage();
        setWsStatus(ws && ws.readyState === WebSocket.OPEN);
        langSwitcher.querySelectorAll(".lang-btn").forEach((b) => b.classList.toggle("active", b.getAttribute("data-lang") === lang));
      });
    });
  }

  const loc = window.location;
  const baseHttp = `${loc.protocol}//${loc.host}`;
  const url = `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/ws`;
  const rpcUrl = `${baseHttp}/rpc`;

  statusUrl.textContent = baseHttp;
  metaRpc.textContent = "/rpc";

  setWsStatus(false);
  connectWs(url);

  const reasoningCheckbox = document.getElementById("reasoning-checkbox");
  if (reasoningCheckbox) {
    showReasoning = reasoningCheckbox.checked;
    reasoningCheckbox.addEventListener("change", () => {
      showReasoning = reasoningCheckbox.checked;
    });
  }

  const rightPanelGateway = document.getElementById("right-panel-gateway");
  const rightPanelAgents = document.getElementById("right-panel-agents");
  const rightPanelLogs = document.getElementById("right-panel-logs");
  const rightPanelConfig = document.getElementById("right-panel-config");
  const agentsTbody = document.getElementById("agents-tbody");
  const agentsEmptyEl = document.getElementById("agents-empty");
  const agentsRefreshBtn = document.getElementById("agents-refresh");
  const configEmpty = document.getElementById("config-empty");
  const configSections = document.getElementById("config-sections");
  const configRefreshBtn = document.getElementById("config-refresh");
  const configSaveBtn = document.getElementById("config-save");
  const configEditor = document.getElementById("config-editor");
  const configCurrent = document.getElementById("config-current");
  const configSaveStatus = document.getElementById("config-save-status");

  let configLoaded = false;
  let currentSection = "__all__";

  async function rpcCall(method, params) {
    const idem = `dashboard-${method}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const body = { id: idem, method, params: params || {} };
    const res = await fetch(rpcUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json().catch(() => ({}));
  }

  function setConfigStatus(text, isError) {
    if (!configSaveStatus) return;
    configSaveStatus.textContent = text || "";
    configSaveStatus.style.color = isError ? "var(--danger)" : "var(--text-muted)";
  }

  function renderSectionTabs(sections) {
    if (!configSections) return;
    configSections.innerHTML = "";
    const allBtn = document.createElement("button");
    allBtn.type = "button";
    allBtn.className = "lang-btn";
    allBtn.textContent = t("configAll");
    allBtn.addEventListener("click", () => loadConfigSection("__all__"));
    configSections.appendChild(allBtn);
    (sections || []).forEach((sec) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "lang-btn";
      btn.textContent = String(sec);
      btn.addEventListener("click", () => loadConfigSection(String(sec)));
      configSections.appendChild(btn);
    });
  }

  async function loadConfigSection(section) {
    currentSection = section || "__all__";
    if (configCurrent) configCurrent.textContent = currentSection;
    setConfigStatus("");
    try {
      if (currentSection === "__all__") {
        const jsonCfg = await rpcCall("config.get", {});
        if (jsonCfg && jsonCfg.ok && jsonCfg.payload) {
          if (configEmpty) configEmpty.classList.add("hidden");
          if (configEditor) configEditor.value = JSON.stringify(jsonCfg.payload.config || {}, null, 2);
        }
        return;
      }
      const jsonSec = await rpcCall("config.section.get", { section: currentSection });
      if (jsonSec && jsonSec.ok && jsonSec.payload) {
        if (configEmpty) configEmpty.classList.add("hidden");
        if (configEditor) configEditor.value = JSON.stringify(jsonSec.payload.value || {}, null, 2);
      }
    } catch (err) {
      if (configEmpty) {
        configEmpty.textContent = `Failed to load config: ${String(err)}`;
        configEmpty.classList.remove("hidden");
      }
    }
  }

  function formatTsMs(ms) {
    if (ms == null || ms === "") return "–";
    try {
      const d = new Date(Number(ms));
      return d.toLocaleString();
    } catch {
      return String(ms);
    }
  }

  function renderAgentsTable(agents) {
    if (!agentsTbody) return;
    agentsTbody.innerHTML = "";
    if (!agents || !agents.length) {
      if (agentsEmptyEl) {
        agentsEmptyEl.classList.remove("hidden");
        agentsEmptyEl.textContent = t("agentsEmpty");
      }
      return;
    }
    if (agentsEmptyEl) agentsEmptyEl.classList.add("hidden");
    for (const a of agents) {
      const tr = document.createElement("tr");
      const rs = a.runStatus || {};
      const st = rs.state === "running" ? "running" : "idle";
      const badgeClass = st === "running" ? "agents-badge running" : "agents-badge idle";
      const badgeText = st === "running" ? t("runStateRunning") : t("runStateIdle");
      const activeN = rs.activeRuns != null ? rs.activeRuns : 0;
      const lr = rs.lastRun;
      let lastLine = "–";
      if (lr) {
        lastLine = `${t("lastRunLabel")}: ${lr.status || "?"} · ${formatTsMs(lr.endedAt)}`;
        if (lr.runId) lastLine += ` · ${String(lr.runId).slice(0, 8)}…`;
      }
      const cfgBadge = a.configured ? t("configuredBadge") : t("defaultAgentBadge");
      const activeLine =
        activeN > 0 ? `<div class="agents-meta-line">${escapeHtml(tFormat("activeRuns", { n: activeN }))}</div>` : "";
      tr.innerHTML = `
        <td><strong>${escapeHtml(a.agentId || "")}</strong>
          <div class="agents-meta-line">${escapeHtml(cfgBadge)}</div></td>
        <td><span class="${badgeClass}">${escapeHtml(badgeText)}</span>
          ${activeLine}
          <div class="agents-meta-line">${escapeHtml(lastLine)}</div></td>
        <td class="path-cell">
          <div><b>agent_dir</b> ${escapeHtml(a.agentDir || "")}</div>
          <div style="margin-top:6px"><b>workspace</b> ${escapeHtml(a.workspaceDir || "")}</div>
          <div style="margin-top:6px"><b>sessions</b> ${escapeHtml(a.sessionsFile || "")}</div>
        </td>`;
      agentsTbody.appendChild(tr);
    }
  }

  async function loadAgentsUI() {
    if (!agentsTbody) return;
    try {
      const res = await rpcCall("agents.list", {});
      if (res && res.ok && res.payload && Array.isArray(res.payload.agents)) {
        renderAgentsTable(res.payload.agents);
      } else {
        if (agentsEmptyEl) {
          agentsEmptyEl.classList.remove("hidden");
          agentsEmptyEl.textContent = (res && res.error && res.error.message) || t("agentsLoadError");
        }
      }
    } catch (err) {
      if (agentsEmptyEl) {
        agentsEmptyEl.classList.remove("hidden");
        agentsEmptyEl.textContent = `${t("agentsLoadError")} ${String(err)}`;
      }
    }
  }

  async function loadConfigUI() {
    if (configLoaded) return;
    configLoaded = true;
    try {
      const jsonList = await rpcCall("config.sections.list", {});
      const sections = (jsonList && jsonList.ok && jsonList.payload && jsonList.payload.sections) || [];
      renderSectionTabs(sections);
      await loadConfigSection("__all__");
    } catch (err) {
      if (configEmpty) {
        configEmpty.textContent = `Failed to load config: ${String(err)}`;
        configEmpty.classList.remove("hidden");
      }
    }
  }
  document.querySelectorAll(".right-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabName = tab.getAttribute("data-tab");
      document.querySelectorAll(".right-tab").forEach((t) => t.classList.toggle("active", t.getAttribute("data-tab") === tabName));
      if (tabName === "gateway") {
        if (rightPanelGateway) rightPanelGateway.classList.remove("hidden");
        if (rightPanelAgents) rightPanelAgents.classList.add("hidden");
        if (rightPanelLogs) rightPanelLogs.classList.add("hidden");
        if (rightPanelConfig) rightPanelConfig.classList.add("hidden");
      } else if (tabName === "agents") {
        if (rightPanelGateway) rightPanelGateway.classList.add("hidden");
        if (rightPanelAgents) rightPanelAgents.classList.remove("hidden");
        if (rightPanelLogs) rightPanelLogs.classList.add("hidden");
        if (rightPanelConfig) rightPanelConfig.classList.add("hidden");
        loadAgentsUI();
      } else if (tabName === "logs") {
        if (rightPanelGateway) rightPanelGateway.classList.add("hidden");
        if (rightPanelAgents) rightPanelAgents.classList.add("hidden");
        if (rightPanelLogs) rightPanelLogs.classList.remove("hidden");
        if (rightPanelConfig) rightPanelConfig.classList.add("hidden");
      } else if (tabName === "config") {
        if (rightPanelGateway) rightPanelGateway.classList.add("hidden");
        if (rightPanelAgents) rightPanelAgents.classList.add("hidden");
        if (rightPanelLogs) rightPanelLogs.classList.add("hidden");
        if (rightPanelConfig) rightPanelConfig.classList.remove("hidden");
        loadConfigUI();
      }
    });
  });

  if (configRefreshBtn) {
    configRefreshBtn.addEventListener("click", async () => {
      configLoaded = false;
      setConfigStatus("");
      await loadConfigUI();
    });
  }

  if (agentsRefreshBtn) {
    agentsRefreshBtn.addEventListener("click", async () => {
      await loadAgentsUI();
    });
  }

  if (configSaveBtn) {
    configSaveBtn.addEventListener("click", async () => {
      if (!configEditor) return;
      if (!currentSection || currentSection === "__all__") {
        setConfigStatus("Only section editing is supported.", true);
        return;
      }
      let obj;
      try {
        obj = JSON.parse(configEditor.value || "{}");
      } catch (e) {
        setConfigStatus("Invalid JSON.", true);
        return;
      }
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
        setConfigStatus("Section value must be a JSON object.", true);
        return;
      }
      setConfigStatus("Saving…", false);
      try {
        const res = await rpcCall("config.section.set", { section: currentSection, value: obj });
        if (res && res.ok) {
          setConfigStatus("Saved.", false);
          await loadConfigSection(currentSection);
        } else {
          setConfigStatus((res && res.error && res.error.message) || "Save failed.", true);
        }
      } catch (err) {
        setConfigStatus(`Save failed: ${String(err)}`, true);
      }
    });
  }

  sendBtn.addEventListener("click", async () => {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = "";
    appendMessage("user", text, t("metaYou"));

    const idem = `dashboard-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const body = {
      id: idem,
      method: "agent",
      params: {
        message: text,
        sessionKey: "dashboard",
        sessionId: "dashboard",
        agentId: "dashboard",
        idempotencyKey: idem,
        channel: "dashboard",
        reasoningLevel: showReasoning ? "on" : "off",
      },
    };

    try {
      const res = await fetch(rpcUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      const runId = json.runId || (json.payload && json.payload.runId) || "";
      if (runId) {
        setAssistantStep(runId, t("stepThinking"));
        setAssistantBody(runId, "");
        if (metaRun) metaRun.textContent = String(runId);
      }
    } catch (err) {
      appendMessage("assistant", `RPC error: ${err}`, t("metaError"));
    }
  });

  inputEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      sendBtn.click();
    }
  });
}

init();

