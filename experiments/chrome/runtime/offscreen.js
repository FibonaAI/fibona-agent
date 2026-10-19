// The host in Chrome's offscreen document, under the extension's origin. It keeps the session's sandboxed iframe
// alive for the browser's life and does what the session's opaque origin cannot: model requests with the saved key,
// Web APIs for fibona_browser, and the Console log every view shows. background.js creates this page and reaches
// chrome.*; an offscreen document itself only has chrome.runtime.
import { encodeBrowserValue, decodeBrowserValue } from "./browser-wire.js";
import "./channel.js";
import "./native-bridge.js";

// The packaged source, for fibona_source_code: read-only, so the agent can see how it is built.
const SOURCE_FILES = ["manifest.json", "background.js", "index.html", "README.md", "icons/fibona.svg", "agent/core.js", "agent/bootstrap.js",
  "app/bootstrap.js", "app/frame.html", "app/frame.js", "console/console.js", "console/console.css", "console/entry.js",
  "runtime/offscreen.html", "runtime/offscreen.js", "runtime/session.html", "runtime/session.js", "runtime/channel.js",
  "runtime/native-bridge.js", "runtime/browser-wire.js"];

// A chrome.* method, a subscription or another request to the service worker, over JSON.
async function background(type, params = {}) {
  const reply = await chrome.runtime.sendMessage({ to: "background", type, params: await encodeBrowserValue(params) });
  if (reply?.error !== undefined) throw Object.assign(new Error(reply.error), { name: reply.errorName ?? "Error" });
  return decodeBrowserValue(reply?.value);
}

// --- CONSOLES ------------------------------------------------------------------------------------------
// What every console shows: the conversation and the cells, and a status line. Views get the whole log when they
// attach, then each item as it is added or changes.

const views = new Map();  // View id -> {channel, metadata}
const log = [];
const LOG_LIMIT = 2000;
let logSequence = 0;
// state: what the session last reported. thinking: a model request is in flight. running: the session is doing work
// the user can stop.
const status = { state: "Starting", thinking: false, running: false };
let modelRequests = 0;
let runningCell = null;  // The log item of the cell that is running.

function broadcast(message, viewId) {
  for (const [id, view] of views) if (!viewId || id === viewId) view.channel.send(message).catch(() => {});
}
function addLog(item) {
  item.id = ++logSequence;
  log.push(item);
  log.splice(0, Math.max(0, log.length - LOG_LIMIT));
  broadcast({ type: "log", item });
  return item;
}
const updateLog = (item) => broadcast({ type: "log", item });

// Consecutive writes join one message, until a cell or the user interrupts them. level is a console method name
// (log, info, warn, error): App console output and errors carry it, so the Console can mark warnings and errors.
function addText(text, level = "log", source = "fibona") {
  const last = log.at(-1);
  if (last?.type === "text" && last.level === level && last.source === source) {
    last.text += text;
    return updateLog(last);
  }
  addLog({ type: "text", text, level, source });
}

function setStatus(values) {
  Object.assign(status, values, { thinking: modelRequests > 0 });
  broadcast({ type: "status", status });
}

function receiveInput(text) {
  addLog({ type: "user", text });
  session.send({ type: "input", text });
}

// --- MODEL ---------------------------------------------------------------------------------------------

// The model settings that the console saved: {baseURL, apiKey, model}. Read for every request, so a change applies
// at once; the key never enters the session.
const settings = async () => (await background("chrome", { path: "storage.local.get", args: ["fibona_settings"] })).fibona_settings;

// Resolves once settings exist. This page cannot watch chrome.storage, so the Console calls configure() after saving.
let configure;
const configured = new Promise((resolve) => {
  configure = resolve;
});
settings().then((saved) => saved?.apiKey && configure(), console.error);

const aborts = new Set();  // In-flight model requests, which the user's Stop cancels.

// One Responses API request; the model is the settings' unless the body names one. It streams, so a long think is not
// cut off waiting for headers. A provider that answers with plain JSON works too.
async function request(body) {
  let config = await settings();
  if (!config?.apiKey) {
    setStatus({ state: "Waiting for model settings" });
    await configured;
    config = await settings();
  }
  if (!config?.apiKey) throw new Error("Fibona: model settings are missing; save them in the Console");
  modelRequests += 1;
  setStatus({});
  const controller = new AbortController();
  aborts.add(controller);
  try {
    const response = await fetch(config.baseURL + "/responses", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + config.apiKey },
      body: JSON.stringify({ model: config.model, ...body, stream: true }),
    });
    if (!response.ok) throw new Error(`Model request failed: HTTP ${response.status}: ${await response.text()}`);
    if (!response.headers.get("Content-Type")?.includes("text/event-stream")) return withText(await response.json());
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value.replaceAll("\r", "");
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const event of events) {
        const data = event.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
        if (!data || data === "[DONE]") continue;
        const e = JSON.parse(data);
        if (e.type === "response.completed" || e.type === "response.incomplete") return withText(e.response);
        if (e.type === "response.failed") throw new Error(e.response?.error?.message ?? "Model request failed.");
        if (e.type === "error") throw new Error(e.message ?? JSON.stringify(e));
      }
    }
    throw new Error("Model stream ended without a final response.");
  } finally {
    aborts.delete(controller);
    modelRequests -= 1;
    setStatus({});
  }
}
function withText(response) {
  if (response.error || response.status === "failed") throw new Error(response.error?.message ?? "Model request failed.");
  response.output_text = (response.output ?? []).filter((o) => o.type === "message").flatMap((o) => o.content)
    .filter((c) => c.type === "output_text").map((c) => c.text).join("");
  return response;
}

// --- SESSION -------------------------------------------------------------------------------------------

// The FibonaChannel to session.js, once its iframe connects; until then, messages for it wait.
let channel = null;
const early = [];
let connect;
const connected = new Promise((resolve) => {
  connect = resolve;
});
const session = {
  send: (message) => (channel ? channel.send(message) : early.push(message)),
  request: async (method, params) => (await connected).request(method, params),
};
const browser = new FibonaNativeBridge((message) => session.send({ type: "native", message }), globalThis);

// Requests and messages from session.js.
const sessionHandlers = {
  model: ({ body }) => request(body),
  chrome: (call) => background("chrome", call),
  subscribe: ({ path }) => background("subscribe", { path }),
  unsubscribe: ({ path }) => background("unsubscribe", { path }),
  "ui-send": ({ data, viewId }) => {
    if (viewId && !views.has(viewId)) throw new Error(`Fibona: no open view ${viewId}`);
    broadcast({ type: "ui-data", data }, viewId);
  },
  message: async (message) => {
    if (message.type === "write") addText(message.text);
    else if (message.type === "state") {
      setStatus({ state: message.state, running: message.running });
      // A cell blocked in fibona_input is waiting for the user, not working: say so on the cell too.
      const waiting = message.state === "Waiting for input";
      if (runningCell && (waiting || runningCell.status === "waiting")) {
        runningCell.status = waiting ? "waiting" : "running";
        updateLog(runningCell);
      }
    }
    else if (message.type === "abort-model") for (const controller of aborts) controller.abort();
    else if (message.type === "cell") {
      runningCell = addLog({ type: "cell", number: message.number, summary: message.summary, code: message.code, status: "running" });
    } else if (message.type === "result") {
      const { error, quit, stopped } = message.result;
      if (runningCell) {
        Object.assign(runningCell, { status: stopped && (quit || error) ? "stopped" : error ? "error" : quit ? "quit" : "done", error });
        updateLog(runningCell);
      }
      runningCell = null;
    } else if (message.type === "native") await browser.receive(message.message);
    else if (message.type === "event-native") {
      // A call on a function from a Chrome event's arguments, which lives in the service worker.
      const reply = await background("event-native", { message: message.message }).catch((error) =>
        ({ type: "reply", id: message.message.id, error: error.message }));
      session.send({ type: "event-native", message: reply });
    }
  },
};

// session.html announces itself; it gets a MessagePort, then the packaged source to start with.
addEventListener("message", (event) => {
  const frame = document.getElementById("session");
  if (event.source !== frame.contentWindow || event.data?.type !== "fibona_ready" || channel) return;
  const { port1, port2 } = new MessageChannel();
  const opening = new FibonaChannel((message) => port1.postMessage(message), {
    ...sessionHandlers,
    message: async (message) => {
      if (message.type !== "connected") return sessionHandlers.message(message);
      channel = opening;
      connect(channel);
      for (const queued of early.splice(0)) channel.send(queued);
      const sources = Object.fromEntries(await Promise.all(SOURCE_FILES.map(async (file) =>
        [file, await (await fetch(chrome.runtime.getURL(file))).text()])));
      channel.send({ type: "start", sources });
    },
  });
  port1.onmessage = ({ data }) => opening.receive(data);
  frame.contentWindow.postMessage({ type: "fibona_connect" }, "*", [port2]);
});

// --- VIEWS AND THE SERVICE WORKER ----------------------------------------------------------------------

// Each view (index.html in a tab, popup or side panel) connects here with a chrome.runtime Port.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "fibona-view" || port.sender?.id !== chrome.runtime.id ||
      !port.sender?.url?.startsWith(chrome.runtime.getURL("index.html"))) return;
  const id = crypto.randomUUID();
  let sending = Promise.resolve();  // Encoding is async; this keeps messages in order.
  const send = (message) => (sending = sending.catch(() => {}).then(async () => port.postMessage(await encodeBrowserValue(message))));
  const view = { metadata: { id }, channel: null };
  view.channel = new FibonaChannel(send, {
    attach: (metadata) => {
      Object.assign(view.metadata, metadata, { id });
      views.set(id, view);
      return { id, log, status };
    },
    input: ({ text }) => receiveInput(String(text)),
    stop: () => session.send({ type: "stop" }),
    configured: () => configure(),
    ui: ({ data }) => session.request("ui", { data, view: view.metadata }),
    "app-log": ({ text, level }) => {
      addText(text.endsWith("\n") ? text : text + "\n", level ?? "error", "app");
      session.send({ type: "app-log", text: `[view ${view.metadata.surface}] ${text}` });
    },
  });
  port.onMessage.addListener((message) => view.channel.receive(decodeBrowserValue(message)));
  port.onDisconnect.addListener(() => {
    views.delete(id);
    view.channel.close("Fibona: the view closed");
  });
});

// The service worker forwards subscribed events, and text typed after the omnibox keyword. Only this extension's own
// contexts may send them; content scripts it injects into webpages share its id but carry a tab.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.to !== "host" || sender.id !== chrome.runtime.id || sender.tab) return;
  const { type, params } = message;
  if (type === "event") session.send({ type: "event", path: params.path, args: decodeBrowserValue(params.args) });
  else if (type === "input") receiveInput(params.text);
  sendResponse({});
});

// Loaded only now, so its fibona_ready cannot arrive before the listener above.
document.getElementById("session").src = "session.html";
