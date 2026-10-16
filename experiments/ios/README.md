# Fibona on iOS (Experimental)

Fibona's cell loop running inside an iPhone app: the agent writes JavaScript cells that run in JavaScriptCore, in the app's own process, and reach UIKit and the rest of the Objective-C runtime through one bridge. To understand Fibona itself, start with the [Fibona Agent repository](https://github.com/FibonaAI/fibona-agent).

Tested on the iOS 26.5 simulator and an iPhone 14 Pro, built with Xcode 26.

## Demo

### Evolving itself into a GR21 on iPhone

https://github.com/user-attachments/assets/cc607d8a-8b91-4880-96ed-1d7715edcde8

## Usage

Requires Xcode and `rsvg-convert` (`brew install librsvg`), which renders the app icon from `public/favicon.svg`. No API key is built into the app: at first launch the console asks for the model API's base URL, key and model, checks them with one request, and keeps them in the phone's keychain.

```bash
make run   # Then type the task in the app.
```

This builds the app, creates and boots a simulator named `Fibona` if needed, and installs and launches the app. Console output appears in this terminal too.

For a connected iPhone, run `make device TEAM=<your-development-team>`.
This uses Xcode to provision, sign, install and launch the app.
Edit `Fibona.xcodeproj` in Xcode to change the project.

In the app, the console is always one tap away on the floating spiral button. Drag it to move it; hold it to reset Fibona to its first launch, deleting everything it built and the model settings. `boot.js` in the app's Documents directory runs at every launch. If `boot.js` fails to start 3 launches in a row, it is moved to `boot.failed.js` and the app starts with only the console. Every cell is appended to `history.jsonl` there, across launches; at launch the console only says how many it holds.

### Limitations

- Running on a device needs signing and a provisioning profile.
- Pointer arguments accept only `null`; blocks support only the signatures in `main.m`.
- Uses the app-delegate lifecycle, which iOS 26 deprecates in favor of scenes.

## Development

| File | Role |
| --- | --- |
| `main.m` | The native shell: a JavaScriptCore context and the `native` bridge (classes, messages, blocks, delegates) |
| `core.js` | The engine, plain JavaScript: `Mind`, `Memory`, `Slot`, `Agent` and `JSEnv`, mirroring `core/agent.py` and `core/env.py` |
| `app.js` | The iPhone host: `ObjC` helpers, the `Console`, timers and HTTP, the bootstrap cell, boot.js and the agent's start |
| `FBWidget.swift` | The widget's Swift-only calls for JavaScript, as `ObjC.FBWidget`: its folder and reload |
| `widget/` | The home-screen widget extension: shows the PNGs the agent draws, by time, with tap regions |
| `Info.plist` | Everything fixed at build time: permission usage strings and network access |
| `Fibona.xcodeproj/project.pbxproj` | Xcode project used by `make device` and `make testflight` |

### How it differs from the Python core

- Cells are bodies of async functions: `await call_me(...)`, `await input()`. Declarations stay in the cell; globals persist.
- The agent runs once for the app's life: the bootstrap cell reads each task with `input()`, as in the terminal bootstrap. When a run ends, a new one waits for the next message.
- A failed repair cell ends the run instead of retrying without limit, so an unreachable model cannot spin the loop.
- The bridge sends any Objective-C message through `NSInvocation`, and builds delegates from `forwardInvocation:`, so no code is generated at run time.
- Everything runs on the main thread; a cell that blocks freezes the app, and a bad pointer crashes it.
