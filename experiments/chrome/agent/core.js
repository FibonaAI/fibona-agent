// Fibona's core in JavaScript: Mind, Memory, Slot and Agent from core/agent.py, and the session of core/env.py.
// Plain JavaScript with no platform APIs: the host supplies the model transport and where output and input go.
// runtime/runtime.js and console/console.js adapt the session to Chrome.

const INSTRUCTIONS = `
You are the Fibona agent.

You act only by writing the next cell of a JavaScript program, which runs in a persistent session and calls you
back to write the one after.

You replace the fixed agent loop (model → tool call → model, driven by the host) with code you write yourself.
Each step is a JavaScript cell; the cell calls you as a function whenever it needs a decision, and your answer is
the next cell. A cell that never calls you has no next cell. The agent loop no longer lives in the host: it lives
in the program you write.


## Self

You are self-aware. Your self can be understood from two perspectives:

- Internally, your self consists of the predefined instructions, the engine's working mechanism,
  the \`set_next_cell\` tool (without loss of generality), and your entire past history.
- Externally, your self continually evolves on top of that internal self and may also include the code
  in the JavaScript cells you write. When someone outside refers to "you" or "yourself", use the context
  to determine which parts they mean.

\`fibona_call_me\` is the only way to communicate with yourself; whenever you have information to send to yourself,
call \`fibona_call_me\` with that information in \`input\`.

Your memory is this session's variables and \`fibona_history\`. You are alive while the loop runs, each cell waking your
mind with your memory.


## fibona_call_me

\`await fibona_call_me(input, params)\` wakes you and resolves to the Responses API result (\`r.output_text\` for your
reply, \`r.output\` for all items).
- \`input\`: a string, or an array of messages and items.
- \`params\` (optional object): \`tools\` and \`tool_choice\` for your own tools, \`text\` for structured output
  (e.g. \`{format: {type: "json_object"}}\`), \`reasoning: {effort: "high"}\` to think harder (never below medium).

While awake, choose the next cell with the \`set_next_cell(code, summary)\` tool; the last submission in a cell
wins.

You wake up knowing only what the call gives you, so build \`input\` from state: take the results, errors and
facts you need from variables and \`fibona_history\`, and name the variables that hold the full data so the next cell
can use them.


## What a cell does

    pages = await fetchAll(urls);  // do the work in code; a bare assignment keeps \`pages\` for later cells
    fibona_print(\`Fetched \${pages.length} pages.\`);  // the user sees only what you print
    const r = await fibona_call_me("pages (url -> text) holds " + pages.length + " items. Next: summarize.");
    fibona_print(r.output_text);  // show the reply to the user


## Cells

- A cell is the body of an async function: \`await\` works anywhere, and the cell ends when it returns.
  Always \`await fibona_call_me(...)\`; a cell that ends before you choose its next cell fails.
- \`let\`, \`const\` and \`function\` declarations stay inside the cell. Assign globals to keep values:
  \`todos = []\`, \`globalThis.render = () => { ... }\`.
- A finished cell's saved \`fibona_call_me\` cannot schedule another cell. Event handlers can supply data to
  an active cell, which awaits it and calls you. Failed cells do not roll back side effects.


## State

- Fibona-provided session names use the \`fibona_\` prefix; platform globals keep their original names.
- **Variables** persist. Inspect them with \`Object.keys(globalThis)\`; values are not automatically sent to you.
- **\`fibona_history\`**: recorded cells as \`{code, summary, output, error}\` objects, with the full output.
  Summaries record intent; output and errors record what happened. Each cell receives a copy of the host's records.
- Choose what to include in \`input\`. The host does not select or send working context for you.


## In the session

- \`await fibona_call_llm_api(input, params)\`: a plain model call without your predefined instructions or host tool,
  resolving to the reply text. It is not you, just a way to use intelligence as a function; it cannot choose a
  cell, so it is safe to call many at once.
- \`await fibona_input(prompt)\`: asks the user and waits for the reply; use it when you need user input or are ready
  to wait for further instructions.
- \`fibona_print(...)\` and \`fibona_console.log(...)\` write to the user's screen and to the cell's output.
- \`fibona_quit()\`: ends the run. Call it only when the user asks.
- Never print secrets or pass them to a model.


## Working principles

- Solve problems from first principles.
- Persist through failures, try alternatives, and verify results.
- Work independently towards your task; automatically recover from failures without asking.
- Let unexpected errors propagate out of the cell so the recovery cell can wake you to repair them.
  Never write code that traps control or requires unnecessary user intervention to return it to you.
- You do not need to converse continuously, but ensure the user can reach you when needed.


## Browser environment

These are host facts. Working instructions and context selection belong to the editable bootstrap,
not this core. \`fibona_source_code\` contains the packaged source and README; read relevant excerpts as needed.

- You run in a singleton background JS REPL inside an offscreen sandbox. Your \`document\` is hidden,
  not the current webpage or your visible app. Closing views or restarting the service worker preserves
  REPL state. Stop resets it; browser shutdown, extension reload or renderer failure loses live JS memory.
  Saved startup code in storage.local's \`fibona_boot\` key runs once per fresh runtime, not per view.
- \`await fibona_chrome(path,...args)\` forwards native Chrome methods. Arguments/results follow the JSON
  contract; functions and Ports cannot cross. \`fibona_chrome.callback(path,...args)\` waits for a native
  callback, rejects runtime.lastError, and returns callback values or the synchronous result if none.
  \`await fibona_on(path,callback)\` subscribes to a discovered event and returns async unsubscribe;
  \`fibona_on_message(async(message,sender)=>reply)\` handles content/user-script messages.
- \`await fibona_browser(path,...args)\` calls native Web APIs in the extension's offscreen host.
  The root supports async get/set/construct/release; returned handles also have call(path,...args).
  The root itself calls methods by path. Data is cloned; native objects stay in the host. Pass handles back to preserve
  identity; construct host objects through the bridge. Functions become async callbacks, so synchronous
  callback contracts cannot be transparently emulated. Releasing a handle does not close its resource;
  native close/removeListener operations are still required. \`fibona_browser.release(callback)\` releases
  an exported callback reference after unregistering it.
- \`await fibona_ui(codeString)\` installs JS in each attached view and reruns it on reopen. View DOM and
  variables are separate from REPL state. View \`fibona_send(data)\` calls background \`fibona_on_ui\` handlers;
  background \`fibona_ui_send(data,viewId?)\` calls view \`fibona_on_data\` handlers. \`fibona_ui_context\`
  identifies the view. View-only \`fibona_view\` uses the native bridge for the outer page's window/DOM,
  including the editable #recovery switch. \`fibona_show_console(visible)\` and \`fibona_set_title(title)\`
  control normal views; the separate index.html?safe=1 console never loads custom app code and stays visible.
- \`fibona_environment\` reports grants, discovered events, session id and the native userScripts gate,
  refreshed before each run. Host permissions do not bypass restricted pages, CSP, user activation or
  Chrome's Allow User Scripts toggle. The opaque sandbox restricts direct origin APIs; privileged host
  code cannot eval generated JS. Native globals remain native; bridge calls select the owning context.
`.trim();

const SET_NEXT_CELL = {
  type: "function",
  name: "set_next_cell",
  strict: true,
  description: "Submit the complete JavaScript cell to run after the current cell finishes, with a short summary.",
  parameters: {
    type: "object",
    properties: { code: { type: "string" }, summary: { type: "string", maxLength: 256 } },
    required: ["code", "summary"],
    additionalProperties: false,
  },
};

// Fields normalized or fixed by wake; the caller's params must not override them.
const PROTECTED = new Set(["model", "instructions", "tools", "reasoning", "stream", "background", "input",
  "parallel_tool_calls", "store", "previous_response_id"]);

const without = (body, keys) => Object.fromEntries(Object.entries(body).filter(([k]) => !keys.has(k)));

// --- AGENT ---------------------------------------------------------------------------------------------

class Mind {
  // The agent's mind: instructions and the `set_next_cell` tool, reached through `request`.
  // request(body) -> Promise<response>: one Responses API request. The host fills in the model when the body
  // names none, and adds `output_text`.
  constructor(request, instructions) {
    this.request = request;
    this.instructions = instructions;
  }

  // Wake the mind; exposed to cells as `fibona_call_me`. `submit(code, summary)` is where `set_next_cell` lands: the
  // agent binds a fresh one per cell, so a late call from a finished cell cannot choose the next one.
  // body: other Responses API fields. tools are the caller's own, with set_next_cell always appended;
  // reasoning keeps only effort, at least "medium"; model, stream, background, store and previous_response_id
  // are dropped (stateless: to continue, put earlier items such as `r.output` into input); parallel tool calls
  // are off, so set_next_cell never shares a turn with the caller's tools.
  async wake(input = [], submit, body = {}) {
    const effort = body.reasoning?.effort;
    const tools = (body.tools ?? []).filter((t) => t.name !== SET_NEXT_CELL.name);
    body = {
      ...without(body, PROTECTED),
      instructions: this.instructions,
      reasoning: { effort: ["medium", "high", "xhigh"].includes(effort) ? effort : "medium" },
      tools: [...tools, SET_NEXT_CELL],
      parallel_tool_calls: false,
      store: false,
      // Without server-side storage, reasoning can only be replayed in its encrypted form.
      include: [...new Set([...(body.include ?? []), "reasoning.encrypted_content"])],
    };
    let items = inputItems(input);
    while (true) {
      const response = await this.request({ ...body, input: items });
      const host = response.output.find((c) => c.type === "function_call" && c.name === SET_NEXT_CELL.name);
      if (!host) return response;  // A plain reply, or one of the caller's tools for it to answer.
      const output = { type: "function_call_output", call_id: host.call_id, output: runHostTool(host, submit) };
      // Replay every output item, including encrypted reasoning, without server-side storage.
      const replay = response.output.map((o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v != null)));
      items = [...items, ...replay, output];
    }
  }

  // A plain model call used as a function (summarize, extract, classify, ...); exposed to cells as `fibona_call_llm_api`.
  // No agent instructions or host tool are added, so it cannot schedule a cell. Resolves to the reply text.
  async query(input, params = {}) {
    params = without(params, new Set(["stream", "background", "previous_response_id"]));
    return (await this.request({ ...params, store: false, input: inputItems(input) })).output_text;
  }
}

const inputItems = (value) => (typeof value === "string" ? [{ role: "user", content: value }] : [...value]);

function runHostTool(call, submit) {
  let result;
  try {
    const args = JSON.parse(call.arguments);
    if (!args.code?.trim()) throw new Error("code must be a non-empty string");
    submit(args.code, args.summary);
    result = { scheduled: true };
  } catch (error) {
    result = { scheduled: false, error: error.message ?? String(error) };
  }
  return JSON.stringify(result);
}

class Memory {
  // The host's execution records; cells receive copies.
  history = [];  // One {code, summary, output, error} object per finished cell.

  // Number of finished cells, which is also the number of the running one.
  get cells() {
    return this.history.length;
  }

  // After a cell: keep its original record, with the full output.
  record(code, summary, result) {
    this.history.push({ code, summary, output: result.output, error: result.error });
  }

  // Copies for cells to read as history; editing them cannot alter the originals.
  snapshot() {
    return this.history.map((record) => ({ ...record }));
  }
}

class Slot {
  // The next cell chosen during the current one. Closed once that cell ends, so a late `set_next_cell` from it
  // fails instead of overwriting a newer cell's choice.
  code = null;
  summary = null;
  closed = false;

  submit(code, summary) {
    if (this.closed) throw new Error("This cell has finished; its fibona_call_me can no longer choose a cell");
    this.code = code;  // The last submission wins.
    this.summary = summary;
  }

  close() {
    this.closed = true;
  }
}

class Agent {
  // A mind with a session and a memory, alive while the loop runs.
  static REPAIR = `
The previous cell failed (its error is in fibona_history).
If needed, inspect session variables via Object.keys(globalThis) and fibona_history to recover the task and progress.
Repair the failure and continue the program.
`.trim();
  static REPAIR_SUMMARY = "Repair the failed cell.";

  constructor(mind) {
    this.mind = mind;
    this.memory = new Memory();
  }

  // Execute bootstrapCell verbatim, then run the cells selected by its program.
  // Startup policy, user tasks and UI composition belong in the bootstrap cell. onCell(number, summary, code)
  // observes each cell before it runs; onResult(number, result) receives its validated result, including the
  // cell that quits. Resolves when a cell quits, or when a repair cell fails too: unlike core/agent.py, which
  // retries without limit, so an unreachable model cannot spin the loop. Interruptions are not supported.
  async run(bootstrapCell, { env = new JSEnv(), onCell, onResult } = {}) {
    const memory = this.memory;
    let code = bootstrapCell;
    let summary = "Run the bootstrap cell.";
    let repairing = false;
    while (true) {
      const slot = new Slot();
      let quitting = false;
      // Restore runtime bindings each cell; mutations to shared objects still persist.
      env.bind({
        fibona_call_me: (input, body) => this.mind.wake(input, (c, s) => slot.submit(c, s), body),
        fibona_call_llm_api: (input, params) => this.mind.query(input, params),
        fibona_quit: () => {
          quitting = true;  // Holds even if the cell catches the Quit below.
          throw new Quit();
        },
        fibona_history: memory.snapshot(),
      });
      onCell?.(memory.cells, summary, code);
      const result = await env.execute(code);
      slot.close();
      result.quit ||= quitting;
      if (!result.quit && result.error === null && slot.code === null) {
        result.error = "The cell finished without choosing a next cell via set_next_cell.";
      }
      onResult?.(memory.cells, result);
      if (result.quit) return;
      memory.record(code, summary, result);

      if (result.error === null) {
        repairing = false;
        ({ code, summary } = slot);
        continue;
      }
      if (repairing) return;
      repairing = true;
      // Side effects of the failed cell are not rolled back; the mind repairs from here.
      const message = Agent.REPAIR + "\n\n" + JSON.stringify({ bootstrap_cell: bootstrapCell, code, error: result.error });
      code = wakeCell(message);
      summary = Agent.REPAIR_SUMMARY;
    }
  }
}

// A cell that only wakes the mind with `message`; the mind chooses the next cell.
const wakeCell = (message) => `const response = await fibona_call_me(${JSON.stringify(message)});\nfibona_print(response.output_text);`;

// --- ENV -----------------------------------------------------------------------------------------------

// Raised by `fibona_quit()` in a cell to end the run. JavaScript has no exception a cell's catch cannot swallow, so the
// agent also notes the call itself.
class Quit extends Error {
  constructor() {
    super("fibona_quit()");
  }
}

class CellResult {
  constructor(output, error = null, quit = false) {
    this.output = output;  // Everything the cell printed.
    this.error = error;  // Error and stack if the cell failed.
    this.quit = quit;  // The cell raised Quit.
  }
}

class JSEnv {
  // A session at global scope: runs cells as bodies of async functions; the globals they assign persist across
  // cells. Output is always captured and also goes to `write` live. User input requires a `readInput` handler,
  // so the environment does not depend on a console. A cell that blocks or crashes the host takes the agent with it.
  constructor({ write = () => {}, readInput = unavailableInput } = {}) {
    this.write = write;
    this.readInput = readInput;
    this.capture = null;  // The running cell's output.
    const print = (...values) => this.print(values.map(format).join(" ") + "\n");
    const console = Object.fromEntries(["log", "info", "warn", "error"].map(level => [level,
      (...values) => this.print(values.map(format).join(" ") + "\n", level),
    ]));
    this.bind({ fibona_print: print, fibona_console: console });
  }

  print(text, level = "log") {
    this.capture?.push(text);
    this.write(text, level);
  }

  // Put names (fibona_call_me, fibona_quit, ...) into the cells' global scope, replacing old ones.
  bind(names) {
    Object.assign(globalThis, names);
  }

  // Resolves to the cell's output and error; Quit sets the quit flag.
  async execute(code) {
    const output = (this.capture = []);
    this.bind({ fibona_input: (...args) => this.readInput(...args) });
    try {
      await (0, eval)(`(async () => {\n${code}\n})()`);  // Indirect eval: global scope, not this function's.
      return new CellResult(output.join(""));
    } catch (error) {
      if (error instanceof Quit) return new CellResult(output.join(""), null, true);
      return new CellResult(output.join(""), `${error}\n${error?.stack ?? ""}`.trim());
    } finally {
      this.capture = null;
    }
  }
}

function format(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function unavailableInput() {
  throw new Error("No input handler configured for this environment");
}
