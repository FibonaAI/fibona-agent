fibona_task = await fibona_input();
fibona_instructions = `
Your working instructions, fibona_variables() and fibona_build_context() are JavaScript state in this session.
Their original implementation is in fibona_source_code['agent/bootstrap.js']; you can replace them in later cells.
fibona_source_code also contains the core runtime source. Read relevant excerpts and include them in input when needed.
Use await fibona_call_me([...fibona_build_context(), {role: "user", content: observations}]) to send these instructions and your selected context.
Keep useful data and task-specific helpers in session variables; update fibona_var_docs to describe them.
fibona_startup_task preserves the initial task. Maintain fibona_task_state before each fibona_call_me():
update goal and constraints when the user steers the task, and completed and pending as work progresses.
Record verified outcomes in completed; planned work belongs in pending. fibona_build_context() carries all fields on every call.
History summaries record intent; use cell output and errors to determine what actually happened.
`.trim();
fibona_startup_task = fibona_task;
fibona_task_state = { goal: fibona_task, constraints: [], completed: [], pending: [] };
fibona_source_code = { ...globalThis.fibona_source_code, ...SOURCE_CODE };
fibona_RECENT = globalThis.fibona_RECENT ?? 10;
fibona_var_docs = {
  ...globalThis.fibona_var_docs,
  fibona_startup_task: "Original task; preserve when the current goal changes.",
  fibona_task_state: "Current goal, constraints, verified completed work and pending work; update before fibona_call_me().",
};

// Select names and types from the live session; cells may replace this policy.
fibona_variables = () => {
  const found = {};
  for (const name of Object.getOwnPropertyNames(globalThis)) {
    if (name.startsWith("_") || fibona_host_names.has(name) || EXCLUDED_NAMES.has(name)) continue;
    const value = globalThis[name];
    found[name] = Array.isArray(value) ? `Array[${value.length}]` : typeof value;
  }
  return found;
};

// Select context from current session variables and the host's refreshed history; cells may replace this.
fibona_build_context = () => {
  const described = Object.entries(fibona_variables()).map(([name, kind]) =>
    `${name}: ${kind}` + (fibona_var_docs[name] ? ` (${fibona_var_docs[name]})` : ""));
  const lines = [
    "Startup task: " + fibona_startup_task,
    "Current goal: " + fibona_task_state.goal,
    "Constraints: " + JSON.stringify(fibona_task_state.constraints),
    "Completed: " + JSON.stringify(fibona_task_state.completed),
    "Pending: " + JSON.stringify(fibona_task_state.pending),
    "Storage: chrome.storage.local; origin file system via fibona_browser",
    "Current task cell: " + fibona_history.length,
    "Variables: " + (described.slice(0, 200).join(", ") || "none"),
    "App log outside cells (newest last; all of it in fibona_app_log): " + (fibona_app_log.slice(-20).join("\n") || "none"),
  ];
  // Older records stay accessible by index; summarize their intent and outcome here.
  const start = Math.max(0, fibona_history.length - fibona_RECENT);
  fibona_history.slice(0, start).forEach((cell, number) => {
    lines.push(`cell ${number}: ${cell.summary} [${cell.error ? "failed: " + cell.error.split("\n")[0] : "ok"}]`);
  });
  const blocks = fibona_history.slice(start).map((cell, i) => {
    const number = start + i;
    const parts = [`### cell ${number} · ${cell.summary}`, "code:", cell.code.trim()];
    let output = cell.output.trim();
    if (output.length > 1000) output = output.slice(0, 500) + `\n[Full output: fibona_history[${number}].output]\n` + output.slice(-500);
    if (output) parts.push("output:", output);
    if (cell.error) parts.push("error:", cell.error.trim());
    return parts.join("\n");
  });
  const context = [...lines, "Recent task cells:", blocks.join("\n\n") || "none yet"].join("\n");
  return [{ role: "developer", content: fibona_instructions }, { role: "user", content: context }];
};

await fibona_call_me([...fibona_build_context(), { role: "user", content: "Start the user's task and submit the next JavaScript cell." }]);
