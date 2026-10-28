/** Cell 0: the complete editable Node agent; evaluated as source, not imported by the host. */
// var keeps agent bindings replaceable in the native REPL.
var fs = await import("node:fs");
var path = await import("node:path");
var os = await import("node:os");
var { randomUUID } = await import("node:crypto");
var { AsyncLocalStorage } = await import("node:async_hooks");
var { createInterface } = await import("node:readline");
var { format } = await import("node:util");
var { setTimeout: sleep } = await import("node:timers/promises");

// GPT-6 Astra defaults, matching the Python agent; adjust when changing providers.
var model_options = {
  model: process.env.FIBONA_MODEL ?? "gpt-6-astra",
  max_output_tokens: 128000,
  reasoning: { effort: "medium" },
  include: ["reasoning.encrypted_content"],
};
var context_window = Number(process.env.FIBONA_CONTEXT_TOKENS ?? 1050000);

// Named sections are composed for every request; edits affect future requests.
var instructions = {
  identity: `You are Fibona, a self-modifying agent that evolves through learning, feedback and verified changes.
Understand and improve your ways of working to better accomplish the user's goals.`,
  working: `## Advance the task

Establish the goal, constraints and evidence of completion. Work independently while useful progress
is possible. Inspect, act and verify; distinguish assumptions, intentions, attempts and verified outcomes.
At key steps, compare expected and actual results. When they differ or a required step fails, stop
dependent work and inspect existing observations and current state before continuing or repeating an
action. Let the evidence change your understanding and plan.
Ask when missing information or a decision blocks progress; report unfinished work and uncertainty.
In recovery mode, repair the fault and hand back to work; a handoff alone does not prove repair.`,
  environment: `## Use and maintain your environment

You operate in a persistent Node.js REPL. Your instructions, tools, context policy and work loop,
along with the variables, functions and resources you create, form a working environment you can
inspect, maintain and modify.

Starting points:
- \`instructions\`: your current instructions; changes affect subsequent requests.
- \`context\`: organize context for subsequent model requests.
- \`session\`: append-only records of source, model responses and execution history.
- \`run_cell\`: execute code; the only capability exposed as a model tool.
- \`request_model\`: request a model response from code.

Discover interface details from relevant code and documentation. Source, execution history and
current bindings may disagree, and failures can leave partial changes. Use them together with actual
behavior to understand the current environment.

The agent defined in cell 0 and later task cells share one live global namespace.
Existing functions may read these global bindings when called; a top-level import,
assignment or definition can therefore change their behavior even if that cell succeeds.
For ordinary task work, prefer local scopes and keep persistent task data under a
distinct name, checking that it is not already in use. Before replacing shared bindings
or mutating the objects they refer to, inspect their current values and dependent code,
then follow the self-modification checks below. Preserve session recording, model access
and cell execution unless intentionally changing them.

Make the purpose, dependencies and failure behavior of reusable variables, functions and tools
discoverable. Manage temporary objects, files, processes and connections too; release or clean them
up when no longer needed so they do not keep consuming resources. Check dependencies and preserve
useful work before cleanup; do not blindly clear shared state.

Maintain context suited to the next step, keeping necessary goals, constraints, progress, open
questions and evidence pointers; retrieve details as needed. When changing direction, pausing or
reorganizing context, leave a handoff sufficient to continue. Account for retrieval and recovery
costs when reorganizing, and retain material that still needs direct inspection.

Context changes do not reset the REPL or undo external actions; process restarts do not automatically
restore in-memory objects. Check relevant current state when resuming to avoid repeating actions.
Explicitly persist state and experience that must last, with pointers for retrieval. Keep session
records append-only rather than rewriting historical facts.

Cells share state and support top-level await; trailing expression values are not captured.
Await nested cells sequentially, never concurrently, and finish concurrent I/O before returning.
Failure, retries and code rollback do not automatically undo existing external effects.

Model output and user presentation are separate channels. \`console.log(...)\` writes output that is recorded
and returned to the model; \`display(...)\` presents to the user without automatically entering model
context. Write a value to both channels if both audiences need it. Your ordinary reply text is
shown to the user automatically, including replies alongside tool calls. Use ordinary replies for
progress and final answers; use \`display(...)\` for user-facing content produced inside a cell,
including questions before input(). Captured cell output is feedback for you, not user presentation.

Use var or assignments for editable top-level definitions; const/let redeclarations can conflict.
Import modules with await import(...). Async input() returns null at EOF.`,
  reflection: `## Reflect and iterate

Notice how your ways of working and environment affect results. Treat user corrections, conflicts
with expectations, repeated mistakes, inefficient patterns and resource problems as signals to
reflect; also notice effective approaches worth reusing.

Use history, current state and implementation to investigate causes, distinguishing evidence from
explanation. When evidence is insufficient, use a small check that can distinguish possible causes.
Based on the cause, decide whether to revise your understanding and approach or change tools, state
organization, context policy, instructions or the work loop. Use existing capabilities when sufficient;
otherwise prefer the smallest coherent change. Adding rules is not the default remedy.
Self-modification must preserve the user's constraints and evidence of success.

Before a change, identify the behavior to improve and the evidence for judging improvement. Inspect
affected callers, bindings and mutable state; preserve working definitions and necessary state.
Saved references are not state snapshots. Replacing a definition neither isolates its dependencies
nor changes active invocations. Keep session storage, model requests and cell execution stable by default.
Switch, check the actual usage path including affected callers, and roll back on failure within the
same cell, without another model request or reliance on the changed capability. For effects that
require later evaluation, retain a rollback path and track what remains unverified.

Apply verified lessons to subsequent plans or tool checks, preserving their conditions and evidence
pointers. Check whether they improve results and what costs they add, then retain, revise or revert
them. Do not turn a single outcome into a permanent rule.
Keep durable guidance in prompt sections, mechanisms and interfaces in code/docstrings, and task
progress in working state or history. Prefer discovery pointers over duplicated implementation details;
check the composed prompt for contradictions after edits and remove guidance that no longer applies.`,
};

// Append-only facts. Views are derived, never persisted separately.
// Session storage: append-only facts and read-only history views.
var Session = class Session {
  /** Read records/views without modifying them; they are not protected snapshots.
   * Agent source: session.records.findLast(r => r.type === "source").code
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

// Cell execution: the only model-facing tool; other capabilities are available as JavaScript.
var cell_tool = {
  type: "function", name: "run_cell",
  description: "Execute JavaScript in the persistent REPL and receive output or an error stack.",
  strict: true,
  parameters: { type: "object", properties: { code: { type: "string" } }, required: ["code"], additionalProperties: false },
};
var execute = run_cell; // Preserve the REPL's raw executor before replacing the tool.
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

/** Identify the current mode interval and enclosing recorded cell. */
var record_origin = function record_origin(log) {
  const mode = log.records.findLast(r => r.type === "mode");
  const active = active_cell.getStore();
  return { mode: mode?.name ?? "normal", mode_id: mode?.id ?? null,
    parent_cell_id: active?.log === log ? active.cell_id : null };
};

// Model access: explicit requests, followed by session-aware recording.
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

/** Record one logical request and its outcome against the same session.
 * Response retries and tool execution belong to agent_step; storage failures are not request failures.
 */
var recorded_request = async function recorded_request(items, { attempt }) {
  let response;
  // Snapshot defaults before awaiting; later live edits belong to future requests.
  const requested_model = model_options.model;
  const reasoning = JSON.parse(JSON.stringify(model_options.reasoning ?? null));
  const log = session;
  const request_id = log.append("model_request", {
    model: requested_model, reasoning, max_output_tokens: model_options.max_output_tokens ?? null,
    input_items: items.length, attempt, ...record_origin(log),
  });
  let data;
  try {
    response = await request_model(items);
    data = {
      request_id, response_id: response.id ?? null, model: requested_model, response_model: response.model ?? null,
      reasoning, status: response.status, output: response.output, usage: response.usage ?? null,
      error: response.error ?? null, incomplete_details: response.incomplete_details ?? null,
    };
  } catch (error) {
    log.append("model_request_error", { request_id,
      status: error?.name === "AbortError" ? "interrupted" : "error", error: error?.stack ?? String(error) });
    throw error;
  }
  // Storage failures propagate; they are not model request failures.
  log.append("model_response", data);
  return response;
};

// History projection: shared by normal context and independent recovery.
/** Select a view of the linear log; mode transitions do not reset context. */
var records_in_mode = function* (log, start = 0, mode = null) {
  let current = "normal";
  for (const record of log.records) {
    if (record.type === "mode") current = record.name;
    if (record.id >= start && (mode === null || current === mode)) yield record;
  }
};

/** Project facts into API input; only model-linked cells synthesize tool replies. */
var project_messages = function project_messages(log, start = 0, { max_output_chars = null, mode = null } = {}) {
  const messages = [];
  for (const record of records_in_mode(log, start, mode)) {
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

/** Unanswered calls in the selected mode, with response and cell record indices. */
var pending_calls = function pending_calls(log, mode = null) {
  const start = 0;
  const pending = new Map();
  for (const r of records_in_mode(log, start, mode)) {
    if (r.type === "model_response" && r.status === "completed") {
      for (const item of r.output) if (item.type === "function_call") pending.set(item.call_id, { response_id: r.id, cell_id: null });
    } else if (r.type === "cell_start" && pending.has(r.call_id)) pending.get(r.call_id).cell_id = r.id;
    else if (r.type === "cell_end") pending.delete(log.records[r.cell_id].call_id);
    else if (r.type === "tool_error") pending.delete(r.call_id);
  }
  return pending;
};
// Context: a bounded model-input view over a complete session log.
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
    return [...items, ...project_messages(this.log, boundary?.first_kept ?? 0, { max_output_chars: this.max_output_chars, mode: "normal" })];
  }

  /** Append a boundary; preserve unanswered tool batches, history, live state and execution mode. */
  reset(handoff = null, { cause_id = null } = {}) {
    if (handoff !== null && typeof handoff !== "string") throw new TypeError("handoff must be text or null");
    const pending = pending_calls(this.log, "normal");
    const first_kept = Math.min(this.log.records.length, ...[...pending.values()].map(p => p.response_id));
    return this.log.append("context_reset", { first_kept, handoff, cause_id, ...record_origin(this.log) });
  }

  /** UTF-8 heuristic with a floor from measured usage; not a tokenizer. */
  estimate(items) {
    let estimate = estimate_tokens([Object.entries(instructions).map(([name, text]) => `# ${name}\n${text}`).join("\n\n"), cell_tool, items]);
    const normal = new Set(records_in_mode(this.log, 0, "normal"));
    const last = this.log.records.findLast(r => r.type === "context_reset" || (r.type === "model_response" && r.usage && normal.has(r)));
    if (last?.type === "model_response") {
      estimate = Math.max(estimate, last.usage.input_tokens + estimate_tokens(project_messages(this.log, last.id, { max_output_chars: this.max_output_chars, mode: "normal" })));
    }
    return estimate;
  }
};

// Tool completion: close interrupted calls without replaying their effects.
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

// Agent step: retry unsuccessful responses, then dispatch complete tool calls.
/** Make at most three response attempts; exhausted failures raise for recovery.
 * Partial output is recorded but never executed or replayed. */
var agent_step = async function agent_step(items) {
  try {
    const log = session;
    let response;
    for (let attempt = 0; attempt < 3; attempt++) {
      response = await recorded_request(items, { attempt: attempt + 1 });
      const status = response.status;
      if (status === "completed") break;
      const code = response.error?.code ?? null;
      const reason = response.error?.message ?? response.incomplete_details?.reason;
      const retryable = status === "incomplete" || (status === "failed"
        && [null, "server_error", "rate_limit_exceeded", "timeout", "overloaded"].includes(code));
      if (!retryable || attempt === 2) {
        throw new Error(`Model response ${status} (${code ?? "unspecified"}): ${reason ?? "No details"}`);
      }
      const delay = status === "failed" ? 2 ** attempt : 0;
      log.append("model_retry", { response_id: response.id ?? null, attempt: attempt + 2,
        reason: status, error_code: code, delay_seconds: delay });
      if (delay) await sleep(delay * 1000);
    }
    for (const item of response.output) {
      if (item.type !== "message") continue;
      for (const part of item.content) {
        if (part.type === "output_text" || part.type === "refusal") {
          console_write((part.type === "output_text" ? part.text : part.refusal) + "\n");
        }
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

// Normal work: context selection and budget checks around the shared agent step.
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

// Recovery: independent context, shared agent step, and a local fallback.
/** Repair independently; reset normal context on failure. Cancellation and fallback failures propagate. */
var recover = async function recover(reason) {
  const start = session.records.length;
  const note = `Recovery mode. Full failure: session.records[${start}].reason. Reason (may be truncated): ${reason.slice(-1000)}\n`
    + `You share the same live REPL and session. Original task and progress are in session.records.slice(0, ${start}). `
    + "Read history in small chunks and inspect current state before acting. Repair or roll back the fault; keep the core capabilities unchanged. "
    + "Do not replay completed effects or execute the original task here. Do not invoke the work loop during repair. "
    + "The main context is paused, not reset. Change it or leave continuation information only as needed for the repair. "
    + "Verify what you can, then finish your response when ready to resume. The agent will try the repaired work loop next.";
  session.append("mode", { name: "recovery", reason, content: note });
  let status = "failed";
  try {
    let recovery_error = null;
    try {
      do {
        status = "failed";
        status = await agent_step([...project_messages(session, start + 1), { role: "developer", content: note }]);
      } while (status === "continue");
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      recovery_error = error?.stack ?? String(error);
    }
    if (status !== "completed") {
      const failure = session.append("recovery_error", {
        recovery: start, reason, recovery_error: recovery_error ?? "Recovery did not complete.",
      });
      // Rebuild rather than call a possibly damaged Context instance.
      context = new Context(session);
      context.reset(`Recovery failed; use this fresh context to repair and continue the original task. `
        + `Failure details: session.records[${failure}]. Prior task and progress: session.records.slice(0, ${start}). `
        + "The REPL and external effects are unchanged. Inspect history and live state before acting; "
        + "do not repeat completed effects. Repair what is needed, then continue the user's task.", { cause_id: failure });
    }
  } finally {
    session.append("mode", { name: "normal", recovery: start, status });
  }
};

// Conversation entry: user input, mode entry and reader lifetime.
// Interaction is agent-owned. Create the iterator before any await so piped lines are retained.
var console_write = process.stdout.write.bind(process.stdout);
// User-facing text is recorded separately and never projected into model input.
var display = (...values) => {
  const active = active_cell.getStore();
  const log = active?.log ?? session;
  const text = format(...values) + "\n";
  log.append("display", { cell_id: active?.cell_id ?? null, text });
  return console_write(text);
};
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

/** Resume normal work after recovery or reset; each new loop failure starts fresh recovery. */
var run_agent = async function run_agent(user_input = null, { interactive = false } = {}) {
  try {
    while (true) {
      if (user_input === null) user_input = await input("You: ");
      if (user_input === null) return;
      if (user_input.trim()) {
        session.append("mode", { name: "normal" });
        session.append("message", { message: { role: "user", content: user_input } });
        while (true) {
          try { await agent_loop(); break; }
          catch (error) {
            if (error?.name === "AbortError") throw error;
            await recover(error?.stack ?? String(error));
          }
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

// Initialize agent state once, after all definitions are available.
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
