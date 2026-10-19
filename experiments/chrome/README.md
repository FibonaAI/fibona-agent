# Fibona Agent in Chrome (Experimental)

[Website](https://fibona.ai)

One JavaScript session shared by Fibona tabs, popups and side panels. The agent writes its own program and App;
small bridges provide Chrome APIs and native Web APIs. The Console is packaged code the agent does not replace.

## Run

Requires Chrome 135 or later. No build step or local server is needed.

1. In `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select this directory.
2. Open Fibona, expand model settings, and save a Responses API base URL, API key and model. The transport appends
   `/responses` to the URL. Local endpoints work too.
3. Send a task in the Console. Closing the view leaves the background session alive.

For generated webpage scripts, enable **Allow User Scripts** in the extension's details (Chrome 138+;
Chrome 135–137 uses Developer mode). The error from a disabled `userScripts` call includes the settings URL.

The Console shows conversation output and expandable execution cells, with status and Stop beside the composer.
Tabs initially show Console; popup and side-panel surfaces initially show App. `?mode=console` opens Console.
Normal pages have a draggable Console/App switch outside the App frame, so replacing the App keeps it; it is ordinary
DOM the agent can restyle or remove through `fibona_view`. Safe mode has no switch.

## When something goes wrong

Fibona repairs its own code, so the user needs only three controls, each for what Fibona cannot fix by itself:

| Control | Where | What it does |
| --- | --- | --- |
| **Stop** (Esc) | Beside the composer | Ends the current task. Fibona keeps its memory (variables, App, handlers); the next message starts a new run. If the task has not stopped after 5 seconds, the button becomes **Force restart**, which discards the session. |
| **Safe mode** | Troubleshooting, or right-click the toolbar icon → **Open Fibona in safe mode**, or `index.html?safe=1` | Restarts Fibona as it was first installed: the default startup and the default App. What Fibona built stays in storage, so ask it to fix that, then **Exit safe mode**. Nothing is deleted. |
| **Clear all data** | Troubleshooting | Deletes everything Fibona built and saved and returns to a fresh install. Model settings are kept. |

Stop ends the run where it waits: in-flight model requests are aborted and `fibona_call_me`, `fibona_call_llm_api` and
`fibona_input` reject with core's Quit. A cell stuck in synchronous code cannot be interrupted in JavaScript, which is
what Force restart is for. Force restart, safe mode and clearing detach debugger targets tracked by this extension.

Safe mode also starts by itself when a saved startup fails before any of its cells succeed, unless the failure was the
model request. For maintenance it separates the two kinds of bug: what reproduces in safe mode is in the packaged code;
what does not is in what Fibona built. The toolbar menu entries are recreated whenever the service worker starts, and
the `?safe=1` link works however the agent changes the toolbar.

Clearing removes `chrome.storage` (local, session, sync), the extension origin's localStorage, IndexedDB, Cache Storage
and origin file system, and the browser registrations Fibona can make (user scripts, content scripts, alarms, dynamic
and session network rules, context menus, toolbar popup, badge, title and side-panel behavior), while no session is
running; a fresh session then starts. Removing the extension removes everything, model settings included.

Model settings live in extension-local storage. The model transport reads them; it does not inject the key into
session bindings. The agent has storage access, so it must still avoid reading or exposing credentials unnecessarily.

## Startup and persistence

There are two independent startup programs:

| Program | Default | Override in `chrome.storage.local` |
| --- | --- | --- |
| Agent | `agent/bootstrap.js` | `fibona_boot` string |
| App in each view | `app/bootstrap.js` | `fibona_app` string |

An override replaces the entire default program. Removing its key restores the default. The agent startup is the
bootstrap cell of each run, read when the run starts. The default waits for the user's task, sets up the working
instructions and context helpers, and wakes the model; while it waits, the Console marks the cell "waiting for your
message". A failing startup cell goes to core's recovery cell; if
that fails too, the next run starts in safe mode. The App runs in each view before the session or model is ready and
reloads when its key changes; an empty string renders nothing.

The toolbar button, the shortcut and the `fibona` omnibox keyword open Fibona by default. When the session subscribes
to `action.onClicked`, `commands.onCommand` or `omnibox.onInputEntered`, its callback replaces that default.

The host does not persist execution history, variables, event handlers or application data. The agent chooses
what to save in Chrome storage or the extension origin's file system, and how its startup program restores it.
`fibona_history` contains the current run's finished cells and `fibona_previous_history` the last run's;
`fibona_app_log` contains recent App and callback diagnostics.
Console logs and session globals remain in memory while the offscreen document lives.

Force restart, browser shutdown, extension reload and renderer termination discard live memory. A service-worker
restart does not discard the offscreen session. Timers remain subject to browser throttling.

## Native APIs

```javascript
const tabs = await fibona_chrome('tabs.query', {});
const unsubscribe = await fibona_on('tabs.onCreated', tab => fibona_print(tab.id));

const response = await fibona_browser('fetch', 'https://example.com');
const text = await response.call('text');
await response.release();

const root = await fibona_browser('navigator.storage.getDirectory');
const file = await root.call('getFileHandle', 'note.txt', {create: true});
const writer = await file.call('createWritable');
await writer.call('write', text);
await writer.call('close');
```

`fibona_chrome(path, ...args)` invokes a Chrome method in the service worker. Arguments are copied data, not functions.
`.callback(path, ...args)` appends a callback, checks `runtime.lastError` and resolves to its callback result.
`fibona_on(eventPath, callback)` returns an async unsubscribe. Function arguments such as `sendResponse` cross as async
references, so a message listener can call them and release them afterward.

`fibona_browser` invokes Web APIs in the offscreen document under the extension origin. Its `get`, `set` and `construct`
operations access properties and constructors. Returned native objects support `get`, `set`, `call`, `construct` and
`release`. Functions cross as asynchronous callbacks; native methods retain their receiver. Passing a reference back
preserves the native object's identity. Data is copied, including binary values. Release bridge references when finished;
resources may also need their own `close` or `removeEventListener` calls.

This is asynchronous RPC: it does not provide user activation or synchronous callbacks such as IndexedDB upgrades.
Use native alternatives or an actual visible user action where required. The sandbox can eval generated cells; privileged
extension pages obey MV3 CSP and cannot eval generated code. Native permissions and restricted pages still apply.

Generated webpage code can use `userScripts.execute` without a debugger banner:

```javascript
const results = await fibona_chrome('userScripts.execute', {
  target: {tabId},
  js: [{code: "document.querySelector('h1').textContent"}],
});
```

## App programs

Register session handlers before installing an App that depends on them:

```javascript
fibona_count = 0;
fibona_on_ui(data => ({count: data.increment ? ++fibona_count : fibona_count}));
await fibona_chrome('storage.local.set', {fibona_app: `
  document.body.innerHTML = '<button id="counter">Count</button>';
  const render = async increment => {
    const state = await fibona_send({increment});
    document.getElementById('counter').textContent = String(state.count);
  };
  document.getElementById('counter').onclick = () => render(true);
  await render(false);
`});
```

App programs run in their own sandboxed iframe with DOM access. `fibona_send(data)` calls the session's `fibona_on_ui`
handlers; `fibona_ui_send(data, viewId?)` sends data back to App `fibona_on_data(callback)` handlers. `fibona_ui_context`
describes the current view. Apps render independently and should handle unavailable session handlers themselves.

App `fibona_show_console(visible)` switches the current view. `fibona_chrome` reaches Chrome methods;
`fibona_view` provides the same native-object bridge to the outer extension page, including its DOM. Put outer-page
changes in App startup to repeat them on reopen. The outer page includes Console settings; do not expose credentials.
Safe mode loads no App and is unaffected by these edits. To customize the switch:

```javascript
// In the App program. Use ordinary native DOM methods to restyle it or remove it deliberately.
const toggle = await fibona_view('document.getElementById', 'view-switch');
if (toggle) await toggle.set('style.borderRadius', '12px');
```

A removed switch returns when the page reopens, and the saved App can reapply its changes.

## Source layout

- `agent/core.js`: platform-independent Mind, Memory, Slot, Agent and JSEnv, prefixed `fibona_` to avoid browser
  globals. No App or persistence policy lives here.
- `agent/bootstrap.js`: the default agent startup: working instructions, `fibona_variables()` and
  `fibona_build_context()`, all editable session state.
  `app/bootstrap.js` supplies the editable default visible App.
- `runtime/session.js`: the Chrome prompt, session bindings, Stop and startup (`fibona_boot` or the default bootstrap,
  safe mode). `fibona_source_code` exposes packaged text sources and the manifest, distinct from live cells and storage
  overrides.
- `runtime/offscreen.js`: model transport, native Web API bridge and shared in-memory Console log.
- `background.js`: offscreen lifecycle and restarts, clearing, generic Chrome calls, event forwarding across worker
  restarts and the default actions of the toolbar button, shortcut and omnibox. `webRequest.*` listeners are added only while subscribed, so
  ordinary browsing does not wake the worker.
- `runtime/channel.js`, `native-bridge.js`, `browser-wire.js`: request/reply and value transport.
- `console/`, `app/frame.*`, `index.html`: protected Console and independent App frame.

This experimental extension requests broad browser permissions so the agent can operate pages and browser data.
The model receives the context selected by the running program, rather than an automatic dump of browser state.

[Offscreen API](https://developer.chrome.com/docs/extensions/reference/api/offscreen) ·
[Service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
