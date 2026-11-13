// ↔ core/env.py. Plain JavaScript with no platform APIs: Node, browsers and JavaScriptCore all run it.

import { isAPIError, type Logger, type Signal } from './utils.js'

/**
 * Raised by `quit()` in a cell to end the run. Python's is a BaseException a cell cannot swallow; JavaScript has
 * none, so the agent also notes the `quit()` call itself.
 */
export class Quit extends Error {
  name = 'Quit'
}

export interface CellResult {
  output: string // Everything the cell printed to stdout/stderr.
  error: string | null // Error and stack if the cell failed.
  quit: boolean // The cell raised Quit.
}

export type Write = (text: string) => void
export type ReadInput = (prompt?: string) => string | Promise<string>

/**
 * A session at global scope (IPythonEnv's): runs cells as bodies of async functions, so `await` works anywhere;
 * the globals they assign persist across cells, while `let`, `const` and `function` declarations stay inside.
 *
 * Output is always captured; optional streams also receive it live. A host that gives `debug` gets console.* there
 * instead of on stdout/stderr, leaving stdout to print alone. User input requires an injected readInput handler, so
 * the environment does not depend on a console.
 *
 * A cell that blocks or crashes the host takes the agent down with it.
 */
export class JSEnv {
  constructor(readonly opts: { stdout?: Write; stderr?: Write; debug?: Write; readInput?: ReadInput } = {}) {}

  /** Put names (call_me, quit, ...) into the cells' global scope, replacing old ones. */
  // ponytail: globalThis is the session, so every JSEnv in a realm shares it; one realm (vm, iframe, Worker) per agent.
  bind(names: Record<string, unknown>): void {
    Object.assign(globalThis, names)
  }

  /**
   * Resolve to cell output and errors; Quit sets the quit flag, an unrepairable API error is thrown.
   *
   * signal: aborting it throws its reason at once, as KeyboardInterrupt does, wherever the cell awaits (as Node's
   * REPL races the cell against SIGINT) and even if the cell catches it. The abandoned cell may run on in the
   * background; its call_me and call_llm_api then fail. A cell that never awaits cannot be interrupted.
   */
  async execute(
    code: string,
    {
      logger,
      summary = '',
      onOutput,
      signal,
    }: { logger?: Logger; summary?: string; onOutput?: Write; signal?: Signal } = {},
  ): Promise<CellResult> {
    logger?.('cell', { summary, code }) // Before execution, so cells that fail or never end are recorded.
    let output = ''
    const tee =
      (stream?: Write) =>
      (...values: unknown[]) => {
        const text = values.map(format).join(' ') + '\n'
        stream?.(text)
        output += text
        onOutput?.(text)
      }
    const [stdout, stderr] = [tee(this.opts.stdout), tee(this.opts.stderr)]
    const debug = this.opts.debug && tee(this.opts.debug)
    const [log, warn] = [debug ?? stdout, debug ?? stderr]
    // redirect_stdout/redirect_stderr: the console is the cell's while it runs, the host's again after.
    const hostConsole = (globalThis as any).console
    this.bind({
      input: this.opts.readInput ?? unavailableInput, // Rebound every cell, so a replaced `input` does not persist.
      print: stdout,
      console: { ...hostConsole, log, info: log, debug: log, warn, error: warn },
    })
    let onAbort = () => {}
    const aborted = new Promise<never>((_, reject) => (onAbort = () => reject(signal!.reason)))
    signal?.addEventListener('abort', onAbort)
    try {
      // Indirect eval: global scope, not this function's.
      await Promise.race([(0, eval)(`(async () => {\n${code}\n})()`), aborted])
      if (signal?.aborted) throw signal.reason // The cell caught the abort and returned first.
      return { output, error: null, quit: false }
    } catch (error) {
      if (signal?.aborted) throw signal.reason
      // 400/404/422 reject the request itself (bad params, too long, unknown model), so the mind can repair it.
      if (isAPIError(error) && ![400, 404, 422].includes(error.status!)) {
        throw error // Retries are exhausted; a provider failure cannot be repaired by another model call.
      }
      const quit = error instanceof Quit
      return { output, error: quit ? null : formatError(error), quit }
    } finally {
      signal?.removeEventListener('abort', onAbort)
      ;(globalThis as any).console = hostConsole
    }
  }
}

function format(value: unknown): string {
  if (typeof value === 'string') return value
  if (value instanceof Error) return formatError(value)
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

/** traceback.format_exception: the error, its stack and its causes. */
function formatError(error: unknown): string {
  if (!(error instanceof Error)) return `Uncaught ${format(error)}`
  const head = String(error)
  const stack = error.stack ?? ''
  const text = stack.startsWith(head) ? stack : `${head}\n${stack}`.trim() // JavaScriptCore's stack omits the message.
  return error.cause === undefined ? text : `${text}\n\nCaused by: ${formatError(error.cause)}`
}

function unavailableInput(): never {
  throw new Error('No input handler configured for this environment')
}
