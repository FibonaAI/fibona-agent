import assert from "node:assert/strict";
import test from "node:test";
import { readFile, mkdtemp, rm, writeFile, appendFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable, PassThrough } from "node:stream";
import { Console } from "node:console";
import { spawnSync, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { once } from "node:events";
import { REPL } from "../runtimes/node/runtime.js";

const bootstrap = fileURLToPath(new URL("../bootstraps/node/main.js", import.meta.url));
const main = fileURLToPath(new URL("../main.js", import.meta.url));
const source = await readFile(bootstrap, "utf8");
const message = text => ({ type: "message", role: "assistant", content: [{ type: "output_text", text }] });
const call = (code, call_id = "call") => ({ type: "function_call", name: "run_cell", call_id, arguments: JSON.stringify({ code }) });
const response = (output = [message("Done")], status = "completed") => ({ id: "response", status, output, usage: { input_tokens: 20, output_tokens: 10, total_tokens: 30 } });
const plain = value => JSON.parse(JSON.stringify(value));

async function harness(t, { file = "", respond = () => response() } = {}) {
  const repl = new REPL();
  const stdout = new PassThrough(), stderr = new PassThrough();
  let output = "";
  stdout.on("data", x => { output += x; });
  stderr.on("data", x => { output += x; });
  const requests = [];
  repl.bind({
    process: { env: { FIBONA_SESSION: file }, stdin: Readable.from([]), stdout, stderr },
    console: new Console(stdout, stderr),
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      requests.push(body);
      return { ok: true, json: async () => await respond(body, requests.length) };
    },
  });
  t.after(() => { repl.close(); stdout.destroy(); stderr.destroy(); });
  await repl.execute(source);
  return { repl, ns: repl.server.context, requests, output: () => output };
}

function paired(items) {
  const pending = new Set();
  for (const item of items) {
    if (item.type === "function_call") pending.add(item.call_id);
    if (item.type === "function_call_output") {
      assert.ok(pending.has(item.call_id)); pending.delete(item.call_id);
    }
  }
  assert.equal(pending.size, 0);
}

// Runtime and execution

test("runtime preserves declarations, imports and nested await, with source history", async t => {
  const repl = new REPL(); t.after(() => repl.close());
  await repl.execute('var x = 1; var mod = await import("node:path");');
  assert.equal(await repl.execute('await run_cell("x += 2"); x'), 3);
  assert.equal(await repl.execute('typeof mod.join'), "function");
  await assert.rejects(repl.execute('throw new Error("bad")'), /bad/);
  await assert.rejects(repl.execute('await run_cell("throw new Error(\\"nested\\")")'), /nested/);
  await assert.rejects(repl.execute('var broken = ;'), /Unexpected/);
  assert.equal(await repl.execute("x"), 3);
  assert.equal(repl.history[1], 'var x = 1; var mod = await import("node:path");');
});

test("runtime rejects sibling execution but supports sequential nesting", async t => {
  const repl = new REPL(); t.after(() => repl.close());
  const results = await repl.execute('var outcomes = await Promise.allSettled([run_cell("await new Promise(r => setTimeout(r, 10))"), run_cell("var bad = true")]); outcomes.map(x => x.status)');
  assert.deepEqual(plain(results), ["fulfilled", "rejected"]);
  assert.equal(await repl.execute('await run_cell("var nested = 7"); nested'), 7);
});

test("binding host-like names is local to a REPL, not a host-global setter", async t => {
  const host = globalThis.process;
  const first = new REPL(), second = new REPL();
  t.after(() => { first.close(); second.close(); });
  first.bind({ process: { marker: "local" } });
  assert.equal(globalThis.process, host);
  assert.equal(await first.execute("process.marker"), "local");
  assert.equal(await second.execute("process.pid"), host.pid);
  await first.execute("var private_value = 123");
  assert.equal(await second.execute("typeof private_value"), "undefined");
});

test("source and nested cell facts are complete, and errors preserve partial state", async t => {
  const { repl, ns } = await harness(t);
  assert.equal(ns.session.records.find(r => r.type === "source").code, source);
  const result = await ns.run_cell('var child = await run_cell("var value = 41; console.log(\\"child\\"); throw new Error(\\"expected\\")"); console.log(child.status); value += 1;');
  assert.equal(result.status, "completed");
  assert.equal(result.output, "error\n");
  assert.equal(await repl.execute("value"), 42);
  const [parent, child] = ns.session.cells;
  assert.equal(child.parent_cell_id, parent.cell_id);
  assert.equal(child.call_id, null);
  assert.equal(child.status, "error");
  assert.equal(child.output, "child\n");
  assert.match(child.error, /expected/);
  assert.deepEqual(plain(ns.context.build()), []);
});

test("display bypasses capture, stdout/stderr are restored after a cell", async t => {
  const { ns, output } = await harness(t);
  const stdout = ns.process.stdout.write;
  const result = await ns.run_cell('display("immediate"); console.log("captured"); process.stderr.write("error stream");');
  assert.match(output(), /immediate/);
  assert.doesNotMatch(output(), /captured/);
  assert.equal(result.output, "captured\nerror stream");
  assert.equal(ns.process.stdout.write, stdout);
});

test("only explicit display reaches the user during the model loop", async t => {
  const { ns, output, requests } = await harness(t, { respond: (_body, n) => n === 1
    ? response([message("hidden reply"), call('console.log("hidden inspection"); display(user_value);')])
    : response([message("hidden final")]) });
  ns.user_value = "visible answer";
  await ns.run_agent("go");
  assert.match(output(), /visible answer/);
  assert.doesNotMatch(output(), /hidden/);
  assert.doesNotMatch(JSON.stringify(ns.session.records), /visible answer/);
  assert.doesNotMatch(JSON.stringify(requests[1].input), /visible answer/);
  assert.match(ns.session.cells[0].code, /display\(user_value\)/);
  assert.match(requests[1].input.find(x => x.type === "function_call_output").output, /hidden inspection/);
  assert.equal(ns.session.records.filter(r => r.type === "model_response").length, 2);
});

// Session and context

test("model loop projects tool replies once; nested facts and usage stay separate", async t => {
  const { ns, requests } = await harness(t, { respond: (_body, n) => n === 1
    ? response([call('await run_cell("console.log(7)"); console.log(8);')]) : response() });
  await ns.run_agent("go");
  assert.equal(requests.length, 2);
  paired(requests[1].input);
  assert.equal(requests[1].input.filter(x => x.type === "function_call_output").length, 1);
  assert.equal(JSON.parse(requests[1].input.find(x => x.type === "function_call_output").output).output, "8\n");
  assert.equal(ns.session.records.filter(r => r.type === "message").length, 1);
  assert.equal(ns.session.usage.length, 2);
});

test("reset inside a tool retains the entire batch; bare reset removes model history only", async t => {
  const { ns, requests } = await harness(t, { respond: (_body, n) => n === 1
    ? response([call('var progress = 1; context.reset("continue progress");', "one"), call('progress += 1;', "two")]) : response() });
  await ns.run_agent("go");
  paired(requests[1].input);
  assert.match(requests[1].input[0].content, /continue progress/);
  const before = ns.session.records.length;
  ns.context.reset();
  assert.equal(ns.progress, 2);
  assert.equal(ns.session.records.length, before + 1);
  assert.equal(ns.context.build().length, 1);
  assert.match(ns.context.build()[0].content, /Fresh context/);
  assert.throws(() => ns.context.reset({ bad: true }), /handoff/);
});

test("context binds its own log and truncates only projected results", async t => {
  const { ns } = await harness(t);
  const log = new ns.Session();
  log.append("model_response", { status: "completed", output: [call("code")], usage: null });
  const id = log.append("cell_start", { code: "code", parent_cell_id: null, call_id: "call" });
  log.append("cell_end", { cell_id: id, output: "x".repeat(1000), error: null, status: "completed" });
  const view = new ns.Context(log); view.max_output_chars = 100;
  const text = view.build().at(-1).output;
  assert.match(text, /Shortened/);
  assert.equal(log.cells[0].output.length, 1000);
  assert.equal(ns.context.log, ns.session);
});

test("persistence detaches inputs, repairs only a torn tail, and rejects old formats", async t => {
  const { ns } = await harness(t);
  const dir = await mkdtemp(join(tmpdir(), "fibona-node-")); t.after(() => rm(dir, { recursive: true, force: true }));
  const file = join(dir, "session.jsonl");
  const log = new ns.Session(file);
  const item = { role: "user", content: "中文" };
  log.append("message", { message: item }); item.content = "changed";
  assert.equal(log.messages[0].content, "中文");
  assert.equal((await stat(file)).mode & 0o777, 0o600);
  const valid = await readFile(file);
  await appendFile(file, Buffer.from([123, 34, 0xe8]));
  const restored = new ns.Session(file);
  assert.equal(restored.messages[0].content, "中文");
  assert.deepEqual(await readFile(file), valid);
  await writeFile(file, valid.subarray(0, -1));
  new ns.Session(file);
  assert.deepEqual(await readFile(file), valid);
  await appendFile(file, '{bad}\n');
  assert.throws(() => new ns.Session(file), /Invalid session/);
  const old = '{"id":0,"type":"session","version":3}\n'; await writeFile(file, old);
  assert.throws(() => new ns.Session(file), /version 4/);
  assert.equal(await readFile(file, "utf8"), old);
});

test("startup closes unfinished nested cells once and never replays code", async t => {
  const dir = await mkdtemp(join(tmpdir(), "fibona-node-resume-")); t.after(() => rm(dir, { recursive: true, force: true }));
  const file = join(dir, "session.jsonl");
  const { ns } = await harness(t, { file });
  ns.session.append("model_response", { status: "completed", output: [call("should_not_run()")], usage: null });
  const parent = ns.session.append("cell_start", { code: "should_not_run()", parent_cell_id: null, call_id: "call" });
  const child = ns.session.append("cell_start", { code: "child()", parent_cell_id: parent, call_id: null });
  const second = await harness(t, { file });
  assert.deepEqual(plain(second.ns.session.records.filter(r => r.type === "cell_end").map(r => r.cell_id)), [child, parent]);
  paired(second.ns.context.build());
  assert.equal(second.requests.length, 0);
  const third = await harness(t, { file });
  assert.equal(third.ns.session.records.filter(r => r.type === "cell_end").length, 2);
});

test("nested repair does not close live parent cells; rebinding session does not move their records", async t => {
  const { ns } = await harness(t);
  const log = ns.session;
  const result = await ns.run_cell('await run_cell("repair_pending(); console.log(1)"); session = new Session();');
  assert.equal(result.status, "completed");
  assert.equal(log.cells.length, 2);
  assert.ok(log.cells.every(c => c.status === "completed"));
  assert.equal(ns.session.cells.length, 0);
});

test("failed context persistence does not change its boundary or send a model request", async t => {
  const { ns, requests } = await harness(t);
  const before = plain(ns.context.build());
  ns.session.append = () => { throw new Error("storage unavailable"); };
  assert.throws(() => ns.context.reset("handoff"), /storage unavailable/);
  assert.deepEqual(plain(ns.context.build()), before);
  assert.equal(requests.length, 0);
});

// Model protocol and recovery

test("explicit requests have no session/execution side effects and read live options", async t => {
  const { ns, requests } = await harness(t, { respond: () => response([call('var unexpected = true')]) });
  const before = plain(ns.session.records);
  await ns.request_model([{ role: "user", content: "explicit" }], { tools: [], instructions: "custom", max_output_tokens: 100 });
  assert.equal(requests[0].instructions, "custom");
  assert.deepEqual(requests[0].tools, []);
  assert.equal(requests[0].max_output_tokens, 100);
  ns.model_options.model = "test-model";
  await ns.request_model([]);
  assert.equal(requests[1].model, "test-model");
  assert.deepEqual(requests[1].instructions.match(/^# .+$/gm), ["# identity", "# environment", "# working", "# adaptation"]);
  assert.deepEqual(plain(ns.session.records), before);
  assert.equal(ns.unexpected, undefined);
});

test("incomplete responses retry once without execution, then a later user turn works", async t => {
  const { ns, requests } = await harness(t, { respond: (_body, n) => n < 3
    ? response([call('var unexpected = true')], "incomplete") : response() });
  await ns.run_agent("first");
  assert.equal(ns.unexpected, undefined);
  assert.equal(ns.session.cells.length, 0);
  assert.deepEqual(requests[0].input, requests[1].input);
  await ns.run_agent("continue");
  assert.equal(requests.length, 3);
  assert.equal(ns.session.records.filter(r => r.type === "model_response").length, 3);
  assert.equal(requests[2].input.filter(r => r.type === "function_call").length, 0);
});

test("invalid tools are recorded without fake cells and allow continuation", async t => {
  const { ns, requests } = await harness(t, { respond: (_body, n) => n === 1
    ? response([{ ...call(""), arguments: "{" }, { ...call("", "unknown"), name: "other" }]) : response() });
  await ns.run_agent("go");
  assert.equal(ns.session.cells.length, 0);
  assert.equal(ns.session.records.filter(r => r.type === "tool_error").length, 2);
  paired(requests[1].input);
});

test("recovery repairs broken Context independently and resumes original work", async t => {
  const { ns, requests } = await harness(t, { respond: (_body, n) => n === 1
    ? response([call('context = saved_context;')]) : response() });
  ns.saved_context = ns.context;
  ns.context = { build() { throw new Error("broken policy"); } };
  await ns.run_agent("original goal");
  assert.equal(requests.length, 3);
  assert.match(requests[0].input.at(-1).content, /Recovery mode/);
  assert.equal(ns.context, ns.saved_context);
  assert.deepEqual(plain(ns.session.records.filter(r => r.type === "mode").map(r => r.name)), ["normal", "recovery", "normal"]);
  assert.equal(new Set(requests.map(r => r.instructions)).size, 1);
});

test("recovery failure propagates without an endless retry loop", async t => {
  const { ns, requests } = await harness(t, { respond: () => { throw new Error("unavailable"); } });
  await assert.rejects(ns.run_agent("go"), /unavailable/);
  assert.equal(requests.length, 2);
});

test("near budget advises reset; overflow uses fresh recovery input", async t => {
  const { ns, requests } = await harness(t);
  ns.context_window = 10000; ns.model_options.max_output_tokens = 1000;
  ns.context.estimate = () => 8000;
  await ns.run_agent("near");
  assert.match(requests[0].input.at(-1).content, /near its input budget/);
  let first = true;
  ns.context.estimate = () => { if (first) { first = false; return 100000; } return 100; };
  await ns.run_agent("overflow");
  assert.match(requests[1].input.at(-1).content, /Recovery mode/);
  assert.equal(requests.length, 3);
});

test("self modification affects actual next request; a failed candidate rolls back inside its cell", async t => {
  const codes = [
    'var saved_build = context.build; context.build = function() { return [...saved_build.call(this), {role:"developer",content:"LIVE_CHANGE"}]; }; instructions.working = "changed policy";',
    'var good_build = context.build; try { context.build = () => { throw new Error("candidate failed"); }; context.build(); } catch (error) { context.build = good_build; var rollback_error = error.message; }',
  ];
  const { ns, requests } = await harness(t, { respond: (_body, n) => n <= codes.length ? response([call(codes[n - 1], String(n))]) : response() });
  await ns.run_agent("adapt");
  assert.equal(ns.context.build, ns.good_build);
  assert.equal(ns.rollback_error, "candidate failed");
  assert.match(requests[1].instructions, /# working\nchanged policy/);
  assert.equal(requests[1].input.at(-1).content, "LIVE_CHANGE");
  assert.equal(requests[2].input.at(-1).content, "LIVE_CHANGE");
  assert.ok(ns.session.cells.every(c => c.status === "completed"));
  assert.ok(!ns.session.records.some(r => r.type === "mode" && r.name === "recovery"));
});

// Launcher and transport

test("CLI accepts a directory or file without injected agent contract", async t => {
  const dir = await mkdtemp(join(tmpdir(), "fibona-node-cli-")); t.after(() => rm(dir, { recursive: true, force: true }));
  const file = join(dir, "main.js");
  await writeFile(file, 'if (typeof agent_source !== "undefined" || typeof options !== "undefined") throw new Error("injected"); await run_cell("var value = 42"); console.log(value);');
  for (const target of [dir, file]) {
    const r = spawnSync(process.execPath, [main, "--bootstrap", target], { cwd: tmpdir(), encoding: "utf8", timeout: 10000 });
    assert.equal(r.status, 0, r.stderr); assert.equal(r.stdout.trim(), "42");
  }
  await writeFile(file, 'throw new Error("bootstrap failed")');
  const bad = spawnSync(process.execPath, [main, "--bootstrap", file], { encoding: "utf8", timeout: 10000 });
  assert.notEqual(bad.status, 0); assert.match(bad.stderr, /bootstrap failed/);
});

test("real HTTP transport handles multiple piped turns and reports provider errors", async t => {
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = ""; for await (const chunk of req) raw += chunk;
    requests.push(JSON.parse(raw));
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(response([message("HTTP_OK")])));
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening"); t.after(() => { server.closeAllConnections(); server.close(); });
  const child = spawn(process.execPath, [main, "--runtime", "node", "--bootstrap", "node"], {
    cwd: tmpdir(), env: { ...process.env, FIBONA_SESSION: "", OPENAI_API_KEY: "test", OPENAI_BASE_URL: `http://127.0.0.1:${server.address().port}/v1` },
    stdio: ["pipe", "pipe", "pipe"],
  });
  t.after(() => child.kill());
  let out = "", err = ""; child.stdout.on("data", x => { out += x; }); child.stderr.on("data", x => { err += x; });
  child.stdin.end("你好\ncontinue\n");
  const [code] = await once(child, "close");
  assert.equal(code, 0, err); assert.equal(requests.length, 2); assert.doesNotMatch(out, /HTTP_OK/);
  assert.deepEqual(requests[1].input.filter(x => x.role === "user").map(x => x.content), ["你好", "continue"]);
  const { ns } = await harness(t);
  ns.fetch = async () => ({ ok: false, status: 400, text: async () => "bad request" });
  await assert.rejects(ns.request_model([]), /Model HTTP 400: bad request/);
});

// Real terminal input: Python's standard-library PTY driver avoids an npm dependency.
test("terminal handles Chinese editing, multiple turns and EOF", {
  skip: process.platform === "win32" || spawnSync("python3", ["-c", "import pty, termios"]).status !== 0
    ? "requires POSIX and python3 for the PTY driver" : false,
}, () => {
  const script = `
    import { readFile } from "node:fs/promises";
    import { REPL } from ${JSON.stringify(new URL("../runtimes/node/runtime.js", import.meta.url).href)};
    const repl = new REPL();
    repl.bind({ fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      const message = body.input.findLast(item => item.role === "user").content;
      repl.server.context.display("RECEIVED:" + message);
      return { ok: true, json: async () => ({ status: "completed", output: [] }) };
    } });
    try { await repl.execute(await readFile(${JSON.stringify(bootstrap)}, "utf8")); }
    finally { repl.close(); }
  `;
  const driver = String.raw`
import os, pty, select, subprocess, sys, termios, time
master, slave = pty.openpty()
attrs = termios.tcgetattr(slave)
attrs[0] &= ~getattr(termios, "IUTF8", 0)
attrs[6][termios.VERASE] = b"\x7f"
termios.tcsetattr(slave, termios.TCSANOW, attrs)
child = subprocess.Popen([sys.argv[1], "--input-type=module", "-e", sys.argv[2]],
    stdin=slave, stdout=slave, stderr=slave,
    env={**os.environ, "FIBONA_SESSION": "", "TERM": "xterm-256color"})
os.close(slave)
transcript = bytearray()
def wait_for(marker, count=1):
    deadline = time.monotonic() + 5
    while transcript.count(marker) < count:
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([master], [], [], remaining)[0], repr(transcript)
        chunk = os.read(master, 8192)
        assert chunk, repr(transcript)
        transcript.extend(chunk)
try:
    wait_for(b"You: ")
    os.write(master, b"hi\n")
    wait_for(b"RECEIVED:hi")
    wait_for(b"You: ", 2)
    os.write(master, "你知道自己在cell里的代码是什么啊".encode() + b"\x7f" + "吗\n".encode())
    wait_for("RECEIVED:你知道自己在cell里的代码是什么吗".encode())
    wait_for(b"You: ", 3)
    os.write(master, b"\x04")
    assert child.wait(timeout=5) == 0, repr(transcript)
finally:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=5)
    os.close(master)
`;
  const result = spawnSync("python3", ["-c", driver, process.execPath, script], { encoding: "utf8", timeout: 25000 });
  assert.equal(result.status, 0, result.stderr || String(result.error));
});
