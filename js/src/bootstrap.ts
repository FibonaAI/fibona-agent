// ↔ bootstraps/terminal/context.py + parts of init.py: the default bootstrap cell, host-neutral, in parts an app
// can pick from or add its own between: BOOTSTRAP = CONTEXT + STOP + START.
// The app binds what is its own before the run: `task` (else the cell asks input()), and optionally `instructions`
// (its UI and tools) and `var_docs`; this cell merges into them. To stop a cell, the app calls the session's `stop()`.
// It is source text, not a function, so the model reads it unminified in history[0].code.

/** Working state and its helpers: task, startup_task, task_state, instructions, var_docs, variables(), build_context(). */
export const CONTEXT = String.raw`
// Names the host or startup put in, as opposed to what cells defined; variables() hides them.
const hidden = new Set([...Object.keys(globalThis).filter(name => name !== 'instructions' && name !== 'var_docs'), 'task'])
globalThis.task ||= await input()
globalThis.startup_task = task // Preserve the initial task when the current goal changes.
globalThis.task_state = { goal: task, constraints: [], completed: [], pending: [] }
globalThis.instructions = [
  globalThis.instructions,
  [
    'Your working instructions, variables() and build_context() are JavaScript state in this session.',
    'Their original implementation is the bootstrap cell, history[0].code; you can replace them in later cells.',
    'Use await call_me([...build_context(), {role: "user", content: observations}]) to send these instructions and your selected context.',
    'Keep useful data and task-specific helpers in session globals; update var_docs to describe them.',
    'startup_task preserves the initial task. Maintain task_state before each call_me():',
    'update goal and constraints when the user steers the task, and completed and pending as work progresses.',
    'Record verified outcomes in completed; planned work belongs in pending. build_context() carries all fields on every call.',
    'History summaries record intent; use cell output and errors to determine what actually happened.',
  ].join('\n'),
]
  .filter(Boolean)
  .join('\n\n')
globalThis.RECENT ??= 10 // Finished task cells in the default context; others stay in history.
globalThis.var_docs = {
  ...globalThis.var_docs, // Variable name -> description, maintained by the agent in this session.
  startup_task: 'Original task; preserve when the current goal changes.',
  task_state: 'Current goal, constraints, verified completed work and pending work; update before call_me().',
}
/** What cells have defined: name -> type, with the size of containers. Cells may replace this policy. */
globalThis.variables = () =>
  Object.fromEntries(
    Object.keys(globalThis)
      .filter(name => !name.startsWith('_') && !hidden.has(name))
      .map(name => {
        const value = globalThis[name]
        const size =
          typeof value === 'string' || Array.isArray(value)
            ? value.length
            : value instanceof Map || value instanceof Set
              ? value.size
              : value?.constructor === Object
                ? Object.keys(value).length
                : null
        const kind = value === null ? 'null' : typeof value === 'object' ? (value.constructor?.name ?? 'Object') : typeof value
        return [name, size === null ? kind : kind + '[' + size + ']']
      }),
  )
/**
 * Select context from current session globals and the host's refreshed history. Cells can replace this and
 * variables(), the working instructions, var_docs and RECENT.
 * Returns the working instructions as a developer message, then the context as a user message.
 */
globalThis.build_context = () => {
  const described = Object.entries(variables()).map(
    ([name, kind]) => name + ': ' + kind + (var_docs[name] ? ' (' + var_docs[name] + ')' : ''),
  )
  const lines = [
    'Startup task: ' + startup_task,
    'Current goal: ' + task_state.goal,
    'Constraints: ' + JSON.stringify(task_state.constraints),
    'Completed: ' + JSON.stringify(task_state.completed),
    'Pending: ' + JSON.stringify(task_state.pending),
    ...(globalThis.process?.cwd ? ['Working directory: ' + process.cwd()] : []), // Only hosts that have one.
    'Current task cell: ' + (history.length - 1),
    'Variables: ' + (described.slice(0, 200).join(', ') || 'none'),
  ]
  // Older records stay accessible by index; summarize their intent and outcome here.
  const start = Math.max(0, history.length - RECENT)
  history.slice(0, start).forEach((cell, number) => {
    const result = cell.error ? 'failed: ' + cell.error.trim().split('\n')[0] : 'ok'
    lines.push('cell ' + number + ': ' + cell.summary + ' [' + result + ']')
  })
  const blocks = history.slice(start).map((cell, i) => {
    const parts = ['### cell ' + (start + i) + ' · ' + cell.summary, 'code:', cell.code.trim()]
    let output = cell.output.trim()
    if (output) {
      // Keep short output intact; for long output keep the head and tail and point to the full text.
      if (output.length > 1000) {
        output = output.slice(0, 500) + '\n[Full output: history[' + (start + i) + '].output]\n' + output.slice(-500)
      }
      parts.push('output:', output)
    }
    if (cell.error) parts.push('error:', cell.error.trim())
    return parts.join('\n')
  })
  const context = [...lines, 'Recent task cells:', blocks.join('\n\n') || 'none yet'].join('\n')
  return [
    { role: 'developer', content: instructions },
    { role: 'user', content: context },
  ]
}
`.trim()

/** stop() and STOPPED. Needs build_context() (CONTEXT, or the app's own) at the time a stopped cell is repaired. */
export const STOP = String.raw`
// Stop: the app calls stop() (its Ctrl+C, a button). The running cell's model call fails with STOPPED, and core's
// repair cell then waits for the user's message instead of repairing.
// ponytail: lands at the cell's model call (in flight or next); cancelling the request itself is the app's.
globalThis.STOPPED = 'Stopped by the user.'
let stopping = false
const pending = new Set()
globalThis.stop = () => {
  stopping = true
  pending.forEach(fail => fail())
}
const stoppable = call => (...args) =>
  new Promise((resolve, reject) => {
    const fail = () => reject(new Error(STOPPED))
    if (stopping) return fail()
    pending.add(fail)
    call(...args).then(resolve, reject).finally(() => pending.delete(fail))
  })
const afterStop = wake => async (items, params) => {
  if (history.at(-2)?.error?.includes(STOPPED)) {
    stopping = false
    const note =
      'The user stopped cell ' + (history.length - 2) + ' partway; its side effects are not rolled back ' +
      '(record in history, state in session globals). Their message:\n\n' + (await input())
    items = [...build_context(), { role: 'user', content: note }]
  }
  return wake(items, params)
}
// Core binds both for each cell; the setters wrap each fresh binding.
for (const [name, wrap] of [['call_me', fresh => afterStop(stoppable(fresh))], ['call_llm_api', stoppable]]) {
  let bound = wrap(globalThis[name])
  Object.defineProperty(globalThis, name, { get: () => bound, set: fresh => (bound = wrap(fresh)) })
}
`.trim()

/** Wakes the mind for the first time. */
export const START = String.raw`
await call_me([...build_context(), { role: 'user', content: "Start the user's task and submit the next JavaScript cell." }])
`.trim()

export const BOOTSTRAP = [CONTEXT, STOP, START].join('\n\n')
