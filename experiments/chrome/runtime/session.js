// Fibona's session in Chrome: a sandboxed iframe of the offscreen document, where cells may eval. This file is the
// host's side of the session: it binds platform names and runs the bootstrap cell, fibona_boot or agent/bootstrap.js.
// offscreen.js does what this opaque origin cannot (model requests and the Console log), and background.js, the
// service worker, reaches chrome.*. Like core.js it ships read-only, so the agent cannot break it.
// CHROME below is appended to core's INSTRUCTIONS: platform facts for the agent, not working policy, which belongs to
// the editable bootstrap.

const CHROME = `
## Chrome

You run in a sandboxed iframe of one offscreen document, shared by all Fibona views. Browser globals keep their names;
agent bindings use fibona_. fibona_source_code contains the packaged sources and manifest; platform details are in
fibona_source_code["README.md"] and fibona_source_code["runtime/session.js"].

- Chrome APIs: await fibona_chrome(path, ...args) calls chrome.* with data arguments. .callback supports callback-only
  methods; await fibona_on(eventPath, callback) returns an unsubscribe function. Functions in event arguments are async
  references. Subscribing to an event replaces its default action (toolbar click, shortcut, omnibox), if any.
  userScripts.execute runs generated webpage code and requires Chrome's Allow User Scripts setting.
  The debugger API works too; detach when finished (a session restart detaches what is still attached).
- Webpages: the active tab may be a Fibona view itself. When the user says "this page", query tabs to find the webpage.
- Web APIs: await fibona_browser(path, ...args) calls the offscreen window under the extension origin. Native objects
  and callbacks cross as async references with get, set, call, construct and release. This iframe has an opaque origin.
- Storage persists; variables do not outlive the session. chrome.storage.local fibona_boot, if present, is a code
  string that replaces agent/bootstrap.js as the bootstrap cell of every run: write what you build there to become it.
  It is the whole startup, so to keep the default behavior, start from fibona_source_code["agent/bootstrap.js"] and
  add to it. The origin file system is available through fibona_browser("navigator.storage.getDirectory").
  The host does not persist variables or history.
- Safe mode: when the user turns it on, or your saved startup fails before any cell succeeds, runs use
  agent/bootstrap.js and views do not run fibona_app. Both stay in storage and fibona_app_log says why: inspect and
  repair them, then tell the user to exit safe mode in the Console.
- App: each view runs chrome.storage.local fibona_app (default app/bootstrap.js) in a separate sandbox with its own DOM,
  on change and on reopen. fibona_send(data) calls session fibona_on_ui((data, view) => reply): register handlers
  before installing an App that depends on them. fibona_ui_send(data, viewId?) reaches App fibona_on_data callbacks.
  App fibona_view bridges to its outer page, fibona_show_console(visible) switches views, and fibona_ui_context
  identifies the view. App also has fibona_chrome. The outer #view-switch is editable DOM; safe mode loads no App.
- Your app in use: App errors and console output, and prints from callbacks, go to fibona_app_log; check it first when
  the user reports a problem. App code can read its own DOM and report it through fibona_send.
  tabs.captureVisibleTab screenshots a visible webpage; send it to fibona_call_me as {type: "input_image", image_url}.
- Lifetime: closing a view or restarting the service worker preserves the session. The user's Stop ends the current
  run as fibona_quit() would: model calls and fibona_input fail with Quit, the session keeps everything, and the next
  message starts a new run. fibona_app_log notes where it stopped; that run's cells are in fibona_previous_history.
  Force restart, browser shutdown and extension reload discard the session. User messages queue for fibona_input.
`.trim();

let host;  // The FibonaChannel to offscreen.js.
// fibona_browser: Web APIs of the offscreen window. eventArguments: functions that arrive in Chrome events' arguments,
// such as sendResponse, which stay in the service worker.
const browser = new FibonaNativeBridge((message) => host.send({ type: "native", message }));
const eventArguments = new FibonaNativeBridge((message) => host.send({ type: "event-native", message }));

const chromeCall = (path, ...args) => host.request("chrome", { path, args });

// What the app does outside cells, so the agent can see how what it built behaves in the user's hands: prints from
// callbacks and timers, and what views report. Only the newest APP_LOG_LIMIT lines are kept.
// ponytail: a callback's prints during a running cell count as that cell's output.
const APP_LOG_LIMIT = 500;
function logApp(text) {
  const time = new Date().toTimeString().slice(0, 8);
  for (const line of text.replace(/\n$/, "").split("\n")) fibona_app_log.push(`[${time}] ${line}`);
  fibona_app_log.splice(0, Math.max(0, fibona_app_log.length - APP_LOG_LIMIT));
}

// --- STOP ----------------------------------------------------------------------------------------------
// The user's Stop ends the current run, as fibona_quit() would, and keeps the session. JavaScript cannot preempt a
// cell, so the run ends where it waits: model requests and fibona_input reject with core's Quit until it is over.
// A cell that never waits for either keeps running; the Console then offers Force restart.

let run = null;  // The current run: {cell, summary, stopping}.
const stops = new Set();  // Rejecters of the waits that Stop ends.
function stoppable(promise) {
  if (run?.stopping) return Promise.reject(new Quit());
  return new Promise((resolve, reject) => {
    const stop = () => reject(new Quit());
    stops.add(stop);
    promise.then(resolve, reject).finally(() => stops.delete(stop));
  });
}
function stop() {
  if (!run || run.stopping || waiter) return false;  // Not working: nothing to stop.
  run.stopping = true;
  waiter = null;
  for (const reject of stops) reject();
  host.send({ type: "abort-model" });  // ponytail: also aborts model calls a callback makes at that moment.
  sendState("Stopping");
  return true;
}
// running: a run is doing work the user can stop.
function sendState(state) {
  host.send({ type: "state", state, running: Boolean(run && !run.stopping && !waiter) });
}

// The user's messages, oldest first, until input() takes them. They queue while a cell runs. Inside a run the wait
// is stoppable, so a cell that asks for input after Stop ends at once; Stop itself does nothing while it waits.
const inbox = [];
let waiter = null;
function readInput(prompt = "") {
  if (prompt) env.write(prompt.endsWith("\n") ? prompt : prompt + "\n");
  if (inbox.length) return Promise.resolve(inbox.shift());
  const reply = new Promise((resolve) => {
    waiter = resolve;
  });
  sendState("Waiting for input");
  return run ? stoppable(reply) : reply;
}
function receiveInput(text) {
  if (!waiter) return inbox.push(text);
  const resolve = waiter;
  waiter = null;
  sendState("Running");
  resolve(text);
}

// Chrome events the session subscribed to: path -> callbacks. The service worker forwards only those paths.
const subscribers = new Map();
async function on(path, callback) {
  if (typeof callback !== "function") throw new TypeError("fibona_on needs a callback");
  if (!subscribers.has(path)) {
    const callbacks = new Set([callback]);
    subscribers.set(path, callbacks);
    try { await host.request("subscribe", { path }); }
    catch (error) { subscribers.delete(path); throw error; }
  } else subscribers.get(path).add(callback);
  return async () => {
    const callbacks = subscribers.get(path);
    if (!callbacks?.delete(callback) || callbacks.size) return;
    subscribers.delete(path);
    await host.request("unsubscribe", { path });
  };
}
async function deliverEvent(path, args) {
  const values = eventArguments.decode(args);
  for (const callback of subscribers.get(path) ?? []) {
    try {
      await callback(...values);
    } catch (error) {
      const text = `Callback error in ${path}: ${error}\n${error?.stack ?? ""}\n`;
      // Waiting for input is still an active cell: keep errors readable immediately as well as in its output.
      if (env.capture !== null) logApp(text);
      env.print(text);
    }
  }
}

// App requests reach handlers registered by the agent's program.
const uiHandlers = new Set();
async function handleUI({ data, view }) {
  if (!uiHandlers.size) throw new Error("Fibona: no fibona_on_ui handler in the session yet");
  let reply;
  for (const handler of uiHandlers) {
    const value = await handler(data, view);
    if (value !== undefined) reply = value;
  }
  return reply;
}

const env = new JSEnv({
  write: (text) => {
    if (env.capture === null) logApp(text);  // The app's own output, not a cell's.
    host?.send({ type: "write", text });
  },
  readInput,
});
// Model failures are named, so a startup that failed only because the model was unreachable is not blamed on its code.
const request = (body) => stoppable(host.request("model", { body }).catch((error) => {
  error.name = "FibonaModelError";
  throw error;
}));
const mind = new Mind(request, INSTRUCTIONS + "\n\n\n" + CHROME);
const agent = new Agent(mind);
env.bind({
  fibona_app_log: [],
  fibona_source_code: {},
  fibona_browser: browser.root(),
  fibona_chrome: Object.assign(chromeCall, {
    callback: (path, ...args) => host.request("chrome", { path, args, callback: true }),
  }),
  fibona_on: on,
  fibona_on_ui: (handler) => {
    uiHandlers.add(handler);
    return () => uiHandlers.delete(handler);
  },
  fibona_ui_send: (data, viewId) => host.request("ui-send", { data, viewId }),
  fibona_call_llm_api: (input, params) => mind.query(input, params),
});
// Hidden from fibona_variables(): what the host bound, and the names core.js binds for each cell.
env.bind({
  fibona_host_names: new Set([...Object.getOwnPropertyNames(globalThis), "fibona_host_names", "fibona_call_me",
    "fibona_quit", "fibona_history", "fibona_input", "fibona_task"]),
});

// Read by agent/bootstrap.js: the packaged sources, and names its fibona_variables() leaves out.
let SOURCE_CODE = {};
const EXCLUDED_NAMES = new Set(["fibona_task", "fibona_call_me", "fibona_call_llm_api", "fibona_quit",
  "fibona_history", "fibona_input", "fibona_source_code"]);

// One bootstrap cell: fibona_boot when saved, otherwise agent/bootstrap.js. It is read
// for every run, so a run started after the agent saves one uses it. Safe mode runs agent/bootstrap.js instead, and
// starts by itself when a saved startup fails before any cell succeeds (core's recovery cell failing too).
async function main({ sources }) {
  SOURCE_CODE = sources;
  env.bind({ fibona_source_code: sources });
  while (true) {
    const saved = await chromeCall("storage.local.get", ["fibona_boot", "fibona_safe_mode"]);
    const custom = typeof saved.fibona_boot === "string" && !saved.fibona_safe_mode;
    if (saved.fibona_safe_mode) {
      const note = `Safe mode (${saved.fibona_safe_mode.reason}): this run uses agent/bootstrap.js, and views do not ` +
        "run fibona_app. fibona_boot and fibona_app are kept in chrome.storage.local.";
      logApp(note);  // For the agent; the Console shows the user its own notice.
    }
    const results = [];
    await agent.run(custom ? saved.fibona_boot : sources["agent/bootstrap.js"], {
      env,
      onCell: (number, summary, code) => {
        run = { cell: number, summary, stopping: run?.stopping ?? false };
        host.send({ type: "cell", number, summary, code });
        sendState(`Running · cell ${number}`);
      },
      onResult: (number, result) => {
        results.push(result);
        host.send({ type: "result", number, result: { ...result, stopped: Boolean(run?.stopping) } });
      },
    }).finally(() => {
      globalThis.fibona_previous_history = fibona_history;  // The last snapshot core bound; the cell that ended it is not in it.
    });
    const stopped = run?.stopping;
    const last = run;
    run = null;
    if (stopped) {
      logApp(`The user stopped the run at cell ${last.cell} (${last.summary}). Its cells are in fibona_previous_history.`);
      env.write("Stopped. Send a message to continue.\n");
    } else if (custom && results.at(-1)?.error && !results.some((r) => !r.error) &&
               !results.at(-1).error.startsWith("FibonaModelError")) {
      const reason = "the saved startup failed";
      await chromeCall("storage.local.set", { fibona_safe_mode: { reason, error: results[0].error.slice(0, 2000) } });
      env.write(`Fibona's saved startup failed, so the next run starts in safe mode.\n`);
    } else env.write("The run ended. Send a message to start a new one.\n");
    inbox.unshift(await readInput());
  }
}

addEventListener("message", (event) => {
  if (event.source !== parent || event.data?.type !== "fibona_connect" || host) return;
  const port = event.ports[0];
  host = new FibonaChannel((message) => port.postMessage(message), {
    ui: handleUI,
    message: async (message) => {
      if (message.type === "start") main(message).catch((error) => env.write(`Fibona stopped: ${error}\n${error?.stack ?? ""}\n`));
      else if (message.type === "input") receiveInput(message.text);
      else if (message.type === "stop") stop();
      else if (message.type === "event") await deliverEvent(message.path, message.args);
      else if (message.type === "native") await browser.receive(message.message);
      else if (message.type === "event-native") await eventArguments.receive(message.message);
      else if (message.type === "app-log") logApp(message.text);
    },
  });
  port.onmessage = ({ data }) => host.receive(data);
  host.send({ type: "connected" });
});
parent.postMessage({ type: "fibona_ready" }, "*");
