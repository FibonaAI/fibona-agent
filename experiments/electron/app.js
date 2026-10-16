// Fibona on the desktop: the host around core.js, the way app.js is on iOS and fibona.py is in Python.
// main.js runs this file as a script and gives it `native` (dir, log, keychainGet, keychainSet) and a global
// `require`. From them it builds the window, the console, model requests, then runs boot.js and the agent.
// Like core.js it ships read-only with the app, so the agent can never break it.
// One window, like the phone's screen: the agent's app at the bottom, the console over it, and the spiral handle on
// top that switches between them.

const { app, BrowserWindow, Menu, WebContentsView, dialog, ipcMain } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

// Electron's API with signatures and docs, for the agent to search; `npm run package` copies it in.
const ELECTRON_TYPES = ["electron.d.ts", "node_modules/electron/electron.d.ts"].map((f) => path.join(native.dir, f))
  .find((f) => fs.existsSync(f)) ?? null;

const DESKTOP = `
## The desktop

You run inside an Electron app (Electron ${process.versions.electron}, Node ${process.versions.node}, on
${process.platform}), in the main process, on its one thread: a cell that blocks freezes every window. The host is
source_code['app.js'] and the pages' preload is source_code['preload.js']; read them for details.

- \`require\` is global: all of Node and Electron. Look up Electron's API, with signatures and docs, in the file
  \`ELECTRON_TYPES\` instead of guessing.
- Electron is not the limit. Reach the OS out of process with \`child_process\` and the platform's own tools, or in
  process with a Node-API addon you compile, if the machine has a compiler, and load with \`process.dlopen\`; a bad
  call in process crashes the app. Child processes never show UI (\`windowsHide: true\`).
- **Files** persist in \`DATA\`; variables do not outlive the app. \`DATA + "/boot.js"\`, if present, runs as a cell at
  every launch, before you wake: write what you build there to become it. After 3 failed launches in a row it is
  moved to \`boot.failed.js\`. Every cell, across launches, is a line of \`DATA + "/history.jsonl"\`, shaped
  like \`history\`'s records.
- **Your app** goes in \`appView\`, which fills the Fibona window, \`appWindow\`, under the console; not in new windows.
  Hide the console with \`showConsole(false)\`; the user switches back with the floating spiral button. The window has
  no title bar: keep the top 32px clear of controls, for the window's own buttons, and mark a strip there with
  \`-webkit-app-region: drag\` so the user can move the window.
- **Your app in use**: what runs outside cells prints to \`app_log\`, along with uncaught errors; check it first when
  the user reports a problem. To see your app, \`(await appView.webContents.capturePage()).toDataURL()\` goes to
  call_me as \`{type: "input_image", image_url: ...}\`.
`.trim();

// --- WINDOW --------------------------------------------------------------------------------------------

let appWindow, appView, consoleView, handleView;
let consoleShown = true;
const HANDLE = 64;  // The handle's view: a 48 point button with room around it for its shadow.
let handleAt = null;  // Its top left corner in the window, once the user drags it; until then, bottom right.
let dragFrom = null;

function createWindow() {
  // No title bar: the pages run to the top edge, under the window controls, and mark where the window drags.
  appWindow = new BrowserWindow({ width: 900, height: 680, title: "Fibona", backgroundColor: "#20202e",
    titleBarStyle: "hidden", titleBarOverlay: process.platform !== "darwin" });
  const view = (file) => {
    const v = new WebContentsView({ webPreferences: { preload: path.join(native.dir, "preload.js") } });
    v.setBackgroundColor(file === "handle.html" ? "#00000000" : "#20202e");
    appWindow.contentView.addChildView(v);  // Each one over the last.
    if (file) v.webContents.loadFile(path.join(native.dir, file));
    return v;
  };
  appView = view(null);
  consoleView = view("console.html");
  handleView = view("handle.html");
  appWindow.on("resize", layout);
  appWindow.on("closed", () => app.quit());  // Fibona is its window: closing it quits, even if the agent opened others.
  layout();
  return new Promise((resolve) => consoleView.webContents.once("did-finish-load", resolve));
}

function layout() {
  const [width, height] = appWindow.getContentSize();
  for (const v of [appView, consoleView]) v.setBounds({ x: 0, y: 0, width, height });
  moveHandle(...(handleAt ?? [width - HANDLE - 8, height - HANDLE - 72]));
}

function moveHandle(x, y) {
  const [width, height] = appWindow.getContentSize();
  handleAt = [Math.min(Math.max(x, 0), width - HANDLE), Math.min(Math.max(y, 0), height - HANDLE)];
  handleView.setBounds({ x: handleAt[0], y: handleAt[1], width: HANDLE, height: HANDLE });
}

// Switch between console and app; showConsole() toggles. A cell calls it with false once its app is up.
function showConsole(show = !consoleShown) {
  consoleShown = show;
  consoleView.setVisible(show);
  (show ? consoleView : appView).webContents.focus();
}

// The handle: tap to switch, drag to move it, then it settles at the nearest side; hold to reset.
ipcMain.on("handle", (event, action, dx, dy) => {
  if (event.sender !== handleView.webContents) return;
  if (action === "tap") showConsole();
  else if (action === "hold") reset();
  else if (action === "drag") {
    dragFrom ??= handleAt;
    moveHandle(dragFrom[0] + dx, dragFrom[1] + dy);
  } else if (action === "drop") {
    dragFrom = null;
    const [width] = appWindow.getContentSize();
    moveHandle(handleAt[0] + HANDLE / 2 < width / 2 ? 0 : width - HANDLE, handleAt[1]);
  }
});

// --- CONSOLE -------------------------------------------------------------------------------------------

const waiting = [];  // Resolvers of ask() calls, oldest first.
let answer = { secret: false, fallback: null };  // How to treat the next line sent.

const send = (channel, ...args) => consoleView?.webContents.isDestroyed() === false && consoleView.webContents.send(channel, ...args);
const show = (text, kind) => {
  send("write", text, kind);
  native.log(text);  // Mirrored, for whoever started the app from a terminal.
};

// The status line, as on iOS: the state, the time spent in it, and tokens per second while the model streams.
const status = { state: "Ready", started: Date.now(), modelRequests: 0, tokenSamples: [] };  // Samples: [time, tokens]

function setState(state) {
  status.state = state;
  status.started = Date.now();
}

// Ten times a second: the status line, its spinner, and tokens per second over the last 3 seconds.
setInterval(() => {
  const { tokenSamples: samples, started } = status;
  const now = Date.now();
  while (samples.length > 1 && samples[1][0] < now - 3000) samples.shift();
  let speed = 0;
  if (samples.length > 1) {
    const [first, last] = [samples[0], samples.at(-1)];
    speed = (last[1] - first[1]) / Math.max(0.25, (last[0] - first[0]) / 1000);
  }
  const seconds = ((now - started) / 1000).toFixed(1);
  let state = status.state;
  if (status.modelRequests) {
    const streaming = samples.length && samples.at(-1)[0] > started;
    state = `${streaming ? "Generating" : "Thinking"} · ${seconds}s`;
  } else if (state.startsWith("Running")) {
    state += ` · ${seconds}s`;
  }
  const busy = status.modelRequests > 0 || status.state.startsWith("Running");
  send("status", state, `↓ ${speed.toFixed(0)} tok/s`, busy);
}, 100);

// The next line the user sends; `input` in cells. The host's setup can also take a `fallback` for an empty reply, and
// mask a `secret` reply. Not named input: that global is the cells', bound by JSEnv.
function ask(prompt = "", { secret = false, fallback = null } = {}) {
  if (prompt) env.write(prompt.endsWith("\n") ? prompt : prompt + "\n");
  answer = { secret, fallback };
  send("ask", answer);
  setState("Waiting for input");
  return new Promise((resolve) => waiting.push(resolve));
}

ipcMain.on("submit", (event, text) => {
  if (event.sender !== consoleView.webContents || !waiting.length) return;
  if (!text.trim() && answer.fallback !== null) text = answer.fallback;
  show(` › ${answer.secret ? "•".repeat(Math.min(text.length, 12)) : text} \n\n`, "user");
  answer = { secret: false, fallback: null };
  setState("Running");
  waiting.shift()(text);
});

// --- RUNTIME -------------------------------------------------------------------------------------------

const DATA = app.getPath("userData");
const BOOT = path.join(DATA, "boot.js");
const FAILURES = path.join(DATA, "boot-failures");
const HISTORY = path.join(DATA, "history.jsonl");  // Every cell, as history records it, kept across launches.
let runningCell = null;  // The running cell's code and summary, for its line in HISTORY.
const saveCell = (output, error) => fs.appendFileSync(HISTORY, JSON.stringify({ ...runningCell, output, error }) + "\n");

// A cell that is still running when the app quits, usually one waiting in input(), is saved with what it printed.
app.on("will-quit", () => runningCell && saveCell(env.capture?.join("") ?? "", "The app quit while this cell was running."));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => app.quit());

// The console says how much history earlier launches left, without replaying it.
function showHistory() {
  const count = readText(HISTORY)?.trim().split("\n").length ?? 0;
  if (count) show(`${count} cells of history from earlier launches in ${HISTORY}\n`);
}
const readText = (file) => (fs.existsSync(file) ? fs.readFileSync(file, "utf8") : null);

// What the app does outside cells, so the agent can see how what it built behaves in the user's hands. Only the
// newest APP_LOG_LIMIT lines are kept. ponytail: a callback's prints during a running cell count as the cell's.
const app_log = [];
const APP_LOG_LIMIT = 500;
function logApp(text) {
  const time = new Date().toTimeString().slice(0, 8);
  for (const line of text.replace(/\n$/, "").split("\n")) app_log.push(`[${time}] ${line}`);
  app_log.splice(0, Math.max(0, app_log.length - APP_LOG_LIMIT));
}
// An error in a callback is reported, not fatal: the agent repairs it from app_log.
const report = (e) => env.write(`Callback error: ${e}\n${e?.stack ?? ""}\n`);
process.on("uncaughtException", report);
process.on("unhandledRejection", report);

// The model settings, kept in the keychain by setup() at first launch rather than built into the app.
const SETTINGS = "settings";  // Keychain account.
let CONFIG = JSON.parse(native.keychainGet(SETTINGS) ?? "null");  // {baseURL, apiKey, model}

// One Responses API request to CONFIG; the model is CONFIG's unless the body names one. It streams, so a long
// think is not cut off waiting for headers and the status line can tell thinking from generating.
async function request(body) {
  status.modelRequests += 1;
  status.started = Date.now();
  if (!status.tokenSamples.length) status.tokenSamples.push([status.started, 0]);
  try {
    const response = await fetch(CONFIG.baseURL + "/responses", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + CONFIG.apiKey },
      body: JSON.stringify({ model: CONFIG.model, ...body, stream: true }),
    });
    if (!response.ok) throw new Error(`Model request failed: HTTP ${response.status}: ${await response.text()}`);
    const decoder = new TextDecoder();
    let buffer = "";
    for await (const chunk of response.body) {
      buffer += decoder.decode(chunk, { stream: true }).replaceAll("\r", "");
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const event of events) {
        const data = event.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
        if (!data || data === "[DONE]") continue;
        const e = JSON.parse(data);
        if (e.type?.endsWith(".delta") && typeof e.delta === "string") status.tokenSamples.push([Date.now(), status.tokenSamples.at(-1)[1] + Buffer.byteLength(e.delta) / 4]);
        else if (e.type === "response.completed" || e.type === "response.incomplete") {
          const result = e.response;
          result.output_text = result.output.filter((o) => o.type === "message").flatMap((o) => o.content)
            .filter((c) => c.type === "output_text").map((c) => c.text).join("");
          return result;
        }
        else if (e.type === "response.failed") throw new Error(e.response?.error?.message ?? "Model request failed.");
        else if (e.type === "error") throw new Error(e.message ?? JSON.stringify(e));
      }
    }
    throw new Error("Model stream ended without a final response.");
  } finally {
    status.modelRequests -= 1;
  }
}

// --- BOOTSTRAP -----------------------------------------------------------------------------------------

// Read the user's task, set up working state and context helpers, then wake the mind. The helpers live in the
// session, not here, so later cells can replace them; see bootstraps/terminal/context.py. The function's body is
// sent as the bootstrap cell and it is never called directly.
async function bootstrapCell() {
  task = await input();
  instructions = `
Your working instructions, variables() and build_context() are JavaScript state in this session.
Their original implementation is the bootstrap cell in source_code['app.js']; you can replace them in later cells.
source_code also contains the core runtime source. Read relevant excerpts and include them in input when needed.
Use await call_me([...build_context(), {role: "user", content: observations}]) to send these instructions and your selected context.
Keep useful data and task-specific helpers in session variables; update var_docs to describe them.
startup_task preserves the initial task. Maintain task_state before each call_me():
update goal and constraints when the user steers the task, and completed and pending as work progresses.
Record verified outcomes in completed; planned work belongs in pending. build_context() carries all fields on every call.
History summaries record intent; use cell output and errors to determine what actually happened.
`.trim();
  startup_task = task;
  task_state = { goal: task, constraints: [], completed: [], pending: [] };
  source_code = { ...globalThis.source_code, ...SOURCE_CODE };
  RECENT = globalThis.RECENT ?? 10;
  var_docs = {
    ...globalThis.var_docs,
    startup_task: "Original task; preserve when the current goal changes.",
    task_state: "Current goal, constraints, verified completed work and pending work; update before call_me().",
  };

  // Select names and types from the live session; cells may replace this policy.
  variables = () => {
    const found = {};
    for (const name of Object.getOwnPropertyNames(globalThis)) {
      if (name.startsWith("_") || HOST_NAMES.has(name) || EXCLUDED_NAMES.has(name)) continue;
      const value = globalThis[name];
      found[name] = Array.isArray(value) ? `Array[${value.length}]` : typeof value;
    }
    return found;
  };

  // Select context from current session variables and the host's refreshed history; cells may replace this.
  build_context = () => {
    const described = Object.entries(variables()).map(([name, kind]) =>
      `${name}: ${kind}` + (var_docs[name] ? ` (${var_docs[name]})` : ""));
    const lines = [
      "Startup task: " + startup_task,
      "Current goal: " + task_state.goal,
      "Constraints: " + JSON.stringify(task_state.constraints),
      "Completed: " + JSON.stringify(task_state.completed),
      "Pending: " + JSON.stringify(task_state.pending),
      "Data directory: " + DATA,
      "Current task cell: " + history.length,
      "Variables: " + (described.slice(0, 200).join(", ") || "none"),
      "App log outside cells (newest last; all of it in app_log): " + (app_log.slice(-20).join("\n") || "none"),
    ];
    // Older records stay accessible by index; summarize their intent and outcome here.
    const start = Math.max(0, history.length - RECENT);
    history.slice(0, start).forEach((cell, number) => {
      lines.push(`cell ${number}: ${cell.summary} [${cell.error ? "failed: " + cell.error.split("\n")[0] : "ok"}]`);
    });
    const blocks = history.slice(start).map((cell, i) => {
      const number = start + i;
      const parts = [`### cell ${number} · ${cell.summary}`, "code:", cell.code.trim()];
      let output = cell.output.trim();
      if (output.length > 1000) output = output.slice(0, 500) + `\n[Full output: history[${number}].output]\n` + output.slice(-500);
      if (output) parts.push("output:", output);
      if (cell.error) parts.push("error:", cell.error.trim());
      return parts.join("\n");
    });
    const context = [...lines, "Recent task cells:", blocks.join("\n\n") || "none yet"].join("\n");
    return [{ role: "developer", content: instructions }, { role: "user", content: context }];
  };

  await call_me([...build_context(), { role: "user", content: "Start the user's task and submit the next JavaScript cell." }]);
}

const BOOTSTRAP = bootstrapCell.toString().replace(/^[^{]*\{\n/, "").replace(/\}\s*$/, "").replace(/^ {2}/gm, "").trim();
const SOURCE_CODE = Object.fromEntries(["core.js", "app.js", "preload.js"].map((f) => [f, readText(path.join(native.dir, f))]));
const EXCLUDED_NAMES = new Set(["task", "call_me", "call_llm_api", "quit", "history", "input", "source_code"]);

// --- MAIN ----------------------------------------------------------------------------------------------

const env = new JSEnv({
  write: (text) => {
    if (env.capture === null) logApp(text);  // The app's own output, not a cell's.
    show(text);
  },
  readInput: ask,
});
const mind = new Mind(request, INSTRUCTIONS + "\n\n\n" + DESKTOP);
const agent = new Agent(mind);
env.bind({ call_llm_api: (input, params) => mind.query(input, params) });  // boot.js may use it before the agent runs.
const HOST_NAMES = new Set(Object.getOwnPropertyNames(globalThis));  // Hidden from variables().

// Back to the first launch: delete what the agent built and saved, settings included, then quit. Only the user
// starts this, by holding the handle or from the menu.
async function reset() {
  const { response } = await dialog.showMessageBox(appWindow, {
    type: "warning", buttons: ["Cancel", "Reset"], defaultId: 0, cancelId: 0, message: "Reset Fibona?",
    detail: "This deletes everything Fibona built and saved, then quits. Open it again to start fresh.",
  });
  if (response !== 1) return;
  native.keychainSet(SETTINGS, null);
  fs.rmSync(DATA, { recursive: true, force: true });
  app.exit(0);  // exit, not quit: nothing may write to DATA again on the way out.
}

// Run boot.js, counting launches it fails to survive; after 3 in a row, set it aside.
async function boot() {
  const code = readText(BOOT);
  const failures = Number(readText(FAILURES) ?? 0);
  if (!code) return;
  if (failures >= 3) {
    fs.renameSync(BOOT, path.join(DATA, "boot.failed.js"));
    fs.rmSync(FAILURES);
    return env.write(`Safe mode: boot.js failed to start ${failures} times in a row and was moved to boot.failed.js.\n`);
  }
  fs.writeFileSync(FAILURES, String(failures + 1));  // Before it runs, so a crash still counts.
  const result = await env.execute(code);
  logApp(`boot.js ${result.error ? "failed" : "ran"}\n${result.output}${result.error ?? ""}`);
  if (result.error) return env.write(`boot.js failed:\n${result.error}\n`);
  showConsole(false);
  setTimeout(() => fs.rmSync(FAILURES, { force: true }), 10000);
}

// First launch: ask for the model settings in the console, check them with one small request, and keep them in the
// keychain. Asks again until a request works. Reset deletes them.
async function setup() {
  env.write("Fibona needs a model API to think with. It asks once and keeps the answers in this computer's keychain.\n\n");
  while (true) {
    let baseURL = "";
    while (!baseURL) baseURL = (await ask("Model API base URL")).trim();
    const apiKey = (await ask("API key", { secret: true })).trim();
    const model = (await ask("Model", { fallback: "gpt-6-astra" })).trim();
    CONFIG = { baseURL: baseURL.replace(/\/+$/, ""), apiKey, model };
    env.write("Checking…\n");
    try {
      await mind.query("Reply with OK.", { max_output_tokens: 16 });
    } catch (e) {
      env.write(`That did not work: ${e.message}\nLet's try again.\n`);
      continue;
    }
    const failed = native.keychainSet(SETTINGS, JSON.stringify(CONFIG));
    env.write(failed ? "No keychain available, so they last until the app quits.\n" : "Saved.\n");
    return;
  }
}

// The agent reads each task with input(); when a run ends, a new one waits for the next message.
async function main() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: "Fibona", submenu: [
      { label: "Switch Console and App", accelerator: "CmdOrCtrl+Shift+K", click: () => showConsole() },
      { label: "Reset Fibona", click: reset },
      { type: "separator" },
      { role: "quit" },
    ] },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ]));
  await createWindow();
  showHistory();
  if (!CONFIG) await setup();
  await boot();
  while (true) {
    await agent.run(BOOTSTRAP, {
      env,
      onCell: (number, summary, code) => {
        runningCell = { code, summary };
        show(`\n● cell ${number} · ${summary}\n`, "cell");
        setState(`Running · cell ${number}`);
      },
      onResult: (number, result) => {
        if (result.error) show(`${result.error}\n`);
        saveCell(result.output, result.error);
        runningCell = null;
        setState(result.quit ? "Ended" : "Ready");
      },
    });
    env.write("The run ended. Send a message to start a new one.\n");
    setState("Waiting for input");
  }
}

main().catch((e) => env.write(`Fibona stopped: ${e}\n${e?.stack ?? ""}\n`));
