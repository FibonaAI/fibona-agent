// A Fibona view (index.html in a tab, popup or side panel): the packaged Console and the agent's App, which start
// independently of the session. The Console attaches to the host in the offscreen document for the shared log and
// status, and sends the user's messages, Stop and Troubleshooting actions. The App runs in a sandboxed iframe
// (app/frame.js) with chrome.* and this page's objects reachable through the bridges below.
import { encodeBrowserValue, decodeBrowserValue } from "../runtime/browser-wire.js";
import "../runtime/channel.js";
import "../runtime/native-bridge.js";

const elements = Object.fromEntries(["status", "settings", "settings-form", "base-url", "api-key", "model", "save-settings", "settings-status", "troubleshooting-status", "log", "input-form", "message", "send", "stop", "app", "console-panel", "safe-notice", "safe-reason", "exit-safe", "enter-safe", "clear-all"].map(id => [id, document.getElementById(id)]));
const params = new URL(location.href).searchParams;
// Safe mode: chrome.storage.local fibona_safe_mode, set by the user or by the session after a failed saved startup.
// index.html?safe=1 is the link that always reaches it, even if the agent changed the toolbar and menus.
const safeLink = params.get("safe") === "1";
const safeState = (await chrome.storage.local.get("fibona_safe_mode")).fibona_safe_mode;
const safeMode = safeLink || Boolean(safeState);
const surface = params.get("surface") ?? "tab";
const context = { id: crypto.randomUUID(), surface, url: location.href };  // fibona_ui_context in the App.
const activities = new Map();
const pendingAppLogs = [];
const pendingAppData = [];
let host, hostPort, connecting, reconnectTimer, connectionRevision = 0, restarting = false;
let appChannel, appPort, appBridge, appRevision = 0, appCode = "", appReady = false;
let savedSettings, submitting = false;
// The Console/App switch: an ordinary page element the App may restyle or remove through fibona_view.
const viewSwitch = document.getElementById("view-switch");
let switchDrag, switchDragged = false;

async function backgroundRequest(type, params = {}) {
  const reply = await chrome.runtime.sendMessage({ to: "background", type, params: await encodeBrowserValue(params) });
  if (!reply) throw new Error("Fibona: background did not reply");
  if (reply.error !== undefined) throw Object.assign(new Error(reply.error), { name: reply.errorName ?? "Error" });
  return decodeBrowserValue(reply.value);
}

function showConsole(visible = true) {
  visible = safeMode || Boolean(visible);
  document.documentElement.dataset.view = visible ? "console" : "app";
  elements["console-panel"].hidden = !visible;
  elements.app.hidden = visible;
  if (viewSwitch?.isConnected) {
    const label = viewSwitch.querySelector("#view-label");
    if (label) label.textContent = visible ? "Console" : "App";
    viewSwitch.setAttribute("aria-expanded", String(visible));
    viewSwitch.title = `Viewing ${visible ? "Console" : "App"}. Open ${visible ? "App" : "Console"}. Drag to move.`;
    viewSwitch.setAttribute("aria-label", visible ? "Open App" : "Open Console");
    if (viewSwitch.style.left) positionViewSwitch(parseFloat(viewSwitch.style.left), parseFloat(viewSwitch.style.top));
  }
  if (visible && document.activeElement !== viewSwitch) elements.message.focus({ preventScroll: true });
}

function positionViewSwitch(left, top) {
  if (!viewSwitch?.isConnected) return;
  const bounds = viewSwitch.getBoundingClientRect();
  Object.assign(viewSwitch.style, {
    left: Math.max(8, Math.min(left, innerWidth - bounds.width - 8)) + "px",
    top: Math.max(8, Math.min(top, innerHeight - bounds.height - 8)) + "px",
    right: "auto", bottom: "auto",
  });
}

function finishViewSwitchDrag(event) {
  if (event.pointerId !== switchDrag?.id) return;
  switchDrag = undefined;
  document.body.classList.remove("dragging-view-switch");
}

if (safeMode) viewSwitch?.remove();
else if (viewSwitch) {
  viewSwitch.hidden = false;
  viewSwitch.addEventListener("pointerdown", event => {
    if (!event.isPrimary || event.button !== 0) return;
    const bounds = viewSwitch.getBoundingClientRect();
    switchDrag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: bounds.left, top: bounds.top };
    switchDragged = false;
    document.body.classList.add("dragging-view-switch");
    viewSwitch.setPointerCapture(event.pointerId);
  });
  viewSwitch.addEventListener("pointermove", event => {
    if (event.pointerId !== switchDrag?.id) return;
    const dx = event.clientX - switchDrag.x, dy = event.clientY - switchDrag.y;
    if (!switchDragged && Math.hypot(dx, dy) < 5) return;
    switchDragged = true;
    positionViewSwitch(switchDrag.left + dx, switchDrag.top + dy);
  });
  viewSwitch.addEventListener("lostpointercapture", finishViewSwitchDrag);
  addEventListener("pointerup", finishViewSwitchDrag);
  addEventListener("pointercancel", finishViewSwitchDrag);
  viewSwitch.addEventListener("click", event => {
    if (event.detail && switchDragged) { switchDragged = false; return; }
    showConsole(document.documentElement.dataset.view !== "console");
  });
  addEventListener("resize", () => {
    if (viewSwitch.isConnected && viewSwitch.style.left) {
      positionViewSwitch(parseFloat(viewSwitch.style.left), parseFloat(viewSwitch.style.top));
    }
  });
}

// Stop ends the current task and keeps the session. A cell that never waits cannot be stopped, and one stuck in a
// synchronous loop cannot even answer; if the run has not ended FORCE_AFTER after Stop, the button becomes Force
// restart, which discards the session.
const FORCE_AFTER = 5000;
let lastStatus = { state: "Connecting…", running: false }, forceTimer, forceOffered = false;
const runEnded = () => !lastStatus.running && lastStatus.state !== "Stopping";

function updateControls() {
  elements.send.disabled = !host || restarting || submitting;
  elements.stop.disabled = restarting || !(forceOffered || lastStatus.running);
  elements.stop.textContent = forceOffered ? "Force restart" : "Stop";
  elements.stop.classList.toggle("force", forceOffered);
  elements.stop.title = forceOffered ? "Fibona is not responding. Restart it; unsaved memory is lost."
    : "Stop the current task (Esc). Fibona keeps its memory.";
  for (const id of ["enter-safe", "exit-safe", "clear-all"]) elements[id].disabled = restarting;
}

function showStatus(status) {
  lastStatus = status;
  if (runEnded()) {
    clearTimeout(forceTimer);
    forceTimer = undefined;
    forceOffered = false;
  }
  elements.status.textContent = restarting ? "Restarting…" : status.state === "Stopping" ? "Stopping…"
    : status.thinking ? "Thinking…" : status.state;
  updateControls();
}

function appendActivity(item) {
  // Model calls may return no prose after scheduling a cell. Keep whitespace in the execution record,
  // but do not render an empty assistant message (including when replaying an older host snapshot).
  if (item.type === "text" && !item.text.trim()) return;
  const follow = elements.log.scrollHeight - elements.log.scrollTop - elements.log.clientHeight < 48;
  let activity = activities.get(item.id);
  if (!activity) {
    const node = document.createElement("article");
    node.className = "activity " + item.type;
    if (item.type === "cell") {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const title = document.createElement("span");
      const status = document.createElement("span");
      status.className = "cell-status";
      const code = document.createElement("pre");
      const error = document.createElement("pre");
      error.className = "cell-error";
      summary.append(title, status);
      details.append(summary, code, error);
      node.append(details);
      activity = { node, title, status, code, error };
    } else {
      const label = document.createElement("div");
      label.className = "message-label";
      label.textContent = item.type === "user" ? "You" : item.source === "app" ? "App" : "Fibona";
      const text = document.createElement("pre");
      node.append(label, text);
      activity = { node, text };
    }
    activities.set(item.id, activity);
    elements.log.append(node);
  }
  if (item.type === "cell") {
    activity.title.textContent = `Cell ${item.number} · ${item.summary || "JavaScript"}`;
    activity.status.textContent = item.status === "waiting" ? "waiting for your message" : item.status;
    activity.status.dataset.status = item.status;
    activity.code.textContent = item.code;
    activity.error.textContent = item.error || "";
  } else {
    activity.text.textContent = item.text.replace(/\n+$/, "");
    activity.node.classList.toggle("error", item.level === "error");
    activity.node.classList.toggle("warning", item.level === "warn");
  }
  // Match the host's bounded conversation, including local connection errors.
  while (activities.size > 2000) {
    const [id, first] = activities.entries().next().value;
    first.node.remove();
    activities.delete(id);
  }
  if (follow) elements.log.scrollTop = elements.log.scrollHeight;
}

function showError(error) {
  appendActivity({ id: crypto.randomUUID(), type: "text", source: "app", level: "error", text: error?.message ?? String(error) });
}

function reportAppLog(item) {
  if (host) host.request("app-log", item).catch(showError);
  else {
    pendingAppLogs.push(item);
    if (pendingAppLogs.length > 100) pendingAppLogs.shift();
    showError(new Error(item.text));
  }
}

function receiveHostMessage(message) {
  if (message.type === "log") appendActivity(message.item);
  else if (message.type === "status") showStatus(message.status);
  else if (message.type === "ui-data" && !safeMode) {
    if (appReady) appChannel.send({ type: "data", data: message.data });
    else pendingAppData.push(message.data);
  }
}

function scheduleReconnect() {
  if (restarting || reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = undefined;
    connectHost();
  }, 500);
}

async function connectHost() {
  if (connecting || restarting || host) return;
  const revision = ++connectionRevision;
  connecting = true;
  let port, channel, attachTimeout;
  try {
    elements.status.textContent = "Connecting…";
    await backgroundRequest("ensure");
    if (revision !== connectionRevision || restarting) return;
    port = chrome.runtime.connect({ name: "fibona-view" });
    hostPort = port;
    let sending = Promise.resolve();
    const send = message => {
      const next = sending.then(async () => port.postMessage(await encodeBrowserValue(message)));
      sending = next.catch(() => {});
      return next;
    };
    channel = new FibonaChannel(send, { message: receiveHostMessage });
    port.onMessage.addListener(message => channel.receive(decodeBrowserValue(message)).catch(showError));
    port.onDisconnect.addListener(() => {
      channel.close("Fibona: session connection interrupted");
      if (hostPort !== port) return;
      host = undefined;
      hostPort = undefined;
      updateControls();
      elements.status.textContent = restarting ? "Restarting…" : "Reconnecting…";
      scheduleReconnect();
    });
    // A host can disappear between ensure and attach, leaving a port with no responding receiver.
    attachTimeout = setTimeout(() => channel.close("Fibona: session connection timed out"), 10000);
    const snapshot = await channel.request("attach", context);
    if (revision !== connectionRevision || restarting) return;
    context.id = snapshot.id;
    host = channel;
    activities.clear();
    elements.log.replaceChildren();
    snapshot.log.forEach(appendActivity);
    showStatus(snapshot.status);
    if (appReady) appChannel.send({ type: "context", context });
    for (const item of pendingAppLogs.splice(0)) reportAppLog(item);
    if (savedSettings?.apiKey) host.request("configured").catch(showError);
  } catch (error) {
    if (revision === connectionRevision && !restarting) {
      elements.status.textContent = "Connection unavailable. Retrying…";
      scheduleReconnect();
    }
    if (hostPort === port) hostPort = undefined;
    port?.disconnect();
    channel?.close(error.message);
  } finally {
    clearTimeout(attachTimeout);
    if (revision === connectionRevision) connecting = false;
    updateControls();
  }
}

async function reloadApp() {
  if (safeMode) return;
  const revision = ++appRevision;
  appReady = false;
  appChannel?.close("Fibona: App reloaded");
  appPort?.close();
  appBridge?.close();
  appChannel = appPort = appBridge = undefined;
  pendingAppData.length = 0;
  try {
    const saved = await chrome.storage.local.get("fibona_app");
    const code = typeof saved.fibona_app === "string" ? saved.fibona_app : await (await fetch(chrome.runtime.getURL("app/bootstrap.js"))).text();
    if (revision !== appRevision) return;
    appCode = code;
    // New Window identity rejects stale ready messages from the previous App.
    const replacement = elements.app.cloneNode(false);
    replacement.removeAttribute("src");
    elements.app.replaceWith(replacement);
    elements.app = replacement;
    replacement.src = "app/frame.html";
  } catch (error) {
    reportAppLog({ text: "App could not load: " + error.message, level: "error" });
    showConsole(true);
  }
}

addEventListener("message", event => {
  if (safeMode || event.source !== elements.app.contentWindow || event.data?.type !== "fibona_ready" || appChannel) return;
  const { port1, port2 } = new MessageChannel();
  appPort = port1;
  const revision = appRevision;
  const channel = new FibonaChannel(message => port1.postMessage(message), {
    "show-console": ({ visible }) => showConsole(visible),
    chrome: call => backgroundRequest("chrome", call),
    send: async ({ data }) => {
      if (!host) throw new Error("Fibona: session is connecting; try again shortly");
      return host.request("ui", { data });
    },
    log: item => reportAppLog(item),
    message: async message => {
      if (revision !== appRevision) return;
      if (message.type === "connected") {
        appReady = true;
        channel.send({ type: "run", code: appCode, context });
        // A startup program may await incoming data; do not wait for it to finish before delivering messages.
        for (const data of pendingAppData.splice(0)) channel.send({ type: "data", data });
      } else if (message.type === "native") await appBridge.receive(message.message);
    },
  });
  appChannel = channel;
  appBridge = new FibonaNativeBridge(message => channel.send({ type: "native", message }), globalThis);
  port1.onmessage = ({ data }) => {
    if (revision === appRevision) channel.receive(data).catch(error => reportAppLog({ text: error.message, level: "error" }));
  };
  elements.app.contentWindow.postMessage({ type: "fibona_connect" }, "*", [port2]);
});

function populateSettings(settings) {
  savedSettings = settings;
  if (!settings) { elements.settings.open = true; return; }
  elements["base-url"].value = settings.baseURL || "https://api.openai.com/v1";
  elements["api-key"].value = settings.apiKey || "";
  elements.model.value = settings.model || "gpt-6-astra";
  if (!settings.apiKey) elements.settings.open = true;
}

async function saveSettings() {
  const settings = {
    baseURL: elements["base-url"].value.trim().replace(/\/+$/, ""),
    apiKey: elements["api-key"].value.trim(),
    model: elements.model.value.trim(),
  };
  if (!settings.apiKey || !settings.model) throw new Error("Fibona: enter an API key and model in Model settings");
  await chrome.storage.local.set({ fibona_settings: settings });
  savedSettings = settings;
  if (host) await host.request("configured");
  elements["settings-status"].textContent = "Saved";
}

elements["settings-form"].addEventListener("submit", async event => {
  event.preventDefault();
  elements["save-settings"].disabled = true;
  try { await saveSettings(); }
  catch (error) { elements["settings-status"].textContent = error.message; }
  finally { elements["save-settings"].disabled = false; }
});
elements["settings-form"].addEventListener("input", () => { elements["settings-status"].textContent = ""; });
elements["input-form"].addEventListener("submit", async event => {
  event.preventDefault();
  const text = elements.message.value;
  if (!text.trim() || elements.send.disabled) return;
  // Custom startup programs may accept input without ever calling a model.
  if (!savedSettings?.apiKey) elements.settings.open = true;
  submitting = true;
  updateControls();
  try {
    await host.request("input", { text });
    if (elements.message.value === text) elements.message.value = "";
  } catch (error) { showError(error); }
  finally { submitting = false; updateControls(); }
});
elements.message.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements["input-form"].requestSubmit();
  }
});
// Discard the session and start a fresh one, after `before` (a storage change) if given.
async function restartSession(before) {
  restarting = true;
  connectionRevision += 1;
  connecting = false;
  clearTimeout(reconnectTimer);
  reconnectTimer = undefined;
  host?.close("Fibona: session restarted");
  host = undefined;
  hostPort?.disconnect();
  hostPort = undefined;
  clearTimeout(forceTimer);
  forceTimer = undefined;
  forceOffered = false;
  updateControls();
  elements.status.textContent = "Restarting…";
  try {
    await (before ? before() : backgroundRequest("reset"));
  } catch (error) { showError(error); }
  finally { restarting = false; connectHost(); }
}

function stopTask() {
  if (elements.stop.disabled) return;
  if (forceOffered) return restartSession();
  elements.status.textContent = "Stopping…";
  forceTimer ??= setTimeout(() => {
    forceTimer = undefined;
    forceOffered = !runEnded();
    updateControls();
  }, FORCE_AFTER);
  host?.request("stop").catch(showError);
}
elements.stop.addEventListener("click", stopTask);
addEventListener("keydown", event => {
  if (event.key === "Escape" && !event.isComposing && document.documentElement.dataset.view === "console") stopTask();
});

elements["enter-safe"].addEventListener("click", () =>
  restartSession(() => backgroundRequest("safe-mode", { on: true, reason: "started by you" })));
elements["exit-safe"].addEventListener("click", () => restartSession(() => backgroundRequest("safe-mode", { on: false })));

// The service worker clears storage, origin data and browser registrations with no session running; localStorage
// needs a page, so this view clears it, then starts over.
elements["clear-all"].addEventListener("click", async () => {
  if (!confirm("Delete everything Fibona built and saved? This cannot be undone. Model settings are kept.")) return;
  restarting = true;
  updateControls();
  elements["troubleshooting-status"].textContent = "Clearing…";
  try {
    await backgroundRequest("clear-all");
    localStorage.clear();
    sessionStorage.clear();
    location.replace("index.html?mode=console");  // This view starts over too.
  } catch (error) {
    restarting = false;
    updateControls();
    elements["troubleshooting-status"].textContent = "Clearing failed: " + error.message;
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if ("fibona_safe_mode" in changes && !safeLink && Boolean(changes.fibona_safe_mode.newValue) !== safeMode) location.reload();
  if ("fibona_safe_mode" in changes && safeLink && !changes.fibona_safe_mode.newValue) location.replace("index.html?mode=console");
  if ("fibona_app" in changes) reloadApp();
  if ("fibona_settings" in changes) populateSettings(changes.fibona_settings.newValue);
});

elements["safe-notice"].hidden = !safeMode;
elements["safe-reason"].textContent = safeState?.reason ? ` (${safeState.reason})` : "";
elements["enter-safe"].parentElement.hidden = safeMode;
// The safe-mode link turns safe mode on, so the session also starts without the saved startup.
if (safeLink && !safeState) restartSession(() => backgroundRequest("safe-mode", { on: true, reason: "opened from the safe-mode link" }));
showConsole(document.documentElement.dataset.view === "console");
chrome.storage.local.get("fibona_settings").then(saved => populateSettings(saved.fibona_settings)).catch(showError);
reloadApp();
if (!(safeLink && !safeState)) connectHost();
