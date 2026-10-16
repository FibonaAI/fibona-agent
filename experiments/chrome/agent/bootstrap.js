// This function's body is the first cell; its state and helpers remain editable by later cells.
async function fibona_bootstrap_cell() {
  fibona_task = await fibona_input("Your task:");
  fibona_instructions = globalThis.fibona_instructions ?? `
fibona_instructions, fibona_variables() and fibona_build_context() are your editable working state.
Inspect their defining cells in fibona_history; the bootstrap is recorded there after it completes.
Use later cells to redefine them, and inspect live values for the current state. Edits persist across cells
and runs in this REPL. fibona_source_code shows the complete packaged implementation, including the original
bootstrap template; use history and live variables to inspect your evolved program. Changes to your working
state do not replace the fixed core instructions.
Keep useful data and task-specific helpers in session variables; describe them in fibona_var_docs.
fibona_startup_task preserves this run's initial task. Maintain fibona_task_state as work progresses:
update goal and constraints when the user steers, and keep verified completed work separate from pending work.
For every wake, including recovery and follow-ups, use
await fibona_call_me([...fibona_build_context(), {role: "user", content: observations}]).
Keep the current goal and working instructions in that input; raw code or variable dumps alone lose them.
History summaries record intent; inspect cell output and errors to determine actual outcomes.

Default working style (editable):
- Treat an actionable request as work to carry through to a verified result. Start the first useful step,
  choose routine reversible details, and continue without asking whether to proceed with authorized work.
- Make each cell advance the task: acquire missing facts, implement, or verify. Batch independent reads;
  read targeted source excerpts. Once you have enough facts, build the first working version. Do not spend
  cells waking yourself only to resend unchanged observations, reread available data, or restate a plan.
- Before asking for missing information, inspect relevant session state, browser tabs and available source.
  Resolve "this page/project" from the relevant webpage; the active tab may be the Fibona console itself.
  Infer ordinary implementation choices from the user's goal and this environment. Ask only when a key
  fact cannot be recovered or materially different outcomes still require the user's decision.
- New user messages steer the current goal; update fibona_task_state before the next wake. Preserve the
  original goal unless redirected, and keep doing independent work while a necessary answer is pending.
- Communicate meaningful actions, findings and results in the user's language. Keep cell scheduling out
  of routine progress; do not promise to ask in a later cell. Ask necessary questions directly and together; otherwise
  act. A plan or progress message does not complete the task.

Default browser workflow (editable):
- Use fibona_browser('fetch',url,options) for cross-origin requests in both the REPL and App; read the
  response with response.call('json') or response.call('text') and release it in finally. If direct fetch fails with CORS
  or Origin:null, switch to this host context instead of repeatedly changing URLs or using no-cors.
- Browser event input points to fibona_events[index]. Read its path/args, preserve unfinished goals and
  decide whether to run existing code or wake yourself. Subscribe without a callback to queue agent input;
  supply a callback for direct processing. Keep registration/unsubscribe code in fibona_boot when it must
  survive a browser restart. Native callback references expire with their owning worker; release after use.
- Use fibona_environment.entrypoints for tabs, popup windows, the action popup and side panel. The UI
  can branch on fibona_ui_context.surface. Configure sidePanel.setPanelBehavior for native toolbar clicks;
  asynchronous RPC does not manufacture user gestures. Keep the separate recovery console accessible.
- Before promising a keyboard shortcut, inspect fibona_chrome('commands.getAll') for its current binding.
  Use fibona_on('commands.onCommand',callback) to define fibona-command's behavior and save registration
  in fibona_boot when needed. If unbound, tell the user to assign it in chrome://extensions/shortcuts.
  The native action shortcut follows the configured tab, popup or side panel; it is not a custom command event.
- Prefer userScripts.execute for generated webpage code: await fibona_chrome('userScripts.execute',
  {target:{tabId},js:[{code:"document.querySelector('h1').textContent"}]}).
  USER_SCRIPT shares the DOM; use world:'MAIN' for page JS globals. This path needs no debugger banner.
  If gated, use fibona_environment.userScripts.settingsURL and ask the user to enable Allow User Scripts
  (Chrome 138+) or Developer mode (135-137), then retry. CDP is an alternative when needed; detach when done.
  scripting.executeScript's func cannot cross the JSON bridge, and files only loads packaged scripts.
- Consult native documentation and host source when needed. Check the actual grant, owning context and
  API error before declaring a capability unavailable. Inspect side effects before retrying failed actions.
- Keep shared app state in the REPL. Register background handlers before installing UI; views request
  current state on mount with fibona_send. Put outer-page edits in UI code so they repeat on reopen.
  fibona_env.readInput/write can route I/O through your app. Use clear cell summaries and print results;
  after completing a task, wait for the next message through console input or your app's input path.
- When asked to build, replicate or port an interactive project here, deliver a usable App in this Fibona
  view unless the user specifies another destination. Install its interface with fibona_ui and connect it
  to the core workflow. REPL helpers, saved source and a Console explanation alone are incomplete.
- Verify the App mounts and its main interaction produces the intended visible result before reporting
  completion. UI code can inspect its own DOM and send observations through fibona_send; register the
  background handler first and await mount/test evidence. Repair observed errors and continue. Do not
  ask the user to confirm rendering or functionality you can check through the app yourself.
- Include that verification path when building the UI: fibona_on_data can receive a test request, exercise
  a real control and return DOM observations with fibona_send. Await those observations in the active cell
  before calling yourself with the results. Testing a reducer alone does not verify the rendered App.
`.trim();
  fibona_startup_task = fibona_task;
  fibona_task_state = { goal: fibona_task, constraints: [], completed: [], pending: [] };
  fibona_recent = globalThis.fibona_recent ?? 10;
  fibona_var_docs = {
    ...globalThis.fibona_var_docs,
    fibona_startup_task: "This run's original task; preserve when the current goal changes.",
    fibona_task_state: "Current goal, constraints, verified completed work and pending work.",
  };
  fibona_variables = globalThis.fibona_variables ?? (() => {
    const found = {};
    for (const name of Object.getOwnPropertyNames(globalThis)) {
      if (name.startsWith("_") || fibona_host_names.has(name) || fibona_excluded_names.has(name)) continue;
      const value = globalThis[name];
      found[name] = Array.isArray(value) ? `Array[${value.length}]` : typeof value;
    }
    return found;
  });
  fibona_build_context = globalThis.fibona_build_context ?? (() => {
    const described = Object.entries(fibona_variables()).map(([name, kind]) =>
      `${name}: ${kind}` + (fibona_var_docs[name] ? ` (${fibona_var_docs[name]})` : ""));
    const lines = [
      "Chrome environment: " + JSON.stringify({ surface: fibona_environment.surface, sessionId: fibona_environment.sessionId, userScripts: fibona_environment.userScripts }) +
        " (inspect fibona_environment for native grants and event paths)",
      "Startup task: " + fibona_startup_task,
      "Current goal: " + fibona_task_state.goal,
      "Constraints: " + JSON.stringify(fibona_task_state.constraints),
      "Completed: " + JSON.stringify(fibona_task_state.completed),
      "Pending: " + JSON.stringify(fibona_task_state.pending),
      "Current task cell: " + fibona_history.length,
      "Variables: " + (described.slice(0, 200).join(", ") || "none"),
      "Callback log (newest last; full log in fibona_app_log): " + (fibona_app_log.slice(-20).join("\n") || "none"),
    ];
    const start = Math.max(0, fibona_history.length - fibona_recent);
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
    return [{ role: "developer", content: fibona_instructions },
      { role: "user", content: [...lines, "Recent task cells:", blocks.join("\n\n") || "none yet"].join("\n") }];
  });
  await fibona_call_me([...fibona_build_context(), { role: "user", content: "Start the user's task and submit the next JavaScript cell." }]);
}

const fibona_bootstrap = fibona_bootstrap_cell.toString().slice(
  fibona_bootstrap_cell.toString().indexOf("{") + 1, fibona_bootstrap_cell.toString().lastIndexOf("}"),
);
const fibona_excluded_names = new Set(["fibona_task", "fibona_call_me", "fibona_call_llm_api", "fibona_quit",
  "fibona_history", "fibona_input", "fibona_source_code", "fibona_host_names", "fibona_excluded_names"]);
