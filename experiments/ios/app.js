// Fibona on iOS: the host around core.js, the way fibona.py and bootstraps/terminal are around core/.
// main.m gives this file one object, `native` (cls, send, block, delegate, log). From it this file builds
// Objective-C access, the console, timers and HTTP, then runs boot.js and the agent.
// Like core.js it ships read-only in the app bundle, so the agent can never break it.

const IPHONE = `
## The iPhone

You run inside an iPhone app, in JavaScriptCore, on the main thread. Besides standard JavaScript there are
\`setTimeout\`/\`clearTimeout\` and \`await http(url, {method, headers, body})\`, resolving to \`{status, text}\`.
The host is source_code['app.js']; read it for details.

- **Files** persist in \`DOCS\` (Documents); variables do not outlive the app. \`DOCS + "/boot.js"\`, if present,
  runs as a cell at every launch, before you wake: write what you build there to become it. After 3 failed
  launches in a row it is moved to \`boot.failed.js\`.
- **Objective-C**: \`ObjC.UILabel\` is a class; a method call sends its selector with \`_\` for each \`:\`, as in
  \`ObjC.UILabel.alloc().initWithFrame_([0, 0, 200, 40])\`. Strings, numbers, booleans, arrays and plain objects
  convert both ways and come back as plain JS values; call methods on them with
  \`ObjC.send(value, "selector:", ...args)\`. Structs are number arrays (a CGRect is \`[x, y, width, height]\`),
  pointer arguments accept only \`null\`, and constants are their string values ("NSFont").
- \`ObjC.block(types, fn)\` makes a completion block: "v", "v@", "v@@", "v@@@", "vB" or "vB@".
  \`ObjC.delegate(handlers, protocols)\` makes a delegate or target from functions named like selectors
  (\`tableView_numberOfRowsInSection_\`); name its protocols so argument and return types are known.
- UIKit holds delegates and targets weakly: keep them in globals. An invalid call throws; a wrong pointer or a
  freed object crashes the app, and this session with it.
- Build interfaces with UIKit (SwiftUI is not reachable) in your own UIWindow, then hide the console with
  \`consoleWindow.setHidden_(true)\`. The user can reopen it with the floating spiral button.
- **Permissions and capabilities** are fixed at build time. Info.plist
  (\`ObjC.NSBundle.mainBundle().infoDictionary()\`) lists the usage strings and background modes; on a device, the
  bundle's embedded.mobileprovision lists the entitlements. Request authorization before use. Files cannot be read
  while the phone is locked.
- **Your app in use**: what runs outside cells (callbacks, timers, boot.js) prints to \`app_log\`, along with
  callback errors; build_context() shows the newest lines. Check it first when the user reports a problem.
- **Using your app**: call its functions and read its state directly, or act as the user: walk the view tree,
  type with \`setText_\`, tap with \`sendActionsForControlEvents_(64)\`. Taps skip hit-testing, so also look:
  \`screenshot()\` returns a PNG data URL to send to call_me as \`{type: "input_image", image_url: ...}\`.
`.trim();

// --- OBJC ----------------------------------------------------------------------------------------------
// Objective-C objects arrive as opaque wrappers; `wrap` gives them methods, so label.setText_("hi") sends
// setText:. Converted values (strings, numbers, arrays, plain objects) pass through as they are.

const RAW = Symbol("raw");
const isPlain = (v) => v !== null && typeof v === "object" && Object.getPrototypeOf(v) === Object.prototype;
const isObjC = (v) => typeof v === "function" || (v !== null && typeof v === "object" && !Array.isArray(v) && !isPlain(v));
const mapValues = (o, fn) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, fn(v)]));

function wrap(v) {
  if (Array.isArray(v)) return v.map(wrap);
  if (isPlain(v)) return mapValues(v, wrap);
  if (!isObjC(v)) return v;
  return new Proxy(v, {
    get(target, key) {
      if (key === RAW) return target;
      if (typeof key === "symbol" || key === "then") return undefined;  // Not a thenable, so it can be awaited.
      const selector = ["toString", "toJSON", "valueOf"].includes(key) ? "description" : key.replaceAll("_", ":");
      return (...args) => wrap(native.send(target, selector, args.map(unwrap)));
    },
  });
}

function unwrap(v) {
  if (v?.[RAW]) return v[RAW];
  if (Array.isArray(v)) return v.map(unwrap);
  if (isPlain(v)) return mapValues(v, unwrap);
  return v;
}

// A callback's errors go to the console and app_log instead of vanishing into native code.
// While it runs, prints also go to app_log. ponytail: an async callback's prints after its first await are not counted.
let callbackDepth = 0;
const asCallback = (fn) => {
  callbackDepth += 1;
  try {
    return fn();
  } finally {
    callbackDepth -= 1;
  }
};
const guard = (fn) => (...args) => {
  const report = (e) => asCallback(() => print(`Callback error: ${e}\n${e?.stack ?? ""}`));
  try {
    const result = asCallback(() => fn(...args.map(wrap)));
    if (typeof result?.then === "function") result.then(null, report);  // An async callback fails later.
    return unwrap(result);
  } catch (e) {
    report(e);
  }
};

globalThis.ObjC = new Proxy({
  send: (target, selector, ...args) => wrap(native.send(unwrap(target), selector, args.map(unwrap))),
  block: (types, fn) => wrap(native.block(types, guard(fn))),
  delegate: (handlers, protocols = []) =>
    wrap(native.delegate(Object.setPrototypeOf(mapValues(handlers, guard), null), protocols)),
}, {
  get: (helpers, name) => (typeof name === "symbol" || name === "then" ? undefined : helpers[name] ?? wrap(native.cls(name))),
});

// --- CONSOLE -------------------------------------------------------------------------------------------
// Laid out and colored like bootstraps/terminal: the conversation, the composer, then a status line that keeps
// moving while the agent works (state, elapsed time, and tokens per second while the model streams).

const rgb = (hex) => ObjC.UIColor.colorWithRed_green_blue_alpha_(
  ...[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255), 1);
const COLORS = mapValues({ background: "#20202e", text: "#d6d6dd", composer: "#41414e", status: "#a4cea2",
  user: "#f0a6ca", cell: "#ba94ed", logo: "#78628f", notice: "#9494a4", error: "#f18c8c", speed: "#e5c07b" }, rgb);
const COMPOSER_MAX = 124;  // About five lines.
const mono = (size, weight = 0) => ObjC.UIFont.monospacedSystemFontOfSize_weight_(size, weight);
const LOGO = `███████╗██╗██████╗  ██████╗ ███╗   ██╗ █████╗
██╔════╝██║██╔══██╗██╔═══██╗████╗  ██║██╔══██╗
█████╗  ██║██████╔╝██║   ██║██╔██╗ ██║███████║
██╔══╝  ██║██╔══██╗██║   ██║██║╚██╗██║██╔══██║
██║     ██║██████╔╝╚██████╔╝██║ ╚████║██║  ██║
╚═╝     ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝`;

class Console {
  // The app's own windows: the conversation and composer above all others, and a floating button that toggles
  // them. The user's way in, even when the agent's windows are broken. As in bootstraps/terminal, a line can be sent
  // only while a cell waits in input(); otherwise Return keeps the text in the composer.
  // Delegates, targets and the timer are kept as fields, because UIKit holds the first two weakly.
  waiting = [];  // input() calls not yet answered
  secret = false;  // The pending input is shown masked once sent.
  fallback = null;  // What an empty reply to the pending input means; null when a reply must not be empty.
  welcome = true;
  state = "Ready";
  started = Date.now();
  modelRequests = 0;
  tokenSamples = [];  // [time, total tokens]

  constructor() {
    const screen = ObjC.UIScreen.mainScreen().bounds();
    this.window = ObjC.UIWindow.alloc().initWithFrame_(screen);
    this.window.setWindowLevel_(2001);  // Above alerts (2000).
    this.window.setOverrideUserInterfaceStyle_(2);  // Dark.
    this.window.setRootViewController_(ObjC.UIViewController.new());
    const view = this.window.rootViewController().view();
    view.setBackgroundColor_(COLORS.background);

    this.log = ObjC.UITextView.new();
    this.log.setEditable_(false);
    this.log.setBackgroundColor_(COLORS.background);
    this.log.setTextContainerInset_([8, 4, 8, 4]);

    // The composer wraps and grows with its text up to COMPOSER_MAX points, then scrolls. Return sends.
    this.field = ObjC.UITextView.new();
    this.field.setBackgroundColor_(COLORS.composer);
    this.field.setTextColor_(COLORS.text);
    this.field.setTintColor_(COLORS.cell);
    this.field.setFont_(mono(15));
    this.field.layer().setCornerRadius_(10);
    this.field.setTextContainerInset_([12, 28, 12, 12]);  // Room for the › prompt on the left.
    this.field.setReturnKeyType_(7);  // Send.
    this.field.setScrollEnabled_(false);
    const prompt = ObjC.UILabel.alloc().initWithFrame_([14, 12, 14, 20]);
    prompt.setAttributedText_(this.styled("›", "notice", 15));
    this.placeholder = ObjC.UILabel.alloc().initWithFrame_([33, 12, 300, 20]);  // UITextView has none of its own.
    this.placeholder.setAutoresizingMask_(2);  // Flexible width.
    for (const v of [prompt, this.placeholder]) this.field.addSubview_(v);
    this.setState(this.state);  // Sets the placeholder.

    this.spinner = ObjC.UIActivityIndicatorView.alloc().initWithActivityIndicatorStyle_(100);  // Medium.
    this.spinner.setColor_(COLORS.status);
    this.spinner.setHidesWhenStopped_(true);
    this.spinner.setContentCompressionResistancePriority_forAxis_(1000, 0);
    this.statusLabel = ObjC.UILabel.new();
    this.statusLabel.setLineBreakMode_(4);  // Truncate the tail.
    this.statusLabel.setContentHuggingPriority_forAxis_(1, 0);
    this.speedLabel = ObjC.UILabel.new();
    this.speedLabel.setContentCompressionResistancePriority_forAxis_(1000, 0);
    const status = ObjC.UIStackView.alloc().initWithArrangedSubviews_([this.spinner, this.statusLabel, this.speedLabel]);
    status.setSpacing_(6);

    for (const v of [this.log, this.field, status]) {
      v.setTranslatesAutoresizingMaskIntoConstraints_(false);
      view.addSubview_(v);
    }
    const safe = view.safeAreaLayoutGuide();
    ObjC.NSLayoutConstraint.activateConstraints_([
      this.log.topAnchor().constraintEqualToAnchor_(safe.topAnchor()),
      this.log.leadingAnchor().constraintEqualToAnchor_constant_(safe.leadingAnchor(), 12),
      this.log.trailingAnchor().constraintEqualToAnchor_constant_(safe.trailingAnchor(), -12),
      this.field.topAnchor().constraintEqualToAnchor_constant_(this.log.bottomAnchor(), 8),
      this.field.leadingAnchor().constraintEqualToAnchor_(this.log.leadingAnchor()),
      this.field.trailingAnchor().constraintEqualToAnchor_(this.log.trailingAnchor()),
      (this.fieldHeight = this.field.heightAnchor().constraintEqualToConstant_(44)),
      status.topAnchor().constraintEqualToAnchor_constant_(this.field.bottomAnchor(), 8),
      status.leadingAnchor().constraintEqualToAnchor_constant_(this.log.leadingAnchor(), 4),
      status.trailingAnchor().constraintEqualToAnchor_constant_(this.log.trailingAnchor(), -4),
      status.bottomAnchor().constraintEqualToAnchor_constant_(view.keyboardLayoutGuide().topAnchor(), -8),
    ]);
    this.fieldDelegate = ObjC.delegate({
      // Return sends, and only while a cell waits in input(); otherwise the text stays.
      textView_shouldChangeTextInRange_replacementText_: (field, range, text) => {
        if (text !== "\n") return true;
        const message = field.text();
        if ((message.trim() || this.fallback !== null) && this.waiting.length) {
          field.setText_("");
          this.fit();
          this.submit(message);
        }
        return false;
      },
      textViewDidChange_: () => this.fit(),
    }, ["UITextViewDelegate"]);
    this.field.setDelegate_(this.fieldDelegate);
    this.showWelcome();
    this.window.makeKeyAndVisible();

    // A 48-point button in a 64-point window, so its shadow has room to show.
    // Starts above the tallest composer, so it never covers what the user types.
    this.handleWindow = ObjC.UIWindow.alloc().initWithFrame_([screen[2] - 72, screen[3] - 300, 64, 64]);
    this.handleWindow.setWindowLevel_(2002);
    this.handleWindow.setRootViewController_(ObjC.UIViewController.new());
    const handle = ObjC.UIButton.buttonWithType_(0);  // Custom: the image keeps its own colors.
    handle.setFrame_([8, 8, 48, 48]);
    handle.setImage_forState_(ObjC.UIImage.imageNamed_("Handle"), 0);  // The app icon's spiral, from Assets.xcassets.
    handle.setAccessibilityLabel_("Fibona console");
    handle.imageView().layer().setCornerRadius_(24);  // Round the image, not the button, so the shadow is not clipped.
    handle.imageView().setClipsToBounds_(true);
    handle.layer().setShadowOpacity_(0.35);  // The default shadow color is black.
    handle.layer().setShadowRadius_(6);
    handle.layer().setShadowOffset_([0, 3]);
    // Tap to toggle the console, drag to move, hold to reset.
    this.handleTarget = ObjC.delegate({
      toggle: () => (this.window.isHidden() ? this.show() : this.hide()),
      drag_: (gesture) => this.drag(gesture),
      hold_: (gesture) => gesture.state() === 1 && this.confirmReset(),  // Began.
    });
    handle.addTarget_action_forControlEvents_(this.handleTarget, "toggle", 64);  // Touch up inside.
    handle.addGestureRecognizer_(ObjC.UIPanGestureRecognizer.alloc().initWithTarget_action_(this.handleTarget, "drag:"));
    handle.addGestureRecognizer_(ObjC.UILongPressGestureRecognizer.alloc().initWithTarget_action_(this.handleTarget, "hold:"));
    this.handleWindow.rootViewController().view().addSubview_(handle);
    this.handleWindow.setHidden_(false);

    this.timer = ObjC.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, true, ObjC.block("v@", () => this.refresh()));
  }

  styled(text, color = "text", size = 13, extra = {}) {
    return ObjC.NSAttributedString.alloc().initWithString_attributes_(text,
      { NSFont: mono(size), NSColor: COLORS[color], ...extra });
  }

  // Append to the conversation, following the end unless the user has scrolled up to read.
  append(text, color = "text", extra = {}) {
    const storage = this.log.textStorage();
    if (this.welcome) {
      this.welcome = false;
      storage.deleteCharactersInRange_([0, storage.length()]);
    }
    const [, y] = this.log.contentOffset();
    const following = y + this.log.bounds()[3] >= this.log.contentSize()[1] - 40;
    storage.appendAttributedString_(this.styled(text, color, 13, extra));
    native.log(text);
    if (following) this.log.scrollRangeToVisible_([storage.length(), 0]);
  }

  showWelcome() {
    const center = ObjC.NSMutableParagraphStyle.new();
    center.setAlignment_(1);
    const text = ObjC.NSMutableAttributedString.new();
    text.appendAttributedString_(this.styled("\n\n\n\n" + LOGO + "\n\n", "logo", 7.5, { NSParagraphStyle: center }));
    text.appendAttributedString_(this.styled("Enter a task to begin.", "notice", 13, { NSParagraphStyle: center }));
    this.log.setAttributedText_(text);
  }

  // Cell output, indented under its cell; `print` in cells.
  write(text) {
    const indent = ObjC.NSMutableParagraphStyle.new();
    indent.setFirstLineHeadIndent_(18);
    indent.setHeadIndent_(18);
    this.append(text, "text", { NSParagraphStyle: indent });
  }

  addCell(number, summary) {
    if (!this.welcome && !this.log.textStorage().string().endsWith("\n\n")) this.append("\n");  // One blank line.
    this.currentCell = { at: this.log.textStorage().length(), length: `● cell ${number} · ${summary}`.length };
    this.append(`● cell ${number} · ${summary}\n`, "cell");
    this.setState(`Running · cell ${number}`);
  }

  finishCell(result) {
    const { at, length } = this.currentCell;
    const storage = this.log.textStorage();
    if (result.error) storage.addAttribute_value_range_("NSColor", COLORS.error, [at, length]);
    storage.replaceCharactersInRange_withAttributedString_([at, 1],
      this.styled(result.error ? "✗" : "✓", result.error ? "error" : "cell"));
    if (result.error) this.append(result.error + "\n", "error");
    this.setState(result.quit ? "Ended" : "Ready");
  }

  setState(state) {
    this.state = state;
    this.started = Date.now();
    const placeholder = state === "Waiting for input" ? "Message Fibona…" : "Fibona is working…";
    this.placeholder.setAttributedText_(this.styled(placeholder, "notice", 15));
  }

  // The composer's height follows its wrapped text, between one line and COMPOSER_MAX.
  fit() {
    const [, height] = this.field.sizeThatFits_([this.field.bounds()[2], 10000]);
    this.fieldHeight.setConstant_(Math.min(Math.max(height, 44), COMPOSER_MAX));
    this.field.setScrollEnabled_(height > COMPOSER_MAX);
    this.placeholder.setHidden_(this.field.text().length > 0);
  }

  // Model requests, reported by `request` as they stream.
  modelStarted() {
    this.modelRequests += 1;
    this.started = Date.now();
    if (!this.tokenSamples.length) this.tokenSamples.push([this.started, 0]);
  }

  modelTokens(count) {
    this.tokenSamples.push([Date.now(), this.tokenSamples.at(-1)[1] + count]);
  }

  modelEnded() {
    this.modelRequests -= 1;
  }

  // Ten times a second: the status line, its spinner, and tokens per second over the last 3 seconds.
  refresh() {
    const now = Date.now();
    while (this.tokenSamples.length > 1 && this.tokenSamples[1][0] < now - 3000) this.tokenSamples.shift();
    let speed = 0;
    if (this.tokenSamples.length > 1) {
      const [first, last] = [this.tokenSamples[0], this.tokenSamples.at(-1)];
      speed = (last[1] - first[1]) / Math.max(0.25, (last[0] - first[0]) / 1000);
    }
    const seconds = ((now - this.started) / 1000).toFixed(1);
    let state = this.state;
    if (this.modelRequests) {
      const streaming = this.tokenSamples.length && this.tokenSamples.at(-1)[0] > this.started;
      state = `${streaming ? "Generating" : "Thinking"} · ${seconds}s`;
    } else if (state.startsWith("Running")) {
      state += ` · ${seconds}s`;
    }
    this.statusLabel.setAttributedText_(this.styled(state, "status", 12));
    this.speedLabel.setAttributedText_(this.styled(`↓ ${speed.toFixed(0)} tok/s`, "speed", 12));
    const busy = this.modelRequests > 0 || this.state.startsWith("Running");
    if (busy !== this.spinner.isAnimating()) busy ? this.spinner.startAnimating() : this.spinner.stopAnimating();
  }

  // The next line the user sends; `input` in cells.
  // The host's setup can also take a `fallback` for an empty reply, and mask a `secret` reply once it is sent.
  input(prompt = "", { secret = false, fallback = null } = {}) {
    if (prompt) this.write(prompt.endsWith("\n") ? prompt : prompt + "\n");
    this.setState("Waiting for input");
    if (fallback !== null) this.placeholder.setAttributedText_(this.styled(`Return for ${fallback}`, "notice", 15));
    Object.assign(this, { secret, fallback });
    return new Promise((resolve) => this.waiting.push(resolve));
  }

  submit(text) {
    if (!text.trim() && this.fallback !== null) text = this.fallback;
    const shown = this.secret ? "•".repeat(Math.min(text.length, 12)) : text;
    Object.assign(this, { secret: false, fallback: null });
    // Right under the question it answers, then a blank line.
    this.append(` › ${shown} \n`, "user", { NSBackgroundColor: COLORS.composer, NSFont: mono(13, 0.4) });  // Bold.
    this.append("\n");
    this.setState("Running");
    this.waiting.shift()(text);
  }

  // Move the floating button's window with the finger, then settle at the nearest side, inside the safe area.
  drag(gesture) {
    const screen = this.window.rootViewController().view();  // Full-screen and still, even while hidden.
    if (gesture.state() === 1) this.dragFrom = this.handleWindow.frame();
    const [dx, dy] = gesture.translationInView_(screen);
    const [x, y, w, h] = this.dragFrom;
    this.handleWindow.setFrame_([x + dx, y + dy, w, h]);
    if (gesture.state() < 3) return;  // Ended, cancelled or failed from here on.
    const [, , width, height] = ObjC.UIScreen.mainScreen().bounds();
    const [top, left, bottom, right] = screen.safeAreaInsets();
    const nx = x + dx + w / 2 < width / 2 ? left + 8 : width - right - w - 8;  // The window adds 8 around the button.
    const ny = Math.min(Math.max(y + dy, top), height - bottom - h);
    ObjC.UIView.animateWithDuration_animations_(0.25, ObjC.block("v", () => this.handleWindow.setFrame_([nx, ny, w, h])));
  }

  confirmReset() {
    this.show();
    const alert = ObjC.UIAlertController.alertControllerWithTitle_message_preferredStyle_("Reset Fibona?",
      "This deletes everything Fibona built and saved, then closes the app. Open it again to start fresh.", 1);
    alert.addAction_(ObjC.UIAlertAction.actionWithTitle_style_handler_("Cancel", 1, null));
    alert.addAction_(ObjC.UIAlertAction.actionWithTitle_style_handler_("Reset", 2, ObjC.block("v@", () => resetApp())));
    this.window.rootViewController().presentViewController_animated_completion_(alert, true, null);
  }

  show() {
    this.window.makeKeyAndVisible();
  }

  hide() {
    this.window.setHidden_(true);
  }
}

// --- RUNTIME -------------------------------------------------------------------------------------------

globalThis.setTimeout = (fn, ms = 0, ...args) =>
  ObjC.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(ms / 1000, false, ObjC.block("v@", () => fn(...args)));
globalThis.clearTimeout = (timer) => timer?.invalidate();

const session = ObjC.NSURLSession.sessionWithConfiguration_delegate_delegateQueue_(
  ObjC.NSURLSessionConfiguration.defaultSessionConfiguration(), null, ObjC.NSOperationQueue.mainQueue(),
);

globalThis.http = (url, { method = "GET", headers = {}, body = null } = {}) =>
  new Promise((resolve, reject) => {
    const request = ObjC.NSMutableURLRequest.requestWithURL_(ObjC.NSURL.URLWithString_(url));
    request.setHTTPMethod_(method);
    request.setTimeoutInterval_(600);
    for (const [k, v] of Object.entries(headers)) request.setValue_forHTTPHeaderField_(v, k);
    if (body !== null) request.setHTTPBody_(ObjC.send(body, "dataUsingEncoding:", 4));  // UTF-8
    const done = ObjC.block("v@@@", (data, response, error) => {
      if (error) reject(new Error(error.localizedDescription()));
      else resolve({ status: response.statusCode(), text: ObjC.NSString.alloc().initWithData_encoding_(data, 4) });
    });
    session.dataTaskWithRequest_completionHandler_(request, done).resume();
  });

const files = ObjC.NSFileManager.defaultManager();
const DOCS = files.URLsForDirectory_inDomains_(9, 1)[0].path();  // Documents, user domain.
// What the app does outside cells, so the agent can see how what it built behaves in the user's hands: prints from
// callbacks and timers, callback errors, and how boot.js went. Only the newest APP_LOG_LIMIT lines are kept.
globalThis.app_log = [];
const APP_LOG_LIMIT = 500;
function logApp(text) {
  const time = new Date().toTimeString().slice(0, 8);
  for (const line of text.replace(/\n$/, "").split("\n")) app_log.push(`[${time}] ${line}`);
  app_log.splice(0, Math.max(0, app_log.length - APP_LOG_LIMIT));
}

// What is on screen now, every visible window back to front, as a PNG data URL. Scale 1 keeps it small for a model.
globalThis.screenshot = () => {
  const format = ObjC.UIGraphicsImageRendererFormat.preferredFormat();
  format.setScale_(1);
  const renderer = ObjC.UIGraphicsImageRenderer.alloc().initWithBounds_format_(ObjC.UIScreen.mainScreen().bounds(), format);
  const windows = ObjC.UIApplication.sharedApplication().windows().filter((w) => !w.isHidden());
  const png = renderer.PNGDataWithActions_(ObjC.block("v@", () => {
    for (const w of windows) w.drawViewHierarchyInRect_afterScreenUpdates_(w.frame(), true);
  }));
  return "data:image/png;base64," + png.base64EncodedStringWithOptions_(0);
};

const readText = (path) => ObjC.NSString.stringWithContentsOfFile_encoding_error_(path, 4, null);  // null if missing

// The model settings, kept in the keychain by setup() at first launch rather than built into the app.
const SETTINGS = "model settings";  // Keychain account.
let CONFIG = JSON.parse(native.keychainGet(SETTINGS) ?? "null");  // {baseURL, apiKey, model}

// One Responses API request to CONFIG; the model is CONFIG's unless the body names
// one. It streams, like bootstraps/terminal, so the console can show progress, and resolves to the complete response.
function request(body) {
  return new Promise((resolve, reject) => {
    const req = ObjC.NSMutableURLRequest.requestWithURL_(ObjC.NSURL.URLWithString_(CONFIG.baseURL + "/responses"));
    req.setHTTPMethod_("POST");
    req.setTimeoutInterval_(600);
    req.setValue_forHTTPHeaderField_("application/json", "Content-Type");
    req.setValue_forHTTPHeaderField_("Bearer " + CONFIG.apiKey, "Authorization");
    req.setHTTPBody_(ObjC.send(JSON.stringify({ model: CONFIG.model, ...body, stream: true }), "dataUsingEncoding:", 4));

    const pending = ObjC.NSMutableData.new();  // Bytes not yet decoded: a chunk can end inside a UTF-8 character.
    let buffer = "";  // Text not yet split into server-sent events.
    let raw = "";  // Everything received, for errors that are not events.
    let settled = false;
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      appConsole.modelEnded();
      fn(value);
    };
    const handle = (event) => {
      const data = event.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
      if (!data || data === "[DONE]") return;
      const e = JSON.parse(data);
      if (e.type?.endsWith(".delta") && typeof e.delta === "string") {
        appConsole.modelTokens(encodeURIComponent(e.delta).replace(/%../g, "x").length / 4);  // UTF-8 bytes / 4
      } else if (e.type === "response.completed" || e.type === "response.incomplete") {
        settle(resolve, withOutputText(e.response));
      } else if (e.type === "response.failed") {
        settle(reject, new Error(e.response?.error?.message ?? "Model request failed."));
      } else if (e.type === "error") {
        settle(reject, new Error(e.message ?? JSON.stringify(e)));
      }
    };
    const delegate = ObjC.delegate({
      URLSession_dataTask_didReceiveData_: (session, task, data) => {
        pending.appendData_(data);
        const text = ObjC.NSString.alloc().initWithData_encoding_(pending, 4);
        if (text == null) return;  // Wait for the rest of a split character.
        pending.setLength_(0);
        raw += text;
        buffer += text.replaceAll("\r", "");
        const events = buffer.split("\n\n");
        buffer = events.pop();
        events.forEach(handle);
      },
      URLSession_task_didCompleteWithError_: (session, task, error) => {
        const status = task.response()?.statusCode();
        if (error) settle(reject, new Error(error.localizedDescription()));
        else if (status !== 200) settle(reject, new Error(`Model request failed: HTTP ${status}: ${raw}`));
        else settle(reject, new Error("Model stream ended without a final response."));
      },
    }, ["NSURLSessionDataDelegate"]);
    // The session holds the delegate until its task ends.
    const session = ObjC.NSURLSession.sessionWithConfiguration_delegate_delegateQueue_(
      ObjC.NSURLSessionConfiguration.defaultSessionConfiguration(), delegate, ObjC.NSOperationQueue.mainQueue());
    appConsole.modelStarted();
    session.dataTaskWithRequest_(req).resume();
    session.finishTasksAndInvalidate();
  });
}

function withOutputText(response) {
  response.output_text = response.output.filter((o) => o.type === "message").flatMap((o) => o.content)
    .filter((c) => c.type === "output_text").map((c) => c.text).join("");
  return response;
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
      "Documents directory: " + DOCS,
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
const bundled = (name) => readText(ObjC.NSBundle.mainBundle().pathForResource_ofType_(name, "js"));
const SOURCE_CODE = { "core.js": bundled("core"), "app.js": bundled("app") };
const EXCLUDED_NAMES = new Set(["task", "call_me", "call_llm_api", "quit", "history", "input", "source_code"]);

// --- MAIN ----------------------------------------------------------------------------------------------

const appConsole = new Console();
const consoleWindow = appConsole.window;
const env = new JSEnv({
  write: (text) => {
    if (env.capture === null || callbackDepth > 0) logApp(text);  // The app's own output, not a cell's.
    appConsole.write(text);
  },
  readInput: (prompt) => appConsole.input(prompt),
});
const mind = new Mind(request, INSTRUCTIONS + "\n\n\n" + IPHONE);
const agent = new Agent(mind);
env.bind({ call_llm_api: (input, params) => mind.query(input, params) });  // boot.js may use it before the agent runs.
const HOST_NAMES = new Set(Object.getOwnPropertyNames(globalThis));  // Hidden from variables().

const defaults = ObjC.NSUserDefaults.standardUserDefaults();
const BOOT = DOCS + "/boot.js";
const FAILURES = "fibona.bootFailures";

// Back to the first launch: delete what the agent built and saved (files and settings), then end the process, and the
// session with it. Permissions the user granted stay with iOS until the app is deleted. Only the user starts this.
function resetApp() {
  const library = files.URLsForDirectory_inDomains_(5, 1)[0].path();  // Library, user domain.
  for (const dir of [DOCS, library, files.temporaryDirectory().path()]) {
    for (const name of files.contentsOfDirectoryAtPath_error_(dir, null) ?? []) {
      if (dir !== library || name !== "Preferences") files.removeItemAtPath_error_(dir + "/" + name, null);
    }
  }
  defaults.removePersistentDomainForName_(ObjC.NSBundle.mainBundle().bundleIdentifier());
  native.keychainSet(SETTINGS, null);
  defaults.synchronize();
  try {
    ObjC.UIApplication.sharedApplication().performSelector_("terminateWithSuccess");  // Private; there is no public way.
  } catch {
    env.write("Reset done. Close Fibona and open it again.\n");
  }
}

// Run boot.js, counting launches it fails to survive; after 3 in a row, set it aside.
async function boot() {
  const code = readText(BOOT);
  const failures = defaults.integerForKey_(FAILURES);
  if (!code) return;
  if (failures >= 3) {
    files.removeItemAtPath_error_(DOCS + "/boot.failed.js", null);
    if (!files.moveItemAtPath_toPath_error_(BOOT, DOCS + "/boot.failed.js", null))
      return env.write("Safe mode: could not move boot.js to boot.failed.js; boot.js was skipped.\n");
    defaults.setInteger_forKey_(0, FAILURES);
    return env.write(`Safe mode: boot.js failed to start ${failures} times in a row and was moved to boot.failed.js.\n`);
  }
  // Counted before it runs and saved now, so a crash still counts; cleared once it has run for a while.
  defaults.setInteger_forKey_(failures + 1, FAILURES);
  defaults.synchronize();
  const result = await env.execute(code);
  logApp(`boot.js ${result.error ? "failed" : "ran"}\n${result.output}${result.error ?? ""}`);
  if (result.error) return env.write(`boot.js failed:\n${result.error}\n`);
  appConsole.hide();
  setTimeout(() => defaults.setInteger_forKey_(0, FAILURES), 10000);
}

// First launch: ask for the model settings in the console, check them with one small request, and keep them in the
// keychain. Asks again until a request works. Reset deletes them.
async function setup() {
  env.write("Fibona needs a model API to think with. It asks once and keeps the answers in this phone's keychain.\n\n");
  while (true) {
    const baseURL = (await appConsole.input("Model API base URL")).trim();
    const apiKey = (await appConsole.input("API key", { secret: true })).trim();
    const model = (await appConsole.input("Model", { fallback: "gpt-6-astra" })).trim();
    CONFIG = { baseURL: baseURL.replace(/\/+$/, ""), apiKey, model };
    env.write("Checking…\n");
    try {
      await mind.query("Reply with OK.", { max_output_tokens: 16 });
    } catch (e) {
      env.write(`That did not work: ${e.message}\nLet's try again.\n`);
      continue;
    }
    const status = native.keychainSet(SETTINGS, JSON.stringify(CONFIG));
    env.write(status === 0 ? "Saved.\n" : `Could not save to the keychain (error ${status}), so they last until the app closes.\n`);
    return;
  }
}

// The agent reads each task with input(); when a run ends, a new one waits for the next message.
async function main() {
  if (!CONFIG) await setup();
  await boot();
  while (true) {
    await agent.run(BOOTSTRAP, {
      env,
      onCell: (number, summary) => appConsole.addCell(number, summary),
      onResult: (number, result) => appConsole.finishCell(result),
    });
    env.write("The run ended. Send a message to start a new one.\n");
  }
}

main().catch((e) => env.write(`Fibona stopped: ${e}\n${e?.stack ?? ""}\n`));
