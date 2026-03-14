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
  const rightPanelLogs = document.getElementById("right-panel-logs");
  document.querySelectorAll(".right-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabName = tab.getAttribute("data-tab");
      document.querySelectorAll(".right-tab").forEach((t) => t.classList.toggle("active", t.getAttribute("data-tab") === tabName));
      if (tabName === "gateway") {
        if (rightPanelGateway) rightPanelGateway.classList.remove("hidden");
        if (rightPanelLogs) rightPanelLogs.classList.add("hidden");
      } else {
        if (rightPanelGateway) rightPanelGateway.classList.add("hidden");
        if (rightPanelLogs) rightPanelLogs.classList.remove("hidden");
      }
    });
  });

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

