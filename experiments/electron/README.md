# Fibona on the Desktop (Experimental)

Fibona's cell loop running inside an Electron app on macOS, Windows and Linux. The agent writes JavaScript cells that run in Electron's main process, where they reach Node and Electron directly with `require()`. It builds its app inside the Fibona window, under the console, as on the phone. The engine, `core.js`, is the iOS experiment's, unchanged. To understand Fibona itself, start with the [Fibona Agent repository](https://github.com/FibonaAI/fibona-agent).

Tested on macOS with Electron 44.

## Usage

Requires Node.js.

```bash
npm install
npm start          # Then type the task in the console.
npm run package    # Builds dist/Fibona-<platform>-<arch>/ for this computer.
```

At first launch the console asks for the model API's base URL, key and model, checks them with one request, and saves them encrypted with the OS keychain (`safeStorage`).

The window has no title bar: pages run to the top edge, and the console's top strip drags the window. As on iOS, the console is always one click away on the floating spiral button: click it to switch between the console and the app, and drag it to move it. Hold it to reset Fibona to its first launch, which deletes everything it built, its history and the saved settings. The **Fibona** menu does the same (`Cmd/Ctrl+Shift+K` switches). The composer accepts a message only while the agent waits for one. Console output also appears in the terminal that started the app.

Everything lives in the app's user data directory (`app.getPath("userData")`):

| File | Role |
| --- | --- |
| `boot.js` | Written by the agent; runs at every launch, before it wakes. If it fails to start 3 launches in a row, it is moved to `boot.failed.js` and the app starts with only the console |
| `history.jsonl` | Every cell, one line each, shaped like `history`'s records (`code`, `summary`, `output`, `error`), kept across launches with no limit. A cell still running at quit is saved with what it printed. The console shows the newest 200 at launch |
| `settings.bin` | The model settings, encrypted with the OS keychain |

### Limitations

- Everything runs on the main process's one thread: a cell that blocks freezes every window.
- A callback's prints during a running cell count as that cell's output, not `app_log`.
- `history.jsonl` records cells, not the user's messages.

## Development

| File | Role |
| --- | --- |
| `main.js` | The native shell: runs `core.js` and `app.js` as scripts in the main process, and exposes `native` (this directory, the terminal, the keychain) and a global `require` |
| `core.js` | The engine, the same file as on iOS: `Mind`, `Memory`, `Slot`, `Agent` and `JSEnv`, mirroring `core/agent.py` and `core/env.py` |
| `app.js` | The desktop host: the window, the console and its status line, model requests, history, the bootstrap cell, boot.js and the agent's start |
| `console.html`, `handle.html`, `handle.svg` | The console and the spiral button, layered over the agent's app in one window |
| `preload.js` | Every page's IPC to the main process (`fibona.send`, `invoke`, `on`), the agent's app included |

`npm run package` also copies Electron's `electron.d.ts` into the app, where the agent looks up Electron's API instead of guessing.

### How it differs from iOS

- The layout follows iOS, file for file. The difference is the bridge: `main.m` reaches Objective-C through `native`, while here `require` reaches Node and Electron directly, so `main.js` stays small. Keep `core.js` identical to `../ios/core.js`.
- There is no general native bridge. Beyond Electron's API, the agent reaches the OS itself: out of process through `child_process` and the platform's own tools (JXA, PowerShell), which never show UI, or in process through a Node-API addon it compiles and loads with `process.dlopen`, when the machine has a compiler.
- The agent's prompt names only what it cannot find out alone, such as `appView`, `DATA`, `boot.js`, `history.jsonl` and the title bar; for the rest it reads `source_code` and `electron.d.ts`.
