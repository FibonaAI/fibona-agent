// A visible view attaches to the singleton background REPL. Closing this page never stops that REPL.
import { encodeBrowserValue, decodeBrowserValue } from "../runtime/browser-wire.js";
import "../runtime/native-bridge.js";
import { ConsoleActivity } from "./console-activity.js";
import { getUserScriptsAccess } from "../runtime/chrome-bridge.js";
const elements = Object.fromEntries(["status", "settings", "start-form", "base-url", "api-key", "model", "start", "log", "input-form", "message", "send", "stop", "session", "console-panel", "recovery", "view-label", "view-target", "page-script-access", "open-script-settings"].map(id => [id, document.getElementById(id)]));
const activity = new ConsoleActivity(elements.log);
const parameters = new URL(location.href).searchParams;
const safeMode = parameters.get("safe") === "1";
const surface = parameters.get("surface") ?? "tab";
document.body.dataset.surface = surface;
let port, sequence = 0, framePort, frameHostBridge, frameReady = false, viewRevision = 0, viewInfo, environment, sessionId, appCode = "";
let state = { active: false, waiting: false, console: true, status: "Connecting…" }, resetting = Promise.resolve();
const pending = new Map(), queuedData = [];
let switchDrag, suppressSwitchClick = false;

async function refreshPageScriptAccess() {
  const access = await getUserScriptsAccess();
  elements["page-script-access"].hidden = access.available;
  elements["open-script-settings"].title = access.message ?? "Webpage scripts enabled";
}
elements["open-script-settings"].addEventListener("click", () => chrome.tabs.create({url:"chrome://extensions/?id="+chrome.runtime.id}));
addEventListener("focus", refreshPageScriptAccess);

function positionViewSwitch(left, top) {
  const button = elements.recovery;
  const { width, height } = button.getBoundingClientRect();
  Object.assign(button.style, {
    left: Math.max(8, Math.min(left, innerWidth - width - 8)) + "px",
    top: Math.max(8, Math.min(top, innerHeight - height - 8)) + "px",
    right: "auto", bottom: "auto",
  });
}
function constrainViewSwitch() {
  if (elements.recovery.style.left) positionViewSwitch(parseFloat(elements.recovery.style.left), parseFloat(elements.recovery.style.top));
}
elements.recovery.addEventListener("pointerdown", event => {
  if (!event.isPrimary || event.button !== 0) return;
  elements.recovery.dataset.dragging = "true";
  document.body.classList.add("moving-switch");
  const rect = elements.recovery.getBoundingClientRect();
  switchDrag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
  suppressSwitchClick = false;
  elements.recovery.setPointerCapture(event.pointerId);
});
elements.recovery.addEventListener("pointermove", event => {
  if (switchDrag?.id !== event.pointerId) return;
  const dx = event.clientX - switchDrag.x, dy = event.clientY - switchDrag.y;
  if (!suppressSwitchClick && Math.hypot(dx, dy) < 5) return;
  suppressSwitchClick = true;
  positionViewSwitch(switchDrag.left + dx, switchDrag.top + dy);
});
elements.recovery.addEventListener("lostpointercapture", () => {
  switchDrag = undefined;
  delete elements.recovery.dataset.dragging;
  document.body.classList.remove("moving-switch");
});
addEventListener("resize", constrainViewSwitch);

async function request(command, value = {}) {
  const id = ++sequence, encoded = await encodeBrowserValue(value);
  return await new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    try { port.postMessage({ id, command, value: encoded }); }
    catch (error) { pending.delete(id); reject(error); }
  });
}
function showConsole(visible) {
  visible = safeMode || visible;
  elements["console-panel"].hidden = !visible;
  elements.recovery.setAttribute("aria-expanded", String(Boolean(visible)));
  elements["view-label"].textContent = visible ? "Console" : "App";
  elements["view-target"].textContent = visible ? "Open app" : "Open console";
  elements.recovery.title = (visible ? "Viewing console. Switch to the app." : "Viewing app. Switch to the console.") + " Drag to move.";
  constrainViewSwitch();
}
function applyState(values) {
  const follow = elements.log.scrollHeight - elements.log.scrollTop - elements.log.clientHeight < 40;
  Object.assign(state, values);
  elements.status.textContent = state.status;
  elements.start.disabled = state.active || !viewInfo || state.status === "Stopping…";
  elements.stop.disabled = !viewInfo || state.status === "Stopping…";
  elements.message.disabled = !viewInfo;
  elements.send.disabled = !viewInfo || (state.active && !state.waiting);
  elements.message.placeholder = state.active && !state.waiting ? "Fibona is working… You can draft a message." : "Give Fibona a task…";
  if (state.active) elements.settings.open = false;
  if (state.status === "Stopped") elements.settings.open = true;
  if (state.status.startsWith("Startup failed")) elements.settings.open = true;
  elements.start.hidden = state.active;
  if ("console" in values) showConsole(safeMode ? true : state.console);
  if ("title" in values) document.title = state.title;
  if (follow) elements.log.scrollTop = elements.log.scrollHeight;
}
function appendOutput(text, className = "") {
  activity.appendLocal(text, className);
}
function appendLog(item) {
  activity.update(item);
}
async function recoverConsoleState(error) {
  try { applyState(await request("state")); }
  catch (failure) { appendOutput(failure.message + "\n", "error"); }
  showConsole(true);
  appendOutput(error.message + "\n", "error");
}
function resetView(code) {
  appCode = code;
  if (safeMode) { elements.session.hidden = true; elements.session.setAttribute("aria-busy", "false"); return; }
  const revision = ++viewRevision;
  frameReady = false; queuedData.length = 0;
  elements.session.setAttribute("aria-busy", "true");
  resetting = resetting.then(async () => {
    if (revision !== viewRevision) return;
    await request("frame-reset");
    if (revision !== viewRevision) return;
    frameHostBridge?.close(); framePort?.close(); framePort = undefined;
    // Replacing the element gives this document a new Window identity; old ready messages cannot capture its port.
    const replacement = elements.session.cloneNode(false);
    replacement.removeAttribute("src"); replacement.setAttribute("aria-busy", "true");
    elements.session.replaceWith(replacement); elements.session = replacement;
    replacement.src = "runtime/session.html?role=view";
  }).catch(error => appendOutput("View could not load: " + error.message + "\n", "error"));
}
function receivePush(message) {
  if (message.type === "state") applyState(message.values);
  else if (message.type === "log" || message.type === "log-update") appendLog(message.value);
  else if (safeMode) return;
  else if (message.type === "ui") resetView(message.code);
  else if (message.type === "frame-message") framePort?.postMessage(message.message);
  else if (message.type === "ui-data") {
    if (frameReady) framePort.postMessage({ type: "ui-data", data: message.data });
    else queuedData.push(message.data);
  }
}
function connect() {
  const connection = chrome.runtime.connect({ name: "fibona-view" }); port = connection;
  connection.onMessage.addListener(message => {
    if (message.type === "resetting") { applyState({status:"Stopping…"}); elements.session.setAttribute("aria-busy", "true"); return; }
    if (message.type === "reset") { connectSnapshot(); return; }
    if (message.type === "push") { receivePush(decodeBrowserValue(message.value)); return; }
    const waiter = pending.get(message.id); if (!waiter) return;
    pending.delete(message.id);
    if (message.error) waiter.reject(Object.assign(new Error(message.error), { name: message.errorName ?? "Error" }));
    else waiter.resolve(decodeBrowserValue(message.value));
  });
  connection.onDisconnect.addListener(() => {
    for (const waiter of pending.values()) waiter.reject(new Error("Fibona browser connection interrupted; reconnecting"));
    pending.clear(); viewInfo = undefined; elements.start.disabled = true; elements.stop.disabled = true;
    elements.session.setAttribute("aria-busy", "true");
    if (chrome.runtime.id) { elements.status.textContent = "Reconnecting…"; connect(); }
    else elements.status.textContent = "Extension reloaded; refresh this page";
  });
  function connectSnapshot() { request("attach", { surface, safeMode, url: location.href }).then(snapshot => {
    const reuseFrame = sessionId === snapshot.sessionId && appCode === snapshot.ui && framePort && frameReady;
    sessionId = snapshot.sessionId;
    viewInfo = snapshot.view; environment = snapshot.environment;
    if (snapshot.settings) {
      elements["base-url"].value = snapshot.settings.baseURL;
      elements["api-key"].value = snapshot.settings.apiKey;
      elements.model.value = snapshot.settings.model;
    }
    activity.clear();
    snapshot.log.forEach(appendLog);
    applyState(snapshot);
    if (safeMode && !snapshot.active) elements.status.textContent = "Recovery mode";
    if (reuseFrame) elements.session.setAttribute("aria-busy", "false");
    else resetView(snapshot.ui);
  }).catch(error => { elements.status.textContent = "Connection failed"; appendOutput(error.message + "\n", "error"); }); }
  connectSnapshot();
}
addEventListener("message", event => {
  if (event.source !== elements.session.contentWindow || event.data?.type !== "fibona_ready" || framePort) return;
  const channel = new MessageChannel(), current = channel.port1, revision = viewRevision; framePort = current;
  frameHostBridge = new FibonaNativeBridge(message => current.postMessage({type:"view-native",message}),globalThis);
  current.onmessage = async ({ data }) => {
    if (framePort !== current || revision !== viewRevision) return;
    if (data.type === "connected") {
      current.postMessage({ type: "run-view", code: appCode || 'document.body.style="margin:32px;color:#242321;font:16px system-ui";document.body.textContent="Fibona can build an app here. Open the console to give it a task.";', view: viewInfo, environment });
    } else if (data.type === "view-ready") {
      frameReady = true;
      elements.session.setAttribute("aria-busy", "false");
      if (data.error) { appendOutput("App error: " + data.error + "\n", "error"); showConsole(true); }
      for (const value of queuedData.splice(0)) current.postMessage({ type: "ui-data", data: value });
    } else if (data.type === "view-native") {
      frameHostBridge.receive(data.message);
    } else if (data.type === "native") {
      request("native", data.message).catch(error => appendOutput(error.message + "\n", "error"));
    } else if (data.type === "event-native") {
      request("frame", {method:"event-native",message:data.message}).catch(error => {
        current.postMessage({type:"event-native",message:{type:"reply",id:data.message.id,error:error.message}});
      });
    } else if (data.type === "request") {
      try { const value = await request("frame", data); if (framePort === current) current.postMessage({ type: "reply", id: data.id, value }); }
      catch (error) { if (framePort === current) current.postMessage({ type: "reply", id: data.id, error: error.message, errorName: error.name }); }
    } else if (data.type === "output") request("output", {text:data.text,level:data.level}).catch(console.error);
  };
  current.start(); elements.session.contentWindow.postMessage({ type: "fibona_connect" }, "*", [channel.port2]);
});
function modelSettings() {
  return {baseURL:elements["base-url"].value.trim().replace(/\/+$/, ""),apiKey:elements["api-key"].value.trim(),model:elements.model.value.trim()};
}
elements["start-form"].addEventListener("submit", event => {
  event.preventDefault();
  applyState({active:true,waiting:false,status:"Starting"});
  request("start", {settings:modelSettings()}).catch(recoverConsoleState);
});
elements["start-form"].addEventListener("invalid", () => { elements.settings.open = true; }, true);
elements["input-form"].addEventListener("submit", event => {
  event.preventDefault();
  const text = elements.message.value;
  if (!text.trim() || elements.send.disabled) return;
  const starting = !state.active && !state.waiting;
  if (starting && !elements["start-form"].reportValidity()) return;
  applyState({active:starting || state.active,waiting:false,status:starting?"Starting":"Running"});
  request(starting?"start":"input", starting?{settings:modelSettings(),text}:{text})
    .then(() => { elements.message.value = ""; })
    .catch(recoverConsoleState);
});
elements.message.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); elements["input-form"].requestSubmit(); }
});
elements.stop.addEventListener("click", () => {
  applyState({status:"Stopping…"}); elements.session.setAttribute("aria-busy", "true");
  request("stop").catch(error => appendOutput(error.message + "\n", "error"));
});
elements.recovery.addEventListener("click", event => {
  if (event.detail && suppressSwitchClick) { suppressSwitchClick = false; return; }
  showConsole(elements["console-panel"].hidden);
});
elements.recovery.hidden = safeMode;
document.body.classList.toggle("console-only", safeMode);
showConsole(true); refreshPageScriptAccess(); connect();
