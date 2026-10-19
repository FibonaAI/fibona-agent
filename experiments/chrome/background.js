// Fibona's service worker, the only context with all of chrome.*. It keeps the offscreen document (the host, and the
// session in it) open, runs chrome.* methods for the session, forwards the events it subscribed to, runs the default
// actions of the toolbar button, shortcut and omnibox, and restarts or clears the session for the Console.
// Chrome stops this worker when idle and starts it again for an event; the session lives on in the offscreen document.
//
// Contexts, and how they talk (runtime/channel.js for requests, runtime/native-bridge.js for native objects):
//   background.js (this worker)   <- chrome.runtime messages ->  runtime/offscreen.js (the host)
//   runtime/offscreen.js          <- MessagePort ->              runtime/session.js (the session, where cells run)
//   index.html + console/         <- chrome.runtime Port ->      runtime/offscreen.js
//   index.html + console/         <- MessagePort ->              app/frame.js (the App, in each view)
import { encodeBrowserValue, decodeBrowserValue } from "./runtime/browser-wire.js";
import "./runtime/native-bridge.js";

const HOST = "runtime/offscreen.html";

// --- THE HOST ------------------------------------------------------------------------------------------

let creating = null;
let resetting = null;
async function ensureHost() {
  if (resetting) await resetting;
  creating ??= (async () => {
    const open = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
    if (open.length) return;
    // A new session has subscribed to nothing yet.
    for (const path of await subscriptions) detachLazy(path);
    (await subscriptions).clear();
    await chrome.storage.session.remove("fibona_subscriptions");
    await chrome.offscreen.createDocument({ url: HOST, reasons: ["IFRAME_SCRIPTING", "BLOBS", "CLIPBOARD", "LOCAL_STORAGE"],
      justification: "Keep Fibona's JavaScript session running while no Fibona page is open." });
  })().finally(() => {
    creating = null;
  });
  await creating;
}

const toHost = async (type, params) => {
  await ensureHost();
  return chrome.runtime.sendMessage({ to: "host", type, params });
};

// Restart the session (Force restart, safe mode, clearing): close the host, so the session and everything it held go,
// and start a new one. Debugger sessions the old one left attached are detached, since their banners would stay.
// `between` runs while no session exists.
async function reset(between) {
  resetting ??= (async () => {
    if (creating) await creating;
    if ((await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] })).length) await chrome.offscreen.closeDocument();
    // Include operations accepted before the restart, even if their native attach has not completed yet.
    await queueDebuggerOperation(async () => {
      const saved = await chrome.storage.session.get("fibona_debugger_targets");
      for (const target of saved.fibona_debugger_targets ?? []) await chrome.debugger.detach(target).catch(() => {});
      await chrome.storage.session.remove("fibona_debugger_targets");
    });
    for (const bridge of deliveries.values()) bridge.close();
    deliveries.clear();
    await between?.();
  })().finally(() => {
    resetting = null;
  });
  await resetting;
  await ensureHost();
}

// Clear all data: everything Fibona built and saved, back to a fresh install with the model settings kept. It runs while
// no session exists, so nothing writes meanwhile, and the next session starts clean. localStorage has no worker API;
// the Console clears it. ponytail: native registrations Fibona can make are listed here by API; a new kind needs a line.
async function clearAll() {
  await reset(async () => {
    for (const { name } of await indexedDB.databases()) indexedDB.deleteDatabase(name);
    for (const key of await caches.keys()) await caches.delete(key);
    const root = await navigator.storage.getDirectory();
    for await (const name of root.keys()) await root.removeEntry(name, { recursive: true });
    const { fibona_settings } = await chrome.storage.local.get("fibona_settings");
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
    await chrome.storage.sync?.clear().catch(() => {});
    if (fibona_settings) await chrome.storage.local.set({ fibona_settings });
    await chrome.userScripts?.unregister().catch(() => {});
    await chrome.scripting.unregisterContentScripts().catch(() => {});
    await chrome.alarms.clearAll();
    const rules = await chrome.declarativeNetRequest?.getDynamicRules().catch(() => []) ?? [];
    await chrome.declarativeNetRequest?.updateDynamicRules({ removeRuleIds: rules.map((r) => r.id) }).catch(() => {});
    await chrome.declarativeNetRequest?.updateSessionRules({ removeRuleIds:
      (await chrome.declarativeNetRequest.getSessionRules().catch(() => [])).map((r) => r.id) }).catch(() => {});
    await chrome.action.setPopup({ popup: "" });
    await chrome.action.setBadgeText({ text: "" });
    await chrome.action.setTitle({ title: chrome.runtime.getManifest().action.default_title });
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});
    await chrome.contextMenus.removeAll();
    installMenus();
    await chrome.omnibox.setDefaultSuggestion({ description: " " }).catch(() => {});
  });
}

// --- CHROME METHODS ------------------------------------------------------------------------------------

function member(path) {
  const parts = path.split(".");
  if (parts.some((part) => !/^[A-Za-z]\w*$/.test(part) || part === "constructor" || part === "prototype")) {
    throw new Error(`Fibona: not a chrome.* path: ${path}`);
  }
  const name = parts.pop();
  const owner = parts.reduce((object, key) => object?.[key], chrome);
  if (typeof owner?.[name] !== "function") throw new Error(`Fibona: chrome.${path} is not a method here; check the manifest's permissions`);
  return { owner, method: owner[name] };
}

// Serialize native attach/detach together with their storage update, so cleanup sees every successful attach.
let debuggerOperations = Promise.resolve();
function queueDebuggerOperation(operation) {
  const result = debuggerOperations.then(operation);
  debuggerOperations = result.catch(() => {});
  return result;
}
function callChrome(call) {
  if (call.path === "debugger.attach" || call.path === "debugger.detach") {
    if (resetting) return Promise.reject(new Error("Fibona: the session is restarting; retry after it restarts"));
    return queueDebuggerOperation(() => invokeChrome(call));
  }
  return invokeChrome(call);
}

// callback: append a callback and resolve to what it receives, rejecting runtime.lastError.
async function invokeChrome({ path, args = [], callback = false }) {
  const { owner, method } = member(path);
  try {
    let result;
    if (!callback) result = await method.apply(owner, args);
    else result = await new Promise((resolve, reject) => {
      method.apply(owner, [...args, (...values) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else resolve(values.length > 1 ? values : values[0]);
      }]);
    });
    if (path === "debugger.attach" || path === "debugger.detach") {
      const saved = await chrome.storage.session.get("fibona_debugger_targets");
      const targets = (saved.fibona_debugger_targets ?? []).filter((target) => JSON.stringify(target) !== JSON.stringify(args[0]));
      if (path === "debugger.attach") targets.push(args[0]);
      await chrome.storage.session.set({ fibona_debugger_targets: targets });
    }
    return result;
  } catch (error) {
    if (path.startsWith("userScripts.") && !(await userScriptsAllowed())) {
      throw new Error(`User scripts are off for Fibona. Ask the user to turn on Allow User Scripts at chrome://extensions/?id=${chrome.runtime.id}, then try again.`);
    }
    throw error;
  }
}

async function userScriptsAllowed() {
  try {
    await chrome.userScripts.getScripts();
    return true;
  } catch {
    return false;
  }
}

// --- EVENTS --------------------------------------------------------------------------------------------
// A listener must be added when the worker starts for an event to wake it, so every event gets one now, except the
// webRequest events below. Only the paths the session subscribed to are forwarded; the others run their DEFAULTS, if
// any. The set outlives this worker in storage.session, until the session restarts or the browser quits.

const subscriptions = chrome.storage.session.get("fibona_subscriptions").then((saved) => new Set(saved.fibona_subscriptions ?? []));
const saveSubscriptions = async () => chrome.storage.session.set({ fibona_subscriptions: [...(await subscriptions)] });

// Functions among an event's arguments (sendResponse, omnibox's suggest) stay here and cross as references, through one
// bridge per event. The newest DELIVERY_LIMIT bridges are kept until the session releases what it holds.
const deliveries = new Map();  // Bridge namespace -> bridge
const DELIVERY_LIMIT = 100;

async function forward(path, args) {
  const bridge = new FibonaNativeBridge(() => {
    throw new Error("Fibona: event arguments cannot call back into the session");
  });
  const encoded = bridge.encode(args);
  if (bridge.references.size > 1) {  // Reference 0 is the bridge's own root.
    deliveries.set(bridge.namespace, bridge);
    for (const [namespace, old] of deliveries) {
      if (deliveries.size <= DELIVERY_LIMIT) break;
      old.close();
      deliveries.delete(namespace);
    }
  }
  await toHost("event", { path, args: await encodeBrowserValue(encoded) });
}

// What the extension's own entry points do until the session subscribes to their event and takes them over. The agent
// can also change them natively, as with action.setPopup or sidePanel.setPanelBehavior.
const DEFAULTS = {
  "action.onClicked": async () => {
    if (!(await chrome.sidePanel.getPanelBehavior()).openPanelOnActionClick) await chrome.tabs.create({ url: "index.html" });
  },
  "commands.onCommand": (command) => command === "fibona-command" && chrome.tabs.create({ url: "index.html?mode=console" }),
  "omnibox.onInputEntered": async (text) => {
    await chrome.tabs.create({ url: "index.html?mode=console" });
    await toHost("input", { text });
  },
};

async function dispatch(path, args) {
  try {
    if ((await subscriptions).has(path)) await forward(path, args);
    else await DEFAULTS[path]?.(...args);
  } catch (error) {
    console.error(`Fibona: ${path}`, error);
  }
}

// webRequest events fire for every request in the browser, so they are listened to only while subscribed, instead of
// waking this worker for all traffic. ponytail: after the worker restarts they are added back once storage is read, so
// the request that woke it may be missed.
const lazyEvents = new Map();  // Path -> {event, listener, attached}
const isLazy = (path) => path.startsWith("webRequest.");
function attachLazy(path) {
  const lazy = lazyEvents.get(path);
  if (!lazy || lazy.attached) return;
  lazy.event.addListener(lazy.listener, { urls: ["<all_urls>"] });  // Filter in the session's callback instead.
  lazy.attached = true;
}
function detachLazy(path) {
  const lazy = lazyEvents.get(path);
  if (!lazy?.attached) return;
  lazy.event.removeListener(lazy.listener);
  lazy.attached = false;
}

// Messages from web pages: a listener that may reply later must say so before returning. Fibona's own messages,
// from its pages, are not events.
const REPLYING = ["runtime.onMessage", "runtime.onMessageExternal", "runtime.onUserScriptMessage"];

const EVENTS = [];
(function listen(object, prefix, depth) {
  for (const key of Object.keys(object)) {
    const path = prefix + key;
    let value;
    try {
      value = object[key];
    } catch {
      continue;
    }
    if (!value || typeof value !== "object" || depth > 3) continue;
    if (typeof value.addListener !== "function") {
      listen(value, path + ".", depth + 1);
      continue;
    }
    if (path.startsWith("runtime.") && !REPLYING.includes(path)) continue;
    const listener = REPLYING.includes(path)
      ? (message, sender, sendResponse) => {
          if (message?.to === "background" || message?.to === "host") return;  // Fibona's own.
          subscriptions.then((paths) => (paths.has(path) ? dispatch(path, [message, sender, sendResponse]) : sendResponse()));
          return true;
        }
      : (...args) => dispatch(path, args);
    try {
      if (isLazy(path)) lazyEvents.set(path, { event: value, listener, attached: false });
      else value.addListener(listener);
      EVENTS.push(path);
    } catch {
      // Some events need options or permissions this installation lacks.
    }
  }
})(chrome, "", 0);
subscriptions.then((paths) => paths.forEach((path) => {
  try {
    attachLazy(path);
  } catch (error) {
    console.error(`Fibona: ${path}`, error);
  }
}));

// --- MESSAGES FROM FIBONA'S PAGES ----------------------------------------------------------------------
// index.html and the host send {to: "background", type, params}; the reply is {value} or {error}.

const handlers = {
  ensure: () => ensureHost(),
  reset: () => reset(),
  // Safe mode changes what a fresh session runs, so it restarts the session with the flag set or cleared.
  "safe-mode": ({ on, reason }) => reset(() => on
    ? chrome.storage.local.set({ fibona_safe_mode: { reason } })
    : chrome.storage.local.remove("fibona_safe_mode")),
  "clear-all": () => clearAll(),
  chrome: callChrome,
  subscribe: async ({ path }) => {
    if (!EVENTS.includes(path)) throw new Error(`Fibona: no Chrome event ${path} here; check its name and the manifest's permissions`);
    attachLazy(path);
    (await subscriptions).add(path);
    await saveSubscriptions();
  },
  unsubscribe: async ({ path }) => {
    detachLazy(path);
    (await subscriptions).delete(path);
    await saveSubscriptions();
  },
  // The session calls a function from an event's arguments: run it here and return the reply.
  "event-native": async ({ message }) => {
    const bridge = deliveries.get(String(message.reference).split(":")[0]);
    if (!bridge) return { type: "reply", id: message.id, error: "Fibona: this event's references were released or expired" };
    let reply;
    await bridge.receive(message, (answer) => {
      reply = answer;
    });
    if (bridge.references.size === 1) {  // Everything it lent was released.
      bridge.close();
      deliveries.delete(bridge.namespace);
    }
    return reply;
  },
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const trusted = sender.id === chrome.runtime.id && ["index.html", HOST]
    .some((path) => sender.url?.split("?")[0] === chrome.runtime.getURL(path));
  if (message?.to !== "background" || !trusted) return;
  (async () => {
    const handler = handlers[message.type];
    if (!handler) throw new Error(`Fibona: unknown request ${message.type}`);
    return { value: await encodeBrowserValue(await handler(decodeBrowserValue(message.params))) };
  })()
    .catch((error) => ({ error: error?.message ?? String(error), errorName: error?.name }))
    .then(sendResponse);
  return true;
});

// --- STARTING ------------------------------------------------------------------------------------------

// Fibona's own entries on the toolbar button's menu, recreated whenever this worker starts: the agent has every
// chrome.* method and may remove them, but the user's way back must stay.
function installMenus() {
  for (const [id, title] of [
    ["fibona-open-page", "Open Fibona"],
    ["fibona-safe-mode", "Open Fibona in safe mode"],
  ]) {
    const properties = { title, contexts: ["action"] };
    chrome.contextMenus.update(id, properties, () => {
      if (chrome.runtime.lastError) chrome.contextMenus.create({ id, ...properties }, () => void chrome.runtime.lastError);
    });
  }
}


// The session starts with the browser, so its startup runs without a view open.
chrome.runtime.onStartup.addListener(() => ensureHost().catch(console.error));
chrome.runtime.onInstalled.addListener(() => ensureHost().catch(console.error));
installMenus();
chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === "fibona-open-page") chrome.tabs.create({ url: "index.html?mode=console" });
  else if (info.menuItemId === "fibona-safe-mode") chrome.tabs.create({ url: "index.html?safe=1" });
});
