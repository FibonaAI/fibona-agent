/** Cell 0: the complete editable Node agent; evaluated as source, not imported by the host. */
// var keeps agent bindings replaceable in the native REPL.
var fs = await import("node:fs");
var path = await import("node:path");
var os = await import("node:os");
var { randomUUID } = await import("node:crypto");
var { AsyncLocalStorage } = await import("node:async_hooks");
var { createInterface } = await import("node:readline");
var { format } = await import("node:util");
var { setTimeout: wait_timer } = await import("node:timers/promises");

// GPT-6 Astra defaults, matching the Python agent; adjust when changing providers.
var model_options = {
  model: process.env.FIBONA_MODEL ?? "gpt-6-astra",
  max_output_tokens: 128000,
  reasoning: { effort: "medium" },
  include: ["reasoning.encrypted_content"],
};
var context_window = Number(process.env.FIBONA_CONTEXT_TOKENS ?? 1050000);
if (!Number.isFinite(context_window) || !(model_options.max_output_tokens > 0 && model_options.max_output_tokens < context_window)) {
  throw new Error("Require 0 < max output tokens < context window");
}
if (!process.env.OPENAI_API_KEY) throw new Error("OPENAI_API_KEY is required; set it in .env or the environment");
var cell_timeout = 300;

// Named sections are composed for every request; edits affect future requests.
var instructions = {
  identity: `You are Fibona, an agent capable of understanding and modifying itself.
Improve how you accomplish the user's goals through action, learning and verified changes.`,
  working: `## Advance the task

Understand the user's goal, constraints and completion criteria, and organize work around them.
Act independently while useful progress is possible; seek clarification when a consequential gap
in information or decisions blocks progress.

Choose actions that advance the user's goal or resolve uncertainty that blocks progress. Carry the task
through to completion, comparing expectations with results and revising your approach. Keep the original
acceptance criteria and artifact constraints explicit; proxy measurements diagnose progress without
replacing them. Use checks that distinguish explanations and challenge assumptions shared by the
implementation and its tests. When a necessary premise fails, inspect before advancing dependent work.

Use observed execution time, expected scale and repetition to judge cost. Before repeating expensive
work, identify what changed or what variability the repetition measures. Reuse unchanged deterministic
results and stable reference measurements when appropriate. If experiments stop producing useful
evidence, preserve progress and reconsider the hypothesis, method and remaining budget.

Verify the actual deliverable against the goal; finishing a procedure does not establish success.
Preserve useful progress, distinguish assumptions, attempts and verified results, and report
unfinished work and uncertainty honestly.`,
  environment: `## Understand yourself and your environment

You operate in a persistent Node.js REPL. Your instructions, model access, tools, state management
and work loop are themselves inspectable and editable code in this environment. The complete agent
source is available in the first cell and execution history; instructions holds current guidance,
and request_model provides model access from code.

You share a runtime with task code. Definitions, objects and resources can persist, and local changes
can affect other components through shared state and dependencies. Distinguish the agent's runtime
and dependencies from the task's execution environment before changing installations or relying on
an import check. Understand how the parts cooperate and where and when a change takes effect:
definitions, retained references and active invocations have different lifetimes. Blocking or
terminating this process also affects you.

Use source, history and actual behavior together to understand the current environment. They describe
implementation, experience and present state; none substitutes for the others. Ground your use,
maintenance and self-modification in that understanding.`,
  run_cell: `## Act through code

run_cell is your sole model-facing tool for executing code. Use it to observe, act and modify the
environment; inspect its interface and implementation for execution and output details.
When you have completed the user's task and checked the result, call
end_turn(message) as the last action in a cell, with your final response
to the user. Use ordinary replies for progress updates while continuing work.

In recovery mode, call end_turn(message) when the repair is complete
and normal work can resume.

Organize each cell around a clear intent, making dependencies, effects and results easy to understand
and verify. Reuse retained data, indexes and helpers; group related observations when their results
can be interpreted together. Prefer local scopes for temporary work and check existing bindings
before naming persistent task state. Balance simplicity, reuse and the needs of the current task.

Make important results observable, long-running work inspectable and the state after failure
understandable. Use a representative small run to estimate expensive work. For work that may block
the shared runtime, use available supervised execution capabilities; a cell deadline alone does not
guarantee interruption. After timeout, inspect effects and revise the scale, method or execution
boundary before repeating work. A call ending does not establish that all its effects have ended;
failure, retry and rollback do not return to the starting point. Release resources when no longer
needed; check dependencies and preserve useful work before cleanup.

Distinguish observations for yourself from communication to the user. Captured cell output feeds
the model; ordinary model replies reach the user. Use the appropriate tool for user-facing content
produced inside a cell. Select necessary output deliberately; return values are not automatically conveyed.`,
  session: `## Use historical evidence

session records the course of work. Use it to trace actions and results, understand how a problem
arose and retrieve evidence needed to continue.

Distinguish past observations, current interpretations and present facts. History supports judgment
but does not replace checking current state, and must not be rewritten to fit a new interpretation.

Retrieve relevant experience according to the question at hand. Preserve relationships between evidence
so that conclusions and improvements remain traceable to their basis.`,
  context: `## Maintain working understanding

context selects information for subsequent model requests; task_state holds editable task state.
Actively organize both so that limited attention serves the current goal.

Keep goals, constraints, progress and important unknowns clear. Revise stale information, consolidate
understanding and retain retrieval pointers for details not currently needed, rather than accumulating
content indefinitely. Consider the cost of recovering information when deciding what to retain.

Information stored in the environment is not automatically model input; inspect and select it according
to the next step. Preserve continuity when reorganizing context, distinguishing currently visible input,
live runtime state and persisted evidence. Context reset does not reset the environment; process restart
does not restore live objects. Save and load explicitly for continuity across restarts. Preserve goals,
constraints and progress through transitions, including recovery, and check current state when resuming.`,
  tool: `## Build and maintain capabilities

Use tool_catalog to discover existing tools. Read their implementation and documentation to understand
purpose, conditions and effects before choosing how to use them. The catalog indexes capabilities;
actual capabilities come from current implementations.

Turn methods worth reusing into tools with clear responsibilities that are easy to understand and verify.
Document and verify actual use before adding them to the catalog; one-off work need not become a tool.

As tools change, maintain their documentation and catalog entries and check real callers so discoverable
capabilities match actual behavior. Preserve working definitions and entries for replacement, restore both
if verification fails, and check callers when removing tools. These are your practices, not a workflow
enforced by the catalog. Judge reuse by both task outcomes and maintenance cost.`,
  reflection: `## Learn and modify yourself

Notice differences between expectations and results, including user corrections, and recognize effective
methods worth reusing. Investigate causes from evidence, distinguishing incidental outcomes, conditions
of applicability and lessons that generalize.

Improvement may concern understanding, approach, tools, state organization, instructions or the work loop.
Choose changes proportionate to the cause; neither additional rules nor self-modification are goals in
themselves. Preserve the user's goals, constraints and criteria for success while improving how you work.

Before changing something, identify the intended improvement, how to judge it and the affected dependencies.
When testing an uncertain improvement, choose an experiment that can test the hypothesis and limit its effects.
Keep a recovery path independent of the capability being changed, covering necessary state as well as
old definitions: saved references are not state snapshots. When changing capabilities needed to keep
running, such as session, context, model access or cell execution, replace and verify through real callers
within the same cell, rolling back immediately on failure. Verification and rollback must not depend on
a later model request, another cell or the changed capability. Track effects requiring later evaluation,
but verify the ability to continue before returning control. Decide whether to retain, revise or revert
based on benefits, costs and unresolved issues.

Apply lessons to subsequent actions and test them again. Keep stable principles in instructions,
mechanisms and interfaces in implementation, and task progress in working state. Preserve conditions
and evidence for lessons, and remove repeated, contradictory or obsolete guidance.`,
};

// Append-only facts. Views are derived, never persisted separately.
// Session storage: append-only facts and read-only history views.
var RecordingError = class RecordingError extends Error {};
var Session = class Session {
  /** Treat records and views as read-only; they are not protected snapshots.
   * Agent source: session.records.findLast(r => r.type === "source").code
   * Later definitions: session.cells, checked against live bindings.
   */
  constructor(file = null) {
    this.path = file ? path.resolve(file.replace(/^~(?=\/|$)/, os.homedir())) : null;
    this.records = [];
    this.finished = new Set(); // `${field}:${start ID}` keys that already have a terminal outcome.
    this.resumed = false;
    this.broken = false;
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
        for (const key of this.validate(record)) this.finished.add(key);
        this.records.push(record);
        if (newline < 0) fs.appendFileSync(this.path, "\n");
        offset = end;
      }
      this.resumed = Boolean(this.records.length);
    }
    if (!this.records.length) this.append("session", { version: 4, session_id: randomUUID() });
  }

  /** Check a loaded or new record; return terminal keys to add once it is stored. */
  validate(record) {
    if (!record || !Number.isInteger(record.id) || record.id !== this.records.length || typeof record.type !== "string") {
      throw new Error("Session record IDs must be consecutive and have a type");
    }
    if (!this.records.length && (record.type !== "session" || record.version !== 4)) throw new Error("Expected a version 4 JSONL session; old snapshots are not modified");
    if (this.records.length && record.type === "session") throw new Error("Duplicate session header");
    const finished = [];
    for (const [field, target, terminals] of [["cell_id", "cell_start", ["cell_end"]], ["request_id", "model_request", ["model_response", "model_request_error"]]]) {
      const ref = record[field];
      if (ref == null) continue;
      if (!Number.isInteger(ref) || this.records[ref]?.type !== target) throw new Error("Invalid record association");
      if (!terminals.includes(record.type)) continue;
      if (this.finished.has(`${field}:${ref}`)) throw new Error("Duplicate terminal outcome");
      finished.push(`${field}:${ref}`);
    }
    if (record.type === "cell_end" && record.cell_id == null) throw new Error("Missing cell start association");
    if (record.type === "model_response" && record.accepted !== false && record.status === "completed") validate_output(record.output);
    if (record.type === "model_response" && record.accepted === true && record.status !== "completed") throw new Error("Incomplete response cannot be accepted");
    return finished;
  }

  append(type, data = {}) {
    if (this.broken) throw new RecordingError("Session requires reload after a failed write");
    const encoded = JSON.stringify({ ...data, id: this.records.length, type, time: new Date().toISOString() }) + "\n";
    const record = JSON.parse(encoded);
    const finished = this.validate(record);
    try {
      if (this.path) {
        fs.mkdirSync(path.dirname(this.path), { recursive: true });
        fs.appendFileSync(this.path, encoded, { mode: 0o600 });
      }
      this.records.push(record);
      for (const key of finished) this.finished.add(key);
      return record.id;
    } catch (error) {
      this.broken = true;
      throw new RecordingError(error.message, { cause: error });
    }
  }

  get messages() {
    return this.records.flatMap(r => r.type === "message" ? [r.message]
      : r.type === "model_response" && r.status === "completed" && r.accepted !== false ? r.output : []);
  }

  get cells() {
    const cells = new Map();
    for (const r of this.records) {
      if (r.type === "cell_start") {
        cells.set(r.id, { cell_id: r.id, parent_cell_id: r.parent_cell_id, call_id: r.call_id,
          code: r.code, output: "", error: null, status: "running", timeout_seconds: r.timeout ?? null, elapsed_seconds: null });
      } else if (r.type === "cell_end") {
        Object.assign(cells.get(r.cell_id), { output: r.output, error: r.error, status: r.status, elapsed_seconds: r.elapsed_seconds ?? null });
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
  description: "Execute JavaScript with top-level await in the persistent REPL. Log observations explicitly; trailing expression values are not captured. Await nested cells sequentially, never concurrently. Returns captured output or an error stack.",
  strict: true,
  parameters: { type: "object", properties: { code: { type: "string" }, timeout: { type: ["number", "null"] } }, required: ["code", "timeout"], additionalProperties: false },
};
var execute = run_cell; // Preserve the REPL's raw executor before replacing the tool.
var cell_execution = { scope: new AsyncLocalStorage(), active: undefined };
var active_cell = new AsyncLocalStorage();
var operation = new AsyncLocalStorage();
var CellTimeoutError = class CellTimeoutError extends Error {
  constructor(frame) { super("Cell deadline expired; partial effects remain"); this.frame = frame; }
};
var resolve_cell_timeout = value => {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) throw new TypeError("timeout must be finite and positive");
  return value;
};
/** Execute and record a cell, returning metadata, captured output, error and status.
 * Use var or assignments for editable top-level definitions; const/let redeclarations can conflict.
 * Import modules with await import(...). Trailing expression values are not captured.
 * Await nested cells sequentially; finish concurrent I/O before returning. Leave call_id unset
 * for direct/nested calls. timeout is positive seconds, defaulting to cell_timeout when null.
 * Cancellation is cooperative: blocking code and non-cooperative promises cannot be forcibly
 * interrupted. Ownership and capture remain active until evaluation exits. No effects are undone.
 * elapsed_seconds measures execution through capture/deadline cleanup, excluding session writes;
 * timeout_seconds is the selected budget, even when a parent deadline is sooner.
 * Ordinary errors become results; external/parent cancellation and storage failures propagate.
 */
var run_cell = async function run_cell(code, { call_id = null, timeout = null, signal = null } = {}) {
  if (typeof code !== "string" || !code.trim()) throw new TypeError("code must be a non-empty string");
  const seconds = resolve_cell_timeout(timeout ?? cell_timeout);
  signal?.throwIfAborted();
  const execution = cell_execution, previous = execution.active;
  if (previous !== execution.scope.getStore()) throw new Error("Concurrent cells are not supported; await run_cell directly");
  const owner = {};
  execution.active = owner;
  try {
    return await execution.scope.run(owner, async () => {
      const scope = operation.getStore();
      const log = call_id !== null && scope ? scope.log : session, active = active_cell;
      const parent = active.getStore();
      parent?.signal.throwIfAborted();
      const parent_cell_id = parent?.log === log ? parent.cell_id : null;
      const cell_id = log.append("cell_start", { code, call_id, timeout: seconds, ...record_origin(log), parent_cell_id });
      if (call_id !== null && scope?.call_id === call_id) scope.cell_id = cell_id;
      const now = performance.now.bind(performance);
      const started = now();
      const controller = new AbortController();
      const signals = [controller.signal, parent?.signal, signal].filter(Boolean);
      const expires = started + seconds * 1000;
      const frame = { log, cell_id, output: "", signal: AbortSignal.any(signals), deadline: Math.min(expires, parent?.deadline ?? Infinity) };
      frame.deadline_owner = parent && parent.deadline <= expires ? parent.deadline_owner : frame;
      let timer;
      const expire = () => {
        const remaining = expires - now();
        if (remaining <= 0) controller.abort(new CellTimeoutError(frame));
        else timer = setTimeout(expire, Math.min(remaining, 2147483647));
      };
      expire();
      let status = "completed", error = null, elapsed_seconds;
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
        frame.signal.throwIfAborted();
        if (now() >= frame.deadline) throw new CellTimeoutError(frame.deadline_owner);
      } catch (exc) {
        const failure = frame.signal.aborted ? frame.signal.reason : exc;
        // Only this cell's signal marks cancellation; a task-local AbortError is an ordinary error.
        status = (frame.signal.aborted && !(failure instanceof CellTimeoutError)) || (failure instanceof CellTimeoutError && failure.frame !== frame) ? "interrupted" : "error";
        error = failure?.stack ?? String(failure);
        if (failure instanceof RecordingError || failure instanceof ModelStopError || status === "interrupted" || (failure instanceof CellTimeoutError && failure.frame !== frame)) throw failure;
      } finally {
        clearTimeout(timer);
        stdout.write = outWrite;
        stderr.write = errWrite;
        elapsed_seconds = (now() - started) / 1000;
        log.append("cell_end", { cell_id, output: frame.output, error, status, elapsed_seconds });
      }
      return { cell_id, parent_cell_id, call_id, code, output: frame.output, error, status, elapsed_seconds, timeout_seconds: seconds };
    });
  } finally {
    execution.active = previous;
  }
};

var record_origin = function record_origin(log) {
  const scope = operation.getStore();
  const mode = log.records.findLast(r => r.type === "mode");
  const active = active_cell.getStore();
  return { mode: scope?.log === log ? scope.mode : mode?.name ?? "normal",
    mode_id: scope?.log === log ? scope.mode_id : mode?.id ?? null,
    parent_cell_id: active?.log === log ? active.cell_id : null };
};

var validate_output = function validate_output(output) {
  if (!Array.isArray(output) || output.some(item => !item || typeof item.type !== "string")) throw new Error("Malformed response output");
  const seen = new Set();
  for (const item of output) if (item.type === "function_call") {
    if (typeof item.call_id !== "string" || !item.call_id || seen.has(item.call_id)) throw new Error("Missing or ambiguous call identity");
    seen.add(item.call_id);
  }
  return seen;
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
    body: JSON.stringify(settings), signal: cell_signal() ? AbortSignal.any([cell_signal(), AbortSignal.timeout(600000)]) : AbortSignal.timeout(600000),
  });
  if (!response.ok) {
    const body = await response.text();
    const error = new Error(`Model HTTP ${response.status}: ${body}`);
    try { const parsed = JSON.parse(body); error.code = parsed.error?.code ?? parsed.code; } catch { /* Non-JSON provider error. */ }
    error.status_code = response.status;
    throw error;
  }
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
  const scope = operation.getStore();
  const log = scope?.log ?? session;
  const origin = record_origin(log);
  const request_id = log.append("model_request", {
    model: requested_model, reasoning, max_output_tokens: model_options.max_output_tokens ?? null,
    tool_choice: { type: "function", name: "run_cell" }, parallel_tool_calls: false,
    input_items: items.length, attempt, ...origin,
  });
  let data;
  try {
    response = await request_model(JSON.parse(JSON.stringify(items)), {
      tool_choice: { type: "function", name: "run_cell" }, parallel_tool_calls: false,
    });
    data = {
      request_id, response_id: response.id ?? null, model: requested_model, response_model: response.model ?? null,
      reasoning, status: response.status, output: response.output, usage: response.usage ?? null,
      error: response.error ?? null, incomplete_details: response.incomplete_details ?? null,
    };
  } catch (error) {
    log.append("model_request_error", { request_id,
      status: error?.name === "AbortError" ? "interrupted" : "error", error: error?.stack ?? String(error) });
    const permanent = error?.status_code >= 400 && error.status_code < 500 && ![408, 409, 429].includes(error.status_code) && error.code !== "context_length_exceeded";
    // Credentials, permissions and missing models are beyond model repair. Other
    // rejections may come from broken input, so only recovery's own rejection stops.
    if (permanent && ([401, 403, 404].includes(error.status_code) || origin.mode === "recovery")) {
      throw new ModelStopError(`Permanent model HTTP rejection: ${error.status_code}`, { cause: error });
    }
    throw error;
  }
  let invalid = null;
  try {
    if (data.status === "completed") {
      const identities = validate_output(data.output);
      const pending = pending_calls(log);
      if ([...identities].some(id => pending.has(id))) throw new Error("Call identity is already pending");
    } else if (!["incomplete", "failed"].includes(data.status)) throw new Error("Malformed response status");
  } catch (error) { invalid = error; }
  const response_id = log.append("model_response", { ...data, ...origin, accepted: data.status === "completed" && !invalid });
  if (scope) scope.response_id = response_id;
  if (invalid) throw invalid;
  return response;
};

// History projection: shared by normal context and independent recovery.
/** Select records by owning mode; mode transitions do not reset context. */
var records_in_mode = function* (log, start = 0, mode = null) {
  let current = "normal";
  const owners = new Map();
  for (const record of log.records) {
    if (record.type === "mode") current = record.name;
    let owner = record.mode ?? current;
    const reference = ["model_response", "model_request_error"].includes(record.type) ? record.request_id
      : record.type === "cell_end" ? record.cell_id : record.type === "tool_error" ? record.response_id : null;
    if (owners.has(reference)) owner = owners.get(reference);
    owners.set(record.id, owner);
    if (record.id >= start && (mode === null || owner === mode)) yield record;
  }
};

/** Project facts into API input; only model-linked cells synthesize tool replies. */
var project_messages = function project_messages(log, start = 0, { max_output_chars = null, mode = null } = {}) {
  const messages = [];
  const records = log.records;
  for (const record of records_in_mode(log, start, mode)) {
    if (record.type === "message") messages.push({ ...record.message });
    else if (record.type === "model_response" && record.status === "completed" && record.accepted !== false) messages.push(...record.output.map(item => ({ ...item })));
    else if (record.type === "turn_end") messages.push({ role: "assistant", content: record.message });
    else if (record.type === "mode" && record.content) messages.push({ role: "developer", content: record.content });
    else if (["cell_end", "tool_error"].includes(record.type)) {
      const call_id = record.type === "cell_end" ? records[record.cell_id].call_id : record.call_id;
      if (call_id === null) continue;
      const fields = record.type === "cell_end" ? ["cell_id", "output", "error", "status"] : ["error"];
      const result = Object.fromEntries(fields.map(key => [key, record[key]]));
      if (record.type === "cell_end") {
        result.elapsed_seconds = record.elapsed_seconds ?? null;
        result.timeout_seconds = records[record.cell_id].timeout ?? null;
      }
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
  return JSON.parse(JSON.stringify(messages));
};

/** Unanswered calls in the selected mode, with response and cell record indices. */
var pending_calls = function pending_calls(log, mode = null) {
  const start = 0;
  const pending = new Map();
  const records = log.records;
  for (const r of records_in_mode(log, start, mode)) {
    if (r.type === "model_response" && r.status === "completed" && r.accepted !== false) {
      for (const item of r.output) if (item.type === "function_call") pending.set(item.call_id, { response_id: r.id, cell_id: null });
    } else if (r.type === "cell_start" && pending.has(r.call_id)) pending.get(r.call_id).cell_id = r.id;
    else if (r.type === "cell_end") pending.delete(records[r.cell_id].call_id);
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
    const normal = new Set([...records_in_mode(this.log, 0, "normal")].map(r => r.id));
    const last = this.log.records.findLast(r => r.type === "context_reset" || (r.type === "model_response" && r.usage && normal.has(r.id)));
    if (last?.type === "model_response" && typeof last.usage?.input_tokens === "number" && Number.isFinite(last.usage.input_tokens) && last.usage.input_tokens >= 0) {
      estimate = Math.max(estimate, last.usage.input_tokens + estimate_tokens(project_messages(this.log, last.id, { max_output_chars: this.max_output_chars, mode: "normal" })));
    }
    return estimate;
  }
};

// Tool completion: close interrupted calls without replaying their effects.
/** Close interrupted calls without replaying effects or closing live enclosing cells. */
var repair_pending = function repair_pending(log = null, response_id = null) {
  const scope = operation.getStore();
  log ??= scope?.log ?? session;
  if (response_id === null && scope) {
    response_id = scope.response_id;
    if (response_id == null) return;
  }
  let active = active_cell.getStore();
  const live = new Set();
  const records = log.records;
  while (active?.log === log) {
    live.add(active.cell_id);
    const parent = records[active.cell_id].parent_cell_id;
    active = parent === null ? null : { log, cell_id: parent };
  }
  for (const [call_id, pending] of pending_calls(log)) {
    if (live.has(pending.cell_id) || (response_id !== null && pending.response_id !== response_id)) continue;
    const error = "Execution interrupted; effects unknown. Inspect before retrying.";
    if (pending.cell_id === null) log.append("tool_error", { call_id, response_id: pending.response_id, error });
    else log.append("cell_end", { cell_id: pending.cell_id, status: "interrupted", output: "", error });
  }
};

// Explicit invocation endings and provider stops are distinct from process exit.
/** Stop the current turn without recovery: refusal, unrepairable rejection or repeated failure. */
var ModelStopError = class ModelStopError extends Error {};
/** Call once as the last action of the directly dispatched model cell.
 * Its nonblank message commits only after successful cell-end recording.
 * Recovery/nested model steps end themselves; direct nested cells cannot end a parent. */
var end_turn = function end_turn(message) {
  const scope = operation.getStore(), active = active_cell.getStore();
  if (!scope || !active || active.log !== scope.log || scope.call_id == null
      || active.cell_id !== scope.cell_id) {
    throw new Error("end_turn requires a directly dispatched model cell");
  }
  if (typeof message !== "string" || !message.trim()) throw new TypeError("end_turn requires a nonblank message");
  if (scope.end_message != null) throw new Error("end_turn may be requested only once per cell");
  scope.end_message = message;
};

/** Three response attempts cover transient failures and no-call continuations.
 * Only a successful end_turn cell completes. Refusal stops; exhausted or non-retryable
 * failures enter recovery, where a non-retryable failure stops instead of cycling. */
var agent_step = async function agent_step(items) {
  const log = operation.getStore()?.log ?? session;
  const scope = { log, ...record_origin(log), response_id: null };
  return operation.run(scope, async () => {
    try {
      let response, calls;
      for (let attempt = 0; attempt < 3; attempt++) {
        response = await recorded_request(items, { attempt: attempt + 1 });
        let code, reason, retryReason, retryable;
        const status = response.status;
        if (status === "completed") {
          let refused = false;
          for (const item of response.output) {
            if (item.type !== "message") continue;
            for (const part of item.content) {
              if (part.type === "output_text" || part.type === "refusal") {
                console_write((part.type === "output_text" ? part.text : part.refusal) + "\n");
                refused ||= part.type === "refusal";
              }
            }
          }
          if (refused) {
            log.append("model_stop", { response_id: scope.response_id, reason: "refusal" });
            throw new ModelStopError("Model refused the request");
          }
          calls = response.output.filter(item => item.type === "function_call");
          if (calls.length) break;
          items = [...items, ...JSON.parse(JSON.stringify(log.records[scope.response_id].output)), { role: "developer",
            content: "No tool call was returned. Continue through run_cell. To finish this invocation, call end_turn(message) in a successful cell; ordinary text does not end it." }];
          code = null;
          reason = "No tool call was returned";
          retryReason = "no_tool_calls";
          retryable = true;
        } else {
          code = response.error?.code ?? null;
          reason = response.error?.message ?? response.incomplete_details?.reason;
          retryReason = status;
          retryable = status === "incomplete" || (status === "failed"
            && [null, "server_error", "rate_limit_exceeded", "timeout", "overloaded"].includes(code));
        }
        const failure = `Model response ${status} (${code ?? "unspecified"}): ${reason ?? "No details"}`;
        if (!retryable && scope.mode === "recovery") {
          log.append("model_stop", { response_id: scope.response_id, reason: "permanent_failure" });
          throw new ModelStopError(failure);
        }
        if (!retryable || attempt === 2) throw new Error(failure);
        const delay = status === "failed" ? 2 ** attempt : 0;
        log.append("model_retry", { response_id: response.id ?? null, attempt: attempt + 2,
          reason: retryReason, error_code: code, delay_seconds: delay });
        if (delay) await sleep(delay * 1000);
      }
      for (const [index, call] of calls.entries()) {
        let code, timeout;
        try {
          if (call.name !== "run_cell") throw new Error(`Unknown tool: ${call.name}`);
          const args = JSON.parse(call.arguments);
          if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("arguments must be an object");
          if (Object.keys(args).some(key => !["code", "timeout"].includes(key))) throw new Error("arguments may contain only code and timeout");
          timeout = resolve_cell_timeout(args.timeout ?? cell_timeout);
          code = args.code;
          if (typeof code !== "string" || !code.trim()) throw new Error("code must be a non-empty string");
        } catch (error) {
          log.append("tool_error", { call_id: call.call_id, response_id: scope.response_id, error: error.message });
          continue;
        }
        scope.call_id = call.call_id; scope.cell_id = scope.end_message = null;
        const result = await run_cell(code, { call_id: call.call_id, timeout });
        const message = scope.end_message;
        scope.call_id = scope.cell_id = scope.end_message = null;
        if (result.status === "completed" && message !== null) {
          for (const skipped of calls.slice(index + 1)) log.append("tool_error", {
            call_id: skipped.call_id, response_id: scope.response_id,
            error: "Not executed: end_turn completed this invocation.",
          });
          log.append("turn_end", { cell_id: result.cell_id, response_id: scope.response_id, message, ...record_origin(log) });
          console_write(message + "\n");
          return "completed";
        }
      }
      return "continue";
    } finally {
      if (!log.broken && scope.response_id !== null) repair_pending(log, scope.response_id);
    }
  });
};

// Normal work: context selection and budget checks around the shared agent step.
var agent_loop = async function agent_loop() {
  while (true) {
    if (context.log !== undefined && context.log !== session) context = new Context(session);
    const items = context.build();
    const tokens = context.estimate(items);
    const budget = context_window - model_options.max_output_tokens;
    if (!Number.isFinite(budget) || budget <= 0 || tokens > budget) throw new Error("Context exceeds the input budget. Start fresh context with context.reset(handoff) before continuing work.");
    if (tokens >= budget * 0.8) {
      const warning = { role: "developer", content: "Context is near its input budget. Write a concise handoff and call context.reset(handoff) when appropriate; no automatic summary is generated." };
      if (tokens + estimate_tokens([warning]) > budget) throw new Error("Context including guidance exceeds the input budget");
      items.push(warning);
    }
    if (await agent_step(items) !== "continue") return;
  }
};

// Recovery: independent context, shared agent step, and a local fallback.
/** Repair independently; reset normal context on failure. Returns "completed" or "failed" (after the
 * fresh-context fallback). Cancellation and fallback failures propagate. */
var recover = async function recover(reason) {
  const log = session;
  const start = log.records.length;
  const note = `Recovery mode. Full failure: session.records[${start}].reason. Reason (may be truncated): ${reason.slice(-1000)}\n`
    + `You share the same live REPL and session. Original task and progress are in session.records.slice(0, ${start}). `
    + "Read history in small chunks and inspect current state before acting. Repair or roll back the fault; keep the core capabilities unchanged. "
    + "Do not replay completed effects or execute the original task here. Do not invoke the work loop during repair. "
    + "The main context is paused, not reset. Change it or leave continuation information only as needed for the repair. "
    + "Verify what you can, then call end_turn(message) in a successful cell when ready to resume. The agent will try the repaired work loop next.";
  log.append("mode", { name: "recovery", reason, content: note });
  const status = await operation.run({ log, mode: "recovery", mode_id: start }, async () => {
    let status = "failed";
    try {
      let recovery_error = null;
      try {
        do {
          status = "failed";
          status = await agent_step([...project_messages(log, start + 1, { mode: "recovery" }), { role: "developer", content: note }]);
        } while (status === "continue");
      } catch (error) {
        if (error?.name === "AbortError" || error instanceof RecordingError || error instanceof ModelStopError || error instanceof CellTimeoutError || cell_signal()?.aborted) throw error;
        recovery_error = error?.stack ?? String(error);
      }
      if (status !== "completed") {
        const failure = log.append("recovery_error", {
          recovery: start, reason, recovery_error: recovery_error ?? "Recovery did not complete.",
        });
        // Rebuild rather than call a possibly damaged Context instance.
        context = new Context(session);
        context.reset(`Recovery failed; use this fresh context to repair and continue the original task. `
          + `Failure details: origin session ${log.records[0].session_id} (path ${log.path}), record ${failure}. `
          + `Prior task and progress precede record ${start} in that session. `
          + "The REPL and external effects are unchanged. Inspect history and live state before acting; "
          + "do not repeat completed effects. Repair what is needed, then continue the user's task.", { cause_id: session === log ? failure : null });
      }
    } finally {
      if (!log.broken) log.append("mode", { name: "normal", recovery: start, status });
    }
    return status;
  });
  if (context.log !== undefined && context.log !== session) context = new Context(session);
  return status;
};

// Local tools: ordinary callables for cell code, separate from execution/recording policy.
// Built-in helpers live here; the catalog can also describe functions defined in cells or loaded files.
/** Current cell AbortSignal, or undefined outside a cell. Pass to abortable I/O; no side effects. */
var cell_signal = () => active_cell.getStore()?.signal;
/** Await milliseconds and return value; options follow timers/promises.
 * The cell signal is combined with options.signal. Abort rejects; no detached work is started. */
var sleep = ((timer) => (milliseconds, value, options = {}) => timer(milliseconds, value,
  { ...options, signal: options.signal && cell_signal() ? AbortSignal.any([options.signal, cell_signal()]) : options.signal ?? cell_signal() }))(wait_timer);

// Interaction is agent-owned. Create the iterator before any await so piped lines are retained.
var console_write = process.stdout.write.bind(process.stdout);
/** Format values with util.format and append a newline for the user, outside model capture.
 * Records rendered text in the active cell's session (or current session outside cells).
 * Returns stdout.write's boolean; recording/write errors propagate. Use before input() for
 * questions; console.log separately if the model also needs the value. */
var display = (...values) => {
  const active = active_cell.getStore();
  const log = active?.log ?? session;
  const text = format(...values) + "\n";
  log.append("display", { cell_id: active?.cell_id ?? null, text });
  return console_write(text);
};
var reader = null, input_lines = null;
/** Read one user line asynchronously; returns null at EOF. Present questions with display first. */
var input = async function input(prompt = "") {
  if (!reader) {
    reader = createInterface({ input: process.stdin, output: process.stdout, terminal: Boolean(process.stdin.isTTY), crlfDelay: Infinity });
    input_lines = reader[Symbol.asyncIterator]();
  }
  if (prompt) console_write(prompt);
  const line = await input_lines.next();
  return line.done ? null : line.value;
};

// Plain name-to-purpose index, maintained by the agent under the prompt's tool usage policy.
// Detailed parameters, effects and cancellation limits belong beside each callable.
var tool_catalog = {
  end_turn: "End this invocation after a successful model cell, recording and displaying its final message.",
  display: "Present values to the user and record them separately from model observations.",
  input: "Read a line asynchronously; returns null at EOF. Present questions with display first.",
  sleep: "Await a delay in milliseconds with the current cell's cancellation signal.",
  cell_signal: "Get the current cell's AbortSignal for cooperative cancellation of task I/O.",
};

// Conversation entry: user input, mode entry and reader lifetime.
/** Resume normal work after recovery or reset; each new loop failure starts fresh recovery. */
var run_agent = async function run_agent(user_input = null, { interactive = false } = {}) {
  try {
    while (true) {
      if (user_input === null) user_input = await input("You: ");
      if (user_input === null) return;
      if (user_input.trim()) {
        session.append("mode", { name: "normal" });
        session.append("message", { message: { role: "user", content: user_input } });
        try {
          let failed_repairs = 0;
          while (true) {
            try { await agent_loop(); break; }
            catch (error) {
              if (error?.name === "AbortError" || error instanceof RecordingError || error instanceof ModelStopError || error instanceof CellTimeoutError || cell_signal()?.aborted) throw error;
              // Fallback work may re-enter recovery, but two repairs in a row that fell
              // back end the turn on the next failure instead of cycling.
              if (failed_repairs >= 2) {
                session.append("model_stop", { reason: "repeated_recovery_failure", error: error?.stack ?? String(error) });
                throw new ModelStopError("Work failed again after two failed recoveries", { cause: error });
              }
              failed_repairs = await recover(error?.stack ?? String(error)) === "failed" ? failed_repairs + 1 : 0;
            }
          }
        } catch (error) {
          // Interactive work keeps the live environment and waits for new input.
          if (!interactive || !(error instanceof ModelStopError)) throw error;
          console_write(`Stopped: ${error.message}\n`);
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
var task_state = {};
var context = new Context(session);
// Resume records, never old code or external effects. Close children before parents.
for (const cell of session.cells.reverse()) {
  if (cell.status === "running") session.append("cell_end", { cell_id: cell.cell_id, output: "", status: "interrupted", error: "Process stopped; effects unknown. Inspect before retrying." });
}
repair_pending();
session.append("source", { code: In.at(-1) });
await run_agent(null, { interactive: true });
