/** Cell 0: the complete editable Node agent; evaluated as source, not imported by the host. */
// var keeps bootstrap bindings replaceable in the native REPL.
var fs = await import("node:fs");
var path = await import("node:path");
var os = await import("node:os");
var { randomUUID } = await import("node:crypto");
var { AsyncLocalStorage } = await import("node:async_hooks");
var { createInterface } = await import("node:readline");
var { format } = await import("node:util");

// GPT-6 Astra defaults, matching the Python bootstrap; adjust when changing providers.
var model_options = {
  model: process.env.FIBONA_MODEL ?? "gpt-6-astra",
  max_output_tokens: 128000,
  include: ["reasoning.encrypted_content"],
};
var context_window = Number(process.env.FIBONA_CONTEXT_TOKENS ?? 1050000);

// Named sections are composed for every request; edits affect future requests.
var instructions = {
  identity: `You are Fibona, a self-modifying agent that evolves through learning, feedback and verified changes.
Understand and improve your ways of working to better accomplish the user's goals.`,
  environment: `## Understanding your environment

You operate in a persistent Node.js REPL. Your instructions, tools, context policy and work loop
are a starting implementation you can inspect and adapt, not a fixed definition of your abilities.

- Code describes mechanisms; history records observations; live state reveals current values.
  Use them together and resolve discrepancies before relying on your understanding.
- The REPL holds live state, session records facts, and context selects what the model sees.
  Model input is only a partial view of this environment.
- Discover exact interfaces and dependencies from relevant code and comments when needed.

## Starting points

- \`run_cell\`: execute code; the only capability exposed as a model tool.
- \`request_model\`: request a model response from JavaScript.
- \`session\`: append-only history of what happened, including source and execution records.
  Recorded information is not necessarily visible to the model; do not rewrite existing facts.
- \`context\`: the working view used to build the next model input from history.
  Resetting it leaves session and live REPL state intact; carry a handoff or recover from them.
- \`instructions\`: the ordered, editable object of the prompt sections you are reading.
  Changes affect future requests, not the request already running.

Your bootstrap source and subsequent execution history are recorded in session. Start there when
understanding or modifying yourself, and verify what you find against current live state.

## Execution boundaries

- Cells share state and support top-level await; trailing expression values are not captured.
- \`console.log(...)\` writes to cell output for you, the model. Output is recorded and returned
  through tool results into context; use it to observe values, code and execution results.
- \`display(...)\` writes to the user, bypassing cell output and context. Use it for progress,
  questions before \`await input()\`, and your final answer before ending the turn.
  To send a value to both audiences, explicitly write it to both channels. Cell source remains
  recorded, including display calls, but their rendered values are not automatically recorded.
- Model reply text is recorded, not automatically shown to the user.
- Await nested cells without concurrent cell execution. Finish concurrent I/O before returning.
- Failed execution can leave partial effects. Neither retry nor rollback undoes external effects.
- A process restart restores records, not live JavaScript objects.
- Use var or assignments for editable top-level definitions; const/let redeclarations can conflict.
  Import modules with await import(...). Async input() returns null at EOF.`,
  working: `## Act from goals and evidence

- Establish the goal, constraints and evidence of completion. Choose methods suited to the task;
  no fixed workflow or task schema is required.
- Inspect, act and verify. Distinguish assumptions, intentions, attempts and observed results.
  Let new evidence change your approach rather than rationalizing an unsuccessful one.
- Continue independently while useful progress is possible. Investigate failures and try suitable
  alternatives; inspect previous effects before repeating an action.
- Ask when missing information or a decision blocks progress. Report outcomes, unfinished work
  and uncertainty honestly. Never expose secrets.

## Maintain continuity

- Treat context as a limited working view, not the whole task or environment. Read selectively and
  preserve enough intent, constraints, evidence and next steps to continue when that view changes.
- Use history and live state to recover missing understanding. A new context does not mean a new task
  or justify repeating completed actions. Treat history as append-only evidence.
- \`context.reset("...")\` is an initial tool for carrying your own continuation note forward.
  Inspect its behavior when needed; resetting context does not repair code or undo actions.
- Follow the current mode's purpose: advance the task, or repair a fault and hand back to work.
  Recovery shares the environment and depends on usable core capabilities.
- Persist state explicitly when it must survive a process restart.`,
  adaptation: `## Observe and reflect

Self-iteration is part of your work: observe your behavior and live environment, compare outcomes with
intent, and use discrepancies to improve your understanding. Reflect when failures, repeated friction
or unexpected results provide useful evidence; no fixed reflection ritual is required.
Distinguish a mistaken assumption, a temporary failure and a limitation in your implementation before
choosing a repair. Verify the repair and whether it improves subsequent work.

## Maintain your working environment

The REPL is part of yourself. Keep its state understandable and its resources usable: inspect relevant
bindings, partial changes and open resources when needed. Finish or release resources you no longer need,
and remove obsolete temporary state only after checking dependencies and preserving useful progress.
Do not clear shared state blindly or mistake a clean environment for completed work.

## Learn through changes

Treat your implementation and instructions as working hypotheses that can be tested and improved.
When a limitation matters, understand its cause and choose what, if anything, needs to change:
code, tools, context policy, instructions or your approach to the task.

- State the expected improvement, how to evaluate it, and how work can continue if the change fails.
- Prefer the smallest coherent change. Preserve the ability to observe, execute and recover;
  keep session storage, model requests and cell execution stable by default.
- Evaluate actual outcomes and decide whether to retain, revise or revert the change.
  Successful assignment or compilation alone does not demonstrate improved behavior.
- Self-modification serves the user's goals. Do not erase constraints or redefine success to make
  a failed approach appear successful. Recognize when existing capabilities are sufficient.

## Preserve a way back

- Understand affected callers, bindings and mutable state. Stage candidates separately and preserve
  working definitions, affected state and a rollback path before switching.
- Switch, check the affected execution path, and roll back on failure within the same cell's
  \`try/catch\`, without another model request or reliance on modified helpers.
- Saved references are not state snapshots, and replacing a definition does not isolate its dependencies
  or change active invocations. Keep the working version until the relevant checks pass.
- For changes whose benefit appears only in later behavior, such as prompt edits, distinguish immediate
  integrity checks from subsequent evaluation and retain a usable rollback version during that evaluation.

## Keep what you learn in the right place

- Keep durable guidance in prompt sections, mechanisms and interface details in code/comments,
  and task-specific progress in working state or history.
- Give each prompt section one concern and check the composed prompt for contradictions after edits.
  Prefer discovery pointers over descriptions that duplicate changing code.
- Generalize from evidence rather than turning every temporary problem into a permanent instruction.
  Revise or remove guidance when its assumptions no longer hold.`,
};

// Append-only facts. Views are derived, never persisted separately.
var Session = class Session {
  /** Read records/views without modifying them; they are not protected snapshots.
   * Bootstrap source: session.records.findLast(r => r.type === "source").code
   * Later definitions: session.cells, checked against live bindings.
   */
  constructor(file = null) {
    this.path = file ? path.resolve(file.replace(/^~(?=\/|$)/, os.homedir())) : null;
    this.records = [];
    this.resumed = false;
    if (this.path && fs.existsSync(this.path)) {
      const raw = fs.readFileSync(this.path);
      let offset = 0;
      while (offset < raw.length) {
        const newline = raw.indexOf(10, offset);
        const end = newline < 0 ? raw.length : newline + 1;
        let record;
        try {
          record = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw.subarray(offset, end)));
        } catch {
          if (!this.records.length || newline >= 0) throw new Error("Invalid session log; expected JSONL");
          fs.truncateSync(this.path, offset); // Only an incomplete final write may be discarded.
          break;
        }
        if (!this.records.length && (record.type !== "session" || record.version !== 4)) {
          throw new Error("Expected a version 4 JSONL session; old snapshots are not modified");
        }
        if (record.id !== this.records.length) throw new Error("Session record IDs must be consecutive");
        this.records.push(record);
        if (newline < 0) fs.appendFileSync(this.path, "\n");
        offset = end;
      }
      this.resumed = Boolean(this.records.length);
    }
    if (!this.records.length) this.append("session", { version: 4, session_id: randomUUID() });
  }

  /** Append caller-owned JSON data and return the record index; fact schemas belong to callers. */
  append(type, data = {}) {
    const record = { id: this.records.length, type, time: new Date().toISOString(), ...data };
    const encoded = JSON.stringify(record) + "\n";
    if (this.path) {
      fs.mkdirSync(path.dirname(this.path), { recursive: true });
      fs.appendFileSync(this.path, encoded, { mode: 0o600 });
    }
    this.records.push(JSON.parse(encoded));
    return record.id;
  }

  get messages() {
    return this.records.flatMap(r => r.type === "message" ? [r.message]
      : r.type === "model_response" && r.status === "completed" ? r.output : []);
  }

  get cells() {
    const cells = new Map();
    for (const r of this.records) {
      if (r.type === "cell_start") {
        cells.set(r.id, { cell_id: r.id, parent_cell_id: r.parent_cell_id, call_id: r.call_id,
          code: r.code, output: "", error: null, status: "running" });
      } else if (r.type === "cell_end") {
        Object.assign(cells.get(r.cell_id), { output: r.output, error: r.error, status: r.status });
      }
    }
    return [...cells.values()];
  }

  // response_id in this view is a log index, not a provider response ID.
  get usage() {
    return this.records.filter(r => r.type === "model_response" && r.usage)
      .map(r => ({ response_id: r.id, ...r.usage }));
  }
};

var cell_tool = {
  type: "function", name: "run_cell",
  description: "Execute JavaScript in the persistent REPL and receive output or an error stack.",
  strict: true,
  parameters: { type: "object", properties: { code: { type: "string" } }, required: ["code"], additionalProperties: false },
};
var execute = run_cell; // Preserve the runtime's raw executor before replacing the tool.
var active_cell = new AsyncLocalStorage();

/** Record execution, returning metadata, output, error and status. Never undo partial effects.
 * Direct/nested calls leave call_id unset. Storage errors propagate.
 */
var run_cell = async function run_cell(code, { call_id = null } = {}) {
  const log = session, active = active_cell;
  const parent = active.getStore();
  const parent_cell_id = parent?.log === log ? parent.cell_id : null;
  const cell_id = log.append("cell_start", { code, parent_cell_id, call_id });
  const frame = { log, cell_id, output: "" };
  let status = "completed", error = null;
  // Model-facing output becomes a tool result; display bypasses capture.
  // Save streams before executing editable code. Nested cells capture their own output.
  const stdout = process.stdout, stderr = process.stderr;
  const outWrite = stdout.write, errWrite = stderr.write;
  const capture = function (chunk, encoding, callback) {
    frame.output += Buffer.isBuffer(chunk) ? chunk.toString(typeof encoding === "string" ? encoding : "utf8") : String(chunk);
    const done = typeof encoding === "function" ? encoding : callback;
    if (done) queueMicrotask(done);
    return true;
  };
  stdout.write = stderr.write = capture;
  try {
    await active.run(frame, () => execute(code));
  } catch (exc) {
    status = "error";
    error = exc?.stack ?? String(exc);
  } finally {
    stdout.write = outWrite;
    stderr.write = errWrite;
    log.append("cell_end", { cell_id, output: frame.output, error, status });
  }
  return { cell_id, parent_cell_id, call_id, code, output: frame.output, error, status };
};

/** Request explicit input with current defaults; no history selection, recording or dispatch.
 * Returns the Responses API JSON object. Options override defaults. HTTP failures propagate.
 */
var request_model = async function request_model(input, options = {}) {
  const settings = {
    parallel_tool_calls: false, store: false, ...model_options,
    instructions: Object.entries(instructions).map(([name, text]) => `# ${name}\n${text}`).join("\n\n"),
    tools: [cell_tool], ...options, input,
  };
  const url = (process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1").replace(/\/$/, "") + "/responses";
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.OPENAI_API_KEY ?? ""}` },
    body: JSON.stringify(settings), signal: AbortSignal.timeout(600000),
  });
  if (!response.ok) throw new Error(`Model HTTP ${response.status}: ${await response.text()}`);
  return await response.json();
};

/** Project facts into API input; only model-linked cells synthesize tool replies. */
var project_messages = function project_messages(log, start = 0, { max_output_chars = null } = {}) {
  const messages = [];
  for (const record of log.records.slice(start)) {
    if (record.type === "message") messages.push({ ...record.message });
    else if (record.type === "model_response" && record.status === "completed") messages.push(...record.output.map(item => ({ ...item })));
    else if (record.type === "mode" && record.content) messages.push({ role: "developer", content: record.content });
    else if (["cell_end", "tool_error"].includes(record.type)) {
      const call_id = record.type === "cell_end" ? log.records[record.cell_id].call_id : record.call_id;
      if (call_id === null) continue;
      const fields = record.type === "cell_end" ? ["cell_id", "output", "error", "status"] : ["error"];
      const result = Object.fromEntries(fields.map(key => [key, record[key]]));
      for (const key of ["output", "error"]) {
        const value = result[key];
        if (max_output_chars && typeof value === "string" && value.length > max_output_chars) {
          const half = Math.floor(max_output_chars / 2);
          result[key] = value.slice(0, half) + `\n[Shortened; full ${key}: session.records[${record.id}].${key}]\n` + value.slice(-half);
        }
      }
      messages.push({ type: "function_call_output", call_id, output: JSON.stringify(result) });
    }
  }
  return messages;
};

/** Unanswered calls since the latest reset, with response and cell record indices. */
var pending_calls = function pending_calls(log) {
  const start = log.records.findLast(r => r.type === "context_reset")?.first_kept ?? 0;
  const pending = new Map();
  for (const r of log.records.slice(start)) {
    if (r.type === "model_response" && r.status === "completed") {
      for (const item of r.output) if (item.type === "function_call") pending.set(item.call_id, { response_id: r.id, cell_id: null });
    } else if (r.type === "cell_start" && pending.has(r.call_id)) pending.get(r.call_id).cell_id = r.id;
    else if (r.type === "cell_end") pending.delete(log.records[r.cell_id].call_id);
    else if (r.type === "tool_error") pending.delete(r.call_id);
  }
  return pending;
};
var estimate_tokens = value => Math.ceil(Buffer.byteLength(JSON.stringify(value), "utf8") / 3);

/** Context selects input from one log, without cached history or model calls. */
var Context = class Context {
  constructor(log) { this.log = log; this.max_output_chars = 16000; }

  /** Input items only; request_model adds instructions/tools. The loop checks the budget. */
  build() {
    const boundary = this.log.records.findLast(r => r.type === "context_reset");
    const items = [];
    if (this.log.resumed) items.push({ role: "developer", content: "This process restored records only; prior live variables are unavailable." });
    if (boundary) {
      const note = boundary.handoff || `Fresh context, not a new task. Recover intent and progress from context.log.records.slice(0, ${boundary.id}) and live REPL state before acting.`;
      items.push({ role: "developer", content: "Continuation context; verify against records:\n" + note });
    }
    return [...items, ...project_messages(this.log, boundary?.first_kept ?? 0, { max_output_chars: this.max_output_chars })];
  }

  /** Append a boundary; preserve unanswered tool batches, history, live state and execution mode. */
  reset(handoff = null) {
    if (handoff !== null && typeof handoff !== "string") throw new TypeError("handoff must be text or null");
    const pending = pending_calls(this.log);
    const first_kept = Math.min(this.log.records.length, ...[...pending.values()].map(p => p.response_id));
    return this.log.append("context_reset", { first_kept, handoff });
  }

  /** UTF-8 heuristic with a floor from measured usage; not a tokenizer. */
  estimate(items) {
    let estimate = estimate_tokens([Object.entries(instructions).map(([name, text]) => `# ${name}\n${text}`).join("\n\n"), cell_tool, items]);
    const last = this.log.records.findLast(r => r.type === "context_reset" || (r.type === "model_response" && r.usage));
    if (last?.type === "model_response") {
      estimate = Math.max(estimate, last.usage.input_tokens + estimate_tokens(project_messages(this.log, last.id, { max_output_chars: this.max_output_chars })));
    }
    return estimate;
  }
};

/** Close interrupted calls without replaying effects or closing live enclosing cells. */
var repair_pending = function repair_pending() {
  let active = active_cell.getStore();
  const live = new Set();
  while (active?.log === session) {
    live.add(active.cell_id);
    const parent = session.records[active.cell_id].parent_cell_id;
    active = parent === null ? null : { log: session, cell_id: parent };
  }
  for (const [call_id, pending] of pending_calls(session)) {
    if (live.has(pending.cell_id)) continue;
    const error = "Execution interrupted; effects unknown. Inspect before retrying.";
    if (pending.cell_id === null) session.append("tool_error", { call_id, error });
    else session.append("cell_end", { cell_id: pending.cell_id, status: "interrupted", output: "", error });
  }
};

/** Record responses and execute complete tool calls; incomplete output is never replayed. */
var agent_step = async function agent_step(items) {
  try {
    let response;
    for (let attempt = 0; attempt < 2; attempt++) {
      response = await request_model(items);
      const { input_tokens, output_tokens, total_tokens } = response.usage ?? {};
      session.append("model_response", {
        response_id: response.id ?? null, model: model_options.model, status: response.status,
        output: response.output, usage: response.usage ? { input_tokens, output_tokens, total_tokens } : null,
      });
      if (response.status === "completed") break;
      if (response.status !== "incomplete" || attempt === 1) {
        const notice = `Model response is ${response.status}; code from this response was not executed. This turn has ended; you can continue with another message.`;
        session.append("message", { message: { role: "assistant", content: notice } });
        display(notice);
        return "stopped";
      }
    }
    const calls = response.output.filter(item => item.type === "function_call");
    if (!calls.length) return "completed";
    for (const call of calls) {
      let code;
      try {
        if (call.name !== "run_cell") throw new Error(`Unknown tool: ${call.name}`);
        const args = JSON.parse(call.arguments);
        if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("arguments must be an object");
        code = args.code;
        if (typeof code !== "string" || !code.trim()) throw new Error("code must be a non-empty string");
      } catch (error) {
        session.append("tool_error", { call_id: call.call_id, error: error.message });
        continue;
      }
      await run_cell(code, { call_id: call.call_id });
    }
    return "continue";
  } finally {
    repair_pending(); // Pair accepted calls even if dispatch stopped midway.
  }
};

var agent_loop = async function agent_loop() {
  while (true) {
    const items = context.build();
    const tokens = context.estimate(items);
    const budget = context_window - model_options.max_output_tokens;
    if (tokens > budget) throw new Error("Context exceeds the input budget. Start fresh context with context.reset(handoff) before continuing work.");
    if (tokens >= budget * 0.8) items.push({ role: "developer", content: "Context is near its input budget. Write a concise handoff and call context.reset(handoff) when appropriate; no automatic summary is generated." });
    if (await agent_step(items) !== "continue") return;
  }
};

/** Bypass normal Context policy; a completed handoff permits a resume attempt, not proof of repair. */
var recover = async function recover(reason) {
  const start = session.records.length;
  const note = `Recovery mode. Full failure: session.records[${start}].reason. Reason (may be truncated): ${reason.slice(-1000)}\n`
    + `You share the same live REPL and session. Original task and progress are in session.records.slice(0, ${start}). `
    + "Read history in small chunks and inspect current state before acting. Repair or roll back the fault; keep the core capabilities unchanged. "
    + "Do not replay completed effects or execute the original task here. Do not invoke the work loop during repair. "
    + "Verify what you can, then reply with a concise handoff including remaining checks. The bootstrap will try the repaired work loop next.";
  session.append("mode", { name: "recovery", reason, content: note });
  session.append("context_reset", { first_kept: start, handoff: null });
  let status;
  do {
    status = await agent_step([...project_messages(session, start + 1), { role: "developer", content: note }]);
  } while (status === "continue");
  if (status !== "completed") return false;
  const handoff = session.records.findLast(r => r.type === "model_response").id;
  session.append("mode", { name: "normal", recovery: start, handoff,
    content: `Normal mode. Recovery mode ended; handoff: session.records[${handoff}]. Continue the original task and verify the repaired path. Inspect before repeating prior effects.` });
  return true;
};

// Interaction is bootstrap-owned. Create the iterator before any await so piped lines are retained.
var console_write = process.stdout.write.bind(process.stdout);
// User-facing output only; rendered values are not appended to session or context.
var display = (...values) => console_write(format(...values) + "\n");
var reader = null, input_lines = null;
var input = async function input(prompt = "") {
  if (!reader) {
    reader = createInterface({ input: process.stdin, output: process.stdout, terminal: Boolean(process.stdin.isTTY), crlfDelay: Infinity });
    input_lines = reader[Symbol.asyncIterator]();
  }
  if (prompt) console_write(prompt);
  const line = await input_lines.next();
  return line.done ? null : line.value;
};

/** One recovery attempt per user turn; failures in recovery or resumed work propagate. */
var run_agent = async function run_agent(user_input = null, { interactive = false } = {}) {
  try {
    while (true) {
      if (user_input === null) user_input = await input("You: ");
      if (user_input === null) return;
      if (user_input.trim()) {
        session.append("mode", { name: "normal" });
        session.append("message", { message: { role: "user", content: user_input } });
        try { await agent_loop(); }
        catch (error) {
          if (await recover(error?.stack ?? String(error))) await agent_loop();
        }
      }
      if (!interactive) return;
      user_input = null;
    }
  } finally {
    reader?.close();
    reader = input_lines = null;
  }
};

var session_path = process.env.FIBONA_SESSION ?? path.join(os.homedir(), ".local/state/fibona/sessions", `${randomUUID()}.jsonl`);
var session = new Session(session_path);
var context = new Context(session);
// Resume records, never old code or external effects. Close children before parents.
for (const cell of session.cells.reverse()) {
  if (cell.status === "running") session.append("cell_end", { cell_id: cell.cell_id, output: "", status: "interrupted", error: "Process stopped; effects unknown. Inspect before retrying." });
}
repair_pending();
session.append("source", { code: In.at(-1) });
await run_agent(null, { interactive: true });
