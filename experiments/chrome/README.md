# Fibona Agent in Chrome (Experimental)

[Website](https://fibona.ai)

A singleton JavaScript REPL in the browser background. Fibona writes its own code and interfaces;
a small bridge forwards native browser capabilities. Python and iOS are unchanged.

## Run

Requires Chrome 135 or later. There is no build step, Node.js dependency or local server to start.

1. Use this source directory, or extract the extension ZIP into a permanent folder. In `chrome://extensions`,
   enable **Developer mode**, choose **Load unpacked**, and select the folder containing `manifest.json`.
   Keep that folder in place; Chrome loads the extension from it. The ZIP itself is not loaded directly.
2. In Fibona's extension details, enable **Allow User Scripts** (Chrome 138+). Chrome 135–137 uses
   Developer mode instead. The console shows a settings shortcut until webpage scripts are enabled.
3. Click Fibona and enter a Responses-compatible API base URL, key and model. For example, use
   `https://api.openai.com/v1`; the transport appends `/responses`. Custom and local endpoints are supported.
4. Send a task. **Enter** sends; **Shift+Enter** adds a newline. Sending also starts an idle agent.
   Closing this page leaves the session running.

Model credentials are saved in extension-local storage and
used by the packaged model transport; they are not injected into session bindings. Toolbar, tab, console
and view switch share the existing Fibona logo.

## Give Fibona a task

Open the webpage or project you want to work with, then describe the result in the Console. For example:

- "Look at this project and port its core functionality into an app here."
- "Add a task on the original webpage, mark it complete, and read the page to verify the result."
- "Add a clear-completed button to this app and verify that unfinished tasks are preserved."

Fibona can inspect tabs, operate webpages, and build its own App view. Switch between **Console** and **App**
using the small floating button; drag it if it covers a control. The App starts as an empty workspace until
Fibona installs an interface. Creating helper functions in the background alone does not create an App.
The default working instructions ask Fibona to deliver and verify the actual interface when building an app.

The Console shows the conversation and expandable execution cells. Send follow-up requests there while
keeping the same background session. During execution you can draft a message; send it when Fibona waits
for input. **Start** is optional when sending the first task. **Stop** resets live memory, not just the current step.

You can also type **fibona**, press **Tab**, enter a task and press **Enter** in Chrome's address bar.
By default this opens Fibona and sends the task to the existing agent, or queues it until the model is configured
and started. While a cell is running, browser tasks wait for its next `fibona_input` call.
**Ctrl+Shift+Y** (**Cmd+Shift+Y** on macOS) is the suggested shortcut for `_execute_action`: it invokes
the extension action and follows its configured tab, popup or side panel. It does not emit `commands.onCommand`.
Chrome or another extension may already own the suggested key; check the actual binding in
`chrome://extensions/shortcuts` or `fibona_chrome('commands.getAll')`.

The separate **Custom Fibona shortcut** (`fibona-command`) is initially unbound. Assign a key in
`chrome://extensions/shortcuts`, then ask Fibona to define its behavior through `fibona_on('commands.onCommand',callback)`.
Without a subscriber it opens the normal Console; a subscriber takes over that default behavior.
Save the registration in `fibona_boot` to recreate it after restart. Chrome requires commands to be declared
in the packaged manifest; Fibona can change their handlers, but cannot add commands or assign keys at runtime.

To keep an app after a full browser restart, ask Fibona to save its startup program and application data.
Closing a view preserves the existing session; restoring after a full restart creates a new one from saved code
and explicit state. Model conversation history and arbitrary JavaScript closures are not automatically persisted.

## Permissions and recovery

This experimental extension requests broad permissions so Fibona can choose native browser capabilities at
runtime: tabs and navigation, bookmarks and history, cookies and network access, downloads, clipboard,
notifications, alarms, storage and debugger access. `<all_urls>` enables ordinary page screenshots as well as
network access; `activeTab` grants temporary access when you invoke the extension. File URLs still require
Chrome's **Allow access to file URLs** setting. Restricted pages retain Chrome's native restrictions. These grants allow
real changes to browser data and pages; use a separate Chrome profile when experimenting with unfamiliar tasks.
The model endpoint receives the task and context selected by the running agent. Browser data is not all sent
automatically, but Fibona can read and include relevant page content in its model calls.

If webpage scripts are disabled, use **Open extension settings** in the Console and enable **Allow User Scripts**.
If the App is broken or its switch has been removed, right-click the extension icon and choose
**Open Fibona console**. This separate Console remains available without running custom App code.
If saved startup code fails, the runtime resets and bypasses it for the next manual start.

After changing extension source, use **Reload** on `chrome://extensions` and refresh open Fibona pages.
This discards the live REPL. Save important app data and startup code first. Updated bootstrap defaults apply
to a fresh session; an existing session retains its edited working instructions and context helpers.

## One runtime, multiple views

`background.js` creates one `runtime/offscreen.html`. Its sandbox iframe owns the JS heap and agent. Console tabs,
popups and side panels attach to this same runtime. UI-local DOM is rebuilt on reopen; REPL variables,
history, pending input/model calls and callbacks remain alive. The service worker can suspend/restart
without destroying them. Event listeners are discovered and registered when the worker loads.

**Stop** destroys the whole offscreen document and creates a fresh session, including native browser
objects and callbacks. Owned debugger connections are detached. Explicit browser registrations and
persistent application data remain. Browser shutdown, extension reload/update or a renderer crash loses
live JS memory. Saved startup code and explicit stored state restore a fresh runtime, not arbitrary closures.
The runtime starts at extension install/load and browser startup, or on first attachment. Saved
`chrome.storage.local.fibona_boot` runs once per new runtime when model settings are available.

Timers remain subject to browser throttling/device sleep. Use native alarms where appropriate. Offscreen
reasons used here have no inactivity timeout, but Chrome can still terminate a renderer.
[Offscreen API](https://developer.chrome.com/docs/extensions/reference/api/offscreen),
[service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle).

## Native capability bridge

The host does not maintain capability-specific tools, wrappers or a browser API manual.
Fibona can consult native documentation and build its own helpers in the REPL.

```javascript
// Native Chrome methods and events.
const tabs = await fibona_chrome('tabs.query', {});
const unsubscribe = await fibona_on('tabs.onCreated', tab => fibona_print(tab.id));

// Native Web APIs run under the extension origin, outside the opaque sandbox.
const response = await fibona_browser('fetch', 'https://example.com');
let text;
try { text = await response.call('text'); }
finally { await response.release(); }
const directory = await fibona_browser('navigator.storage.getDirectory');
const file = await directory.call('getFileHandle', 'note.txt', {create: true});
const writer = await file.call('createWritable');
await writer.call('write', text);
await writer.call('close');

// Callbacks are remote references too; native locks retain native scheduling.
await fibona_browser('navigator.locks.request', 'fibona-task', async lock => {
  fibona_print(await lock.get('name'));
});
```

`fibona_browser.get(path)`, `.set(path,value)` and `.construct(path,...args)` access native properties and
constructors. Returned handles have `get`, `set`, `call`, `construct` and `release`; native methods retain
their receiver. Pass handles back as arguments to preserve native object identity. Data, including binary
values, is cloned. Functions become asynchronous callbacks. `release` drops a reference; native resources
still need their own `close`, `removeEventListener`, etc. Stop tears down the entire host document.
After unregistering an exported callback, `fibona_browser.release(callback)` releases its bridge reference.

Direct `fetch` in the REPL and App uses the sandbox's opaque security origin (`Origin: null`) and obeys CORS.
Use `fibona_browser('fetch',url,options)` for cross-origin requests covered by host permissions, then read
and release the returned Response as above. Permissions do not change sandbox fetch. `no-cors` cannot
provide a readable response, and changing the URL to another proxy or archive is not a CORS fix.

This is asynchronous RPC. It cannot manufacture user activation or transparently execute synchronous
callbacks such as IndexedDB upgrade transactions. Use suitable native alternatives (e.g. Chrome storage),
a visible user action, or let the program choose another approach. Local DOM nodes/AbortSignals are not
host objects; construct origin-bound objects through the bridge. Privileged host code obeys MV3 CSP and
cannot eval generated JavaScript. The sandbox can dynamically load libraries under its own CSP/CORS rules.

`fibona_chrome` arguments follow Chrome's JSON contract, without function/Port handles. For native Chrome
events use `fibona_on`; `fibona_on_message` supplies async replies to content/user-script messages.
For APIs whose completion is callback-based, use `fibona_chrome.callback(path,...args)`. It appends a native
callback, rejects `runtime.lastError`, and returns its result; if the callback has no result, it returns the
method's synchronous value. For example, `contextMenus.create` returns its menu id only after creation
succeeds. Promise APIs continue using the ordinary call form.

Chrome event arguments can include remote functions. For example, native address-bar suggestions work
through the same bridge; invoke the callback and release its reference after use:

```javascript
await fibona_on('omnibox.onInputChanged', async (text, suggest) => {
  try { await suggest([{content: text, description: 'Ask Fibona: ' + text}]); }
  finally { await suggest.release(); }
});
```

Worker event listeners are registered synchronously at startup, so waking events are not lost after worker
suspension. `fibona_on` accepts a path and optional callback; filter event data inside that callback.
Native registration filters/options and synchronous blocking return values are not supported by this RPC.
Use CDP for extra network data such as POST bodies and detach afterward; view-owned native listeners
can also be registered through `fibona_view` and end when that view closes. Remote event callback references
belong to the current worker and must not be saved for later sessions.
Each subscription receives its own callback references; releasing one does not invalidate another subscriber.

In the background REPL, `await fibona_on('contextMenus.onClicked')` without a callback stores events in
`fibona_events` and queues a message pointing to the corresponding index. The active program receives it
through `fibona_input` and decides whether to call the model. It does not interrupt a running cell.
Keep subscriptions in saved `fibona_boot` code if they should be recreated after a full restart.

Generated webpage code uses the native User Scripts API, without a debugger connection or banner:

```javascript
const [{result}] = await fibona_chrome('userScripts.execute', {
  target: {tabId},
  js: [{code: "document.querySelector('h1').textContent = 'Changed by Fibona'"}],
});
```

The default `USER_SCRIPT` world shares the DOM and isolates script globals. Use `world:'MAIN'` when code
needs the page's own JavaScript globals. Async script results are awaited. Configure native user-script
messaging with `userScripts.configureWorld({messaging:true})` when needed.

User-script execution requires Chrome's **Allow User Scripts** toggle (138+, Developer mode on 135–137).
Manifest permissions cannot switch it on. Disabled calls report the exact settings URL and action, and can
be retried after enabling it. `scripting.executeScript` cannot accept a function string through this JSON
bridge; `files` only loads packaged files. Adding a file does not turn it into a general CSP-free evaluator.
Native permissions, restricted browser pages and consent/gesture requirements still apply.
`fibona_environment` contains actual grants, discovered event paths, the session id and `userScripts`
availability/settings URL, refreshed before every run.
It also includes entrypoint paths, registered commands and the omnibox keyword.

## Programmable views

The REPL's `document` is hidden. `fibona_ui(codeString)` installs ordinary JavaScript in each visible view.
It owns the whole view DOM/CSS/Canvas and can load libraries; there is no fixed interface schema.

```javascript
fibona_count = 0;
fibona_on_ui(data => ({count: data.increment ? ++fibona_count : fibona_count}));
await fibona_ui(`
  document.body.innerHTML = '<button id="counter">Count</button>';
  const render = async increment => {
    const state = await fibona_send({increment});
    document.getElementById('counter').textContent = String(state.count);
  };
  document.getElementById('counter').onclick = () => render(true);
  await render(false);
`);
await fibona_show_console(false);
```

`fibona_ui_send(data,viewId?)` pushes data to view-side `fibona_on_data(callback)` handlers.
View code should send a ready message to obtain current background state after mounting.
`fibona_ui_context` describes id, URL and surface. `index.html?surface=popup` and `?surface=sidepanel` use
the same runtime. The default switch shows current **Console/App** and **Open app/Open console**.
Drag it with a mouse or touch to move it; a short click still switches views. Its position stays within
the window, including after resizing. Movement is local to the open page.
`fibona_set_title` sets view titles. Default console input continues waiting when no view is open.

Fibona can configure `action.setPopup`, open a popup window with `windows.create`, create right-click
menus, or bind the toolbar to the side panel with `sidePanel.setPanelBehavior`. An action popup replaces
`action.onClicked`; user-gesture requirements still apply. Prefer native bindings for gesture-dependent UI.
Subscribe to `omnibox.onInputEntered` or `commands.onCommand` to replace their default entry behavior.

View-side `fibona_view` reuses the native object bridge to access the outer page's window/DOM. Fibona can
change the default switch's style, replace its content or remove it with ordinary DOM operations:

```javascript
// Inside the code passed to fibona_ui(...).
const button = await fibona_view('document.getElementById', 'recovery');
await button.set('style.background', '#333333');
await button.call('remove');
```

These edits affect the current page. Put them in the UI program to apply again on reopen. The outer page
contains the console and model settings too, so avoid reading credentials or putting them in model context.

Keep native event handling in code. An active cell may await an event and then call `fibona_call_me`;
a completed cell's saved callback cannot schedule another cell. Program variables, context helpers and
I/O handlers remain editable. Inspect their defining cells and generated App code through `fibona_history`;
the completed bootstrap cell is recorded there too. Later definitions can supersede earlier ones, so inspect
live variables for current values and use new cells to change behavior. Editing history records does not execute code.
`fibona_source_code` provides the complete packaged extension source, including core, bridges, runtime,
console, HTML/CSS, manifest and the original bootstrap template, plus reference documentation.
Use it to understand how the whole extension is implemented. It does not track later changes to the running
program; inspect those through cell history and live variables. Editing packaged source snapshots does not
change the installed extension or manifest.

Save startup code under `fibona_boot`, and explicit state in native storage/files. Right-click Fibona and
choose **Open Fibona console**, or use `index.html?safe=1`, to open a separate console. It never loads
custom App code, has no floating switch, and stays visible even when the REPL hides normal consoles.
On a fresh runtime this suppresses automatic boot; on an existing one it attaches without replacing live state. Startup
exceptions reset the runtime and bypass boot for the next manual start.

## Files and checks

| Location | Responsibility |
| --- | --- |
| `agent/` | Mind/Memory/Agent/JSEnv, fixed core instructions and editable bootstrap |
| `runtime/` | Persistent session, sandbox endpoint and generic browser bridges |
| `console/` | Protected console and programmable view connection |
| `background.js` | Extension lifecycle, Chrome method calls and event forwarding |
| `index.html`, `manifest.json`, `icons/` | Extension entry points and shared logo |

The console shows agent steps, progress, messages and errors. Each step keeps its complete code behind
**Code**, including a cell that quits. The runtime status and **Stop** sit immediately above the message
input so they remain visible while you read the conversation. Body text is 16px, code and secondary text are 14px.
Model transport details and native bridge traffic are not part of this view.

The prompt follows the original Fibona design. `agent/core.js` defines the fixed self, execution protocol
and browser host facts. `agent/bootstrap.js` supplies editable working instructions, browser workflow,
task state and context helpers. These working instructions enter each wake through `fibona_build_context()`;
changing them does not replace the core. Edits to `fibona_instructions`, `fibona_variables` and
`fibona_build_context` survive subsequent runs in the same REPL; save startup code to restore them after reset.

Send a message to start an idle agent or continue after a run ends, reusing the same REPL. During a run,
you can draft a message and send when the agent requests input. **Start** opens a run without an initial
message; **Stop** resets the runtime. Saved startup restoration is handled by the runtime, with recovery
bypassing it automatically. Production scripts are packaged; store distribution is unreviewed.

The runtime files are loaded directly by Chrome; there is no bundling step. A distributable development ZIP
contains `manifest.json`, `background.js`, `index.html`, `agent/`, `runtime/`, `console/`, `icons/`, this README
and the project license. Keep development tooling, test code, local profiles and credentials out of the ZIP.
This is an unpacked experimental extension; no Chrome Web Store listing or signed CRX is provided.
