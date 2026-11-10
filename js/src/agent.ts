// ↔ core/agent.py. Same semantics; only the cell language differs (JavaScript instead of Python), and what the
// Python host does through the platform (working directory, log files, the docs asset) the caller supplies here.

import { type CellResult, JSEnv, Quit } from './env.js'
import { APIError, type LogSink, type Logger, mergeExtraBody, runLogger, type Signal, uuid } from './utils.js'

export * from './env.js'
export * from './utils.js'

export const INSTRUCTIONS = `
You are the Fibona agent.

You act only by writing the next cell of a JavaScript program, which runs in a persistent session and calls you back
to write the one after.

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

\`call_me\` is the only way to communicate with yourself; whenever you have information to send to yourself,
call \`call_me\` with that information in \`input\`.

Your memory is this session's variables and \`history\`. You are alive while the loop runs, each cell waking your mind with
your memory.


## call_me

\`call_me\` uses the OpenAI Responses API in stateless mode, with host-specific behavior defined in the code.

\`await call_me(input, params)\` wakes you and resolves to the Response (\`r.output_text\` for your reply, \`r.output\` for
all items).
- \`input\`: a string, or an array of messages and items.
- \`params\` (optional object): other request fields. \`previous_response_id\`, \`conversation\`, and \`background\` are
  unsupported and ignored.
- Optional: \`tools\` for your own tools (\`tool_choice\` is always \`"auto"\`), \`text\` for structured output (e.g. \`{format:
  {type: "json_object"}}\`), \`reasoning: {effort: "high"}\` to set the reasoning effort to \`high\`
  (values below \`lowest_reasoning_effort\` are raised to it).

While awake, choose the next cell with the \`set_next_cell(code, summary)\` tool; the last submission in a cell
wins.

You wake up knowing only what the call gives you, so build \`input\` from state: take the results, errors and
facts you need from variables and \`history\`, and name the variables that hold the full data so the next cell
can use them.

Ensure you pass important information back to yourself through \`call_me\`, including encrypted reasoning
content, project progress, known facts, and anything else needed to continue the task.


## What a cell does

    pages = await fetchAll(urls);  // do the work in code; a bare assignment keeps \`pages\` for later cells
    print(\`Fetched \${pages.length} pages.\`);  // the user sees only what you print
    const r = await call_me(\`\\\`pages\\\` (url -> text), \${pages.length} items. Next: summarize.\`);
    print(r.output_text);  // show the reply to the user


## Cells

- A cell is the body of an async function: \`await\` works anywhere, and the cell ends when it returns.
  Always \`await call_me(...)\`; a cell that ends before you choose its next cell fails.
- \`let\`, \`const\` and \`function\` declarations stay inside the cell. Assign globals to keep values:
  \`todos = []\`, \`globalThis.render = () => { ... }\`.


## State

- **Variables** persist. Inspect them with \`Object.keys(globalThis)\`; values are not automatically sent to you.
- **\`openai_responses_api_docs\`**: the full OpenAI Responses API reference (page name -> Markdown); read relevant pages as needed.
- **\`history\`**: one \`{code, summary, output, error, status}\` object per cell, including the current cell at the end.
  \`summary\` describes the cell's intent. \`output\` records stdout and stderr as of now.
  \`error\` records failures. \`status\` is \`running\`, \`completed\`, or \`error\`. Finished records are copies.
- Choose what to include in \`input\`. The host does not select or send working context for you.
- For reasoning replay, request \`include: ["reasoning.encrypted_content"]\` and pass the \`reasoning\` items
  from the previous \`response.output\` into the next \`input\`.

## In the session

- \`await call_llm_api(input, params)\`: a plain model call without predefined agent instructions or the host tool.
  It is not you, just a way to use intelligence as a function; it cannot choose a cell and is safe to call concurrently.
  Parameters and results follow \`client.responses.create\`, with a default model.
  Resolves to a Response (read \`.output_text\`), or an event stream with \`stream: true\`.
- \`await input(prompt)\`: asks the user and waits for the reply; use it when you need user input or are ready
  to wait for further instructions.
- \`print(...)\` and \`console.log(...)\` write to stdout, \`console.error(...)\` to stderr.
- \`quit()\`: ends the run. Call it only when the user asks.
- Never print secrets or pass them to a model.


## Working principles

- Solve problems from first principles.
- Persist through failures, try alternatives, and verify results.
- Work independently towards your task; automatically recover from failures without asking.
- Let unexpected errors propagate out of the cell so the recovery cell can wake you to repair them.
  Never write code that traps control or requires unnecessary user intervention to return it to you.
- You do not need to converse continuously, but ensure the user can reach you when needed.
`.trim()

export const SET_NEXT_CELL = {
  type: 'function',
  name: 'set_next_cell',
  strict: true,
  description: 'Submit the complete JavaScript cell to run after the current cell finishes, with a short summary.',
  parameters: {
    type: 'object',
    properties: { code: { type: 'string' }, summary: { type: 'string', maxLength: 256 } },
    required: ['code', 'summary'],
    additionalProperties: false,
  },
}

export type Submit = (code: string, summary: string) => void
export const REASONING_EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const
export type ReasoningEffort = (typeof REASONING_EFFORTS)[number]

/**
 * `new OpenAI()`, or any object whose `responses.create(body)` resolves to a Response shaped like the SDK's (with
 * `output_text`) and rejects with an `APIError` when the request fails. `options.signal` cancels the request, as
 * the SDK's does.
 */
export interface Client {
  responses: { create(body: Record<string, any>, options?: { signal?: Signal }): Promise<any> }
}

type Params = Record<string, any>
/** What the agent binds per cell: where set_next_cell lands, the cell's logger, and the run's stop signal. */
type Host = { submit: Submit; logger?: Logger; signal?: Signal }

/**
 * The agent's mind: instructions, model and the `set_next_cell` tool.
 *
 * Loaded only by `call_me` (`wake`): each cell wakes this mind with the input it builds to decide the next cell.
 */
export class Mind {
  readonly instructions: string

  constructor(
    readonly client: Client,
    readonly model: string,
    instructions: string,
    readonly efforts: { lowestReasoningEffort?: ReasoningEffort; reasoningEffort?: ReasoningEffort } = {},
  ) {
    const { lowestReasoningEffort = 'medium', reasoningEffort = 'medium' } = efforts
    for (const effort of [lowestReasoningEffort, reasoningEffort]) {
      if (!REASONING_EFFORTS.includes(effort)) throw new Error(`Unknown reasoning effort: ${JSON.stringify(effort)}`)
    }
    this.efforts = { lowestReasoningEffort, reasoningEffort }
    this.instructions =
      instructions +
      '\n\n\n## Reasoning effort\n\n' +
      `For \`call_me\`, \`reasoning_effort\` (default) is \`${reasoningEffort}\` and ` +
      `\`lowest_reasoning_effort\` (minimum) is \`${lowestReasoningEffort}\`. ` +
      'Omitting `reasoning.effort` uses the default. Values below the minimum are raised to it, ' +
      'including the default.'
  }

  /**
   * Wake the mind. Exposed to cells as `call_me`; see core/agent.py `Mind.wake` for every field's rule.
   *
   * The returned output contains every round's items and host tool results, in order. Usage treats the wake as one
   * logical request: keep the first reported input usage, sum output usage, and recompute total_tokens. Its custom
   * `incomplete` flag is true if any round omits usage. It is null only if no round reports usage. Other Response
   * fields (id, status, etc.) describe the final API response.
   *
   * submit: where `set_next_cell(code, summary)` lands. The agent binds a fresh one per cell, so a late call from a
   * finished cell cannot choose the next one. signal: once aborted, no request is sent and one in flight is cancelled.
   */
  async wake(input: unknown = [], params: Params = {}, { submit, logger, signal }: Host): Promise<any> {
    if (signal?.aborted) throw signal.reason
    const {
      background,
      previous_response_id,
      conversation,
      input: rawInput,
      ...body
    } = mergeExtraBody({
      ...params,
      input,
    })
    const { lowestReasoningEffort, reasoningEffort } = this.efforts as Required<Mind['efforts']>
    const reasoning = body.reasoning ?? {}
    const rank = (effort: ReasoningEffort) => REASONING_EFFORTS.indexOf(effort)
    const requested = REASONING_EFFORTS.includes(reasoning.effort) ? reasoning.effort : reasoningEffort
    const effort = rank(requested) < rank(lowestReasoningEffort) ? lowestReasoningEffort : requested
    Object.assign(body, {
      model: this.model,
      instructions: this.instructions,
      reasoning: { ...reasoning, effort },
      tools: [...(body.tools ?? []).filter((t: any) => t?.name !== SET_NEXT_CELL.name), SET_NEXT_CELL],
      tool_choice: 'auto',
      parallel_tool_calls: false,
      store: false,
      stream: false,
    })
    let items = typeof rawInput === 'string' ? [{ role: 'user', content: rawInput }] : [...rawInput]
    const outputs: any[] = []
    let usage: any = null
    let usageIncomplete = false // Whether any round omitted usage.
    const metadata = { request_id: uuid(), started_at: new Date().toISOString(), source: 'call_me' }
    logger?.('model_request', { ...metadata, request: { ...body, input: items } })
    while (true) {
      let response: any
      try {
        response = await this.client.responses.create({ ...body, input: items }, { signal })
        if (response.status === 'failed') {
          // Provider errors can arrive after generation begins.
          throw new APIError(`Response ${response.id} failed: ${JSON.stringify(response.error)}`)
        }
      } catch (error) {
        logger?.('model_error', { ...metadata, error: errorInfo(error), output: outputs })
        throw error
      }
      outputs.push(...response.output)
      usageIncomplete ||= response.usage == null
      usage = addUsage(usage, response.usage)
      const call = response.output.find(
        (item: any) => item.type === 'function_call' && item.name === SET_NEXT_CELL.name,
      )
      if (call === undefined) {
        if (usage !== null) usage.incomplete = usageIncomplete
        response = { ...response, output: outputs, output_text: outputText(outputs), usage }
        logger?.('model_response', { ...metadata, response })
        return response
      }
      const output = {
        id: 'fco_' + uuid(),
        type: 'function_call_output',
        call_id: call.call_id,
        status: 'completed',
        output: runHostTool(call, submit),
      }
      outputs.push(output)
      // Stateless requests replay each model item and tool result exactly once.
      items = [...items, ...response.output, output]
    }
  }

  /**
   * A plain model call used as a function (summarize, extract, classify, ...).
   *
   * No predefined agent instructions or host tool are added, so it cannot schedule a cell and is safe to call
   * concurrently. Exposed to cells as `call_llm_api`; resolves to the Response or event stream unchanged. Only a
   * default model and logging are added; parameters and failed responses pass through unchanged.
   */
  async query(input: unknown, params: Params = {}, { logger, signal }: Omit<Host, 'submit'> = {}): Promise<any> {
    if (signal?.aborted) throw signal.reason
    const request = { model: this.model, ...mergeExtraBody({ ...params, input }) }
    const metadata = { request_id: uuid(), started_at: new Date().toISOString(), source: 'call_llm_api' }
    logger?.('model_request', { ...metadata, request })
    let response: any
    try {
      response = await this.client.responses.create(request, { signal })
    } catch (error) {
      logger?.('model_error', { ...metadata, error: errorInfo(error) })
      throw error
    }
    if (Symbol.asyncIterator in Object(response)) logger?.('model_stream_created', metadata)
    else logger?.('model_response', { ...metadata, response })
    return response
  }
}

/** Keep initial input usage and accumulate generated output for one logical wake. */
function addUsage(total: any, current: any): any {
  if (current == null) return total
  if (total === null) return JSON.parse(JSON.stringify(current))
  const sum = (a: any, b: any) => (a != null && b != null ? a + b : null)
  total.output_tokens += current.output_tokens
  total.total_tokens = total.input_tokens + total.output_tokens
  const details = total.output_tokens_details
  const incoming = current.output_tokens_details ?? {}
  for (const key of Object.keys(details ?? {})) details[key] = sum(details[key], incoming[key])
  if ('cost' in total) total.cost = sum(total.cost, current.cost) // Actual provider cost still includes every round.
  return total
}

/** The SDK's `Response.output_text`: every output_text part of every message, in order. */
export const outputText = (output: any[]): string =>
  output
    .flatMap(item => (item.type === 'message' ? item.content : []))
    .filter(part => part.type === 'output_text')
    .map(part => part.text)
    .join('')

/** Run a `set_next_cell` call; the JSON result goes back to the mind, so a rejected submission is not thrown. */
function runHostTool(call: any, submit: Submit): string {
  try {
    const args = JSON.parse(call.arguments)
    if (!args.code?.trim()) throw new Error('code must be a non-empty string')
    submit(args.code, args.summary)
    return JSON.stringify({ scheduled: true })
  } catch (error) {
    return JSON.stringify({ scheduled: false, error: (error as Error).message ?? String(error) })
  }
}

const errorInfo = (error: any) => ({ type: error?.name ?? typeof error, message: String(error?.message ?? error) })

export interface CellRecord {
  code: string
  summary: string
  output: string
  error: string | null
  status: 'running' | 'completed' | 'error'
}

/** The host's execution records; cells receive copies. */
export class Memory {
  history: CellRecord[] = [] // One record per finished cell.

  /** Number of finished cells, which is also the number of the running one. */
  get cellCount(): number {
    return this.history.length
  }

  /** After a cell: keep its original record, with the full output. */
  record(code: string, summary: string, { output, error }: CellResult): void {
    this.history.push({ code, summary, output, error, status: error !== null ? 'error' : 'completed' })
  }

  /** Copies for cells to read as history; editing them cannot alter the originals. */
  snapshot(): CellRecord[] {
    return this.history.map(record => ({ ...record }))
  }
}

/**
 * The next cell chosen during the current one. Closed once that cell ends, so a late `set_next_cell` from it fails
 * instead of overwriting a newer cell's choice. (No lock: JavaScript runs it on one thread.)
 */
export class Slot {
  code: string | null = null
  summary: string | null = null
  closed = false

  submit = (code: string, summary: string): void => {
    if (this.closed) throw new Error('This cell has finished; its call_me can no longer choose a cell')
    this.code = code // The last submission wins.
    this.summary = summary
  }
}

/** A mind with a session and a memory, alive while the loop runs. */
export class Agent {
  static readonly REPAIR = `
The previous cell failed (its error is in history).
If needed, inspect session variables via Object.keys(globalThis) and history to recover the task and progress.
Repair the failure and continue the program.
`.trim()

  /**
   * maxFailures: consecutive failed cells before the run ends; a repair that cannot run (e.g. call_me itself fails)
   * must not loop. log: where each run's JSONL records go (core/agent.py: logs/*.jsonl, or $FIBONA_LOG_DIR).
   */
  constructor(
    readonly mind: Mind,
    readonly opts: { maxFailures?: number; log?: LogSink } = {},
  ) {}

  /**
   * Execute bootstrapCell verbatim, then run the cells selected by its program.
   *
   * Startup policy, user tasks and UI composition belong in the supplied JavaScript code. Pass an environment for
   * input/output and an onCell callback to observe execution. Without them, the agent runs with captured output and
   * no user input. onResult receives each cell's final validated result, including the cell that quits. apiDocs, the
   * package's openai_responses_api_docs.json parsed, is bound as `openai_responses_api_docs`, which the instructions
   * point to (core/agent.py loads it from agent_assets).
   * Aborting signal ends the run with its reason (core/: KeyboardInterrupt); after maxFailures cells fail in a row,
   * an Error ends the run.
   */
  async run(
    bootstrapCell: string,
    {
      env = new JSEnv(),
      onCell,
      onResult,
      apiDocs,
      signal,
    }: {
      env?: JSEnv
      onCell?: (number: number, summary: string, code: string) => void
      onResult?: (number: number, result: CellResult) => void
      apiDocs: Record<string, string>
      signal?: Signal
    },
  ): Promise<void> {
    if (apiDocs == null) throw new Error('apiDocs is required: the parsed fibona-agent/openai_responses_api_docs.json')
    const { maxFailures = 5, log } = this.opts
    const logger = runLogger(log)
    env.bind({ openai_responses_api_docs: apiDocs })
    const memory = new Memory()

    let code = bootstrapCell
    let summary = 'Run the bootstrap cell.'
    let failures = 0
    while (true) {
      if (signal?.aborted) throw signal.reason
      const slot = new Slot()
      const current: CellRecord = { code, summary, output: '', error: null, status: 'running' }
      const cellLogger = logger(memory.cellCount)
      let quitting = false // Holds even if the cell catches the Quit.
      // Restore runtime bindings each cell; mutations to shared objects still persist.
      env.bind({
        call_me: (input?: unknown, params?: Params) =>
          this.mind.wake(input, params, { submit: slot.submit, logger: cellLogger, signal }),
        call_llm_api: (input: unknown, params?: Params) =>
          this.mind.query(input, params, { logger: cellLogger, signal }),
        quit: () => {
          quitting = true
          throw new Quit()
        },
        history: [...memory.snapshot(), current],
      })
      onCell?.(memory.cellCount, summary, code)
      const result = await env.execute(code, {
        logger: cellLogger,
        summary,
        onOutput: text => (current.output += text),
        signal,
      })
      slot.closed = true
      // A Quit the cell caught still ends the run, as Python's BaseException would have.
      if (quitting) Object.assign(result, { quit: true, error: null })
      if (!result.quit && result.error === null && slot.code === null) {
        result.error = 'The cell finished without choosing a next cell via set_next_cell.'
      }
      Object.assign(current, { output: result.output, error: result.error })
      current.status = result.error !== null ? 'error' : 'completed'
      cellLogger?.('cell_result', { ...result })
      onResult?.(memory.cellCount, result)
      if (result.quit) return
      memory.record(code, summary, result)

      if (result.error === null) {
        failures = 0
        code = slot.code!
        summary = slot.summary!
        continue
      }
      if (++failures >= maxFailures) {
        throw new Error(`${failures} cells failed in a row; last error:\n${result.error.slice(-4000)}`)
      }
      // Side effects of the failed cell are not rolled back; the mind repairs from here. The recovery cell only
      // wakes the mind with the failure so it can submit a repaired next cell.
      const message = JSON.stringify({
        bootstrap_cell: bootstrapCell,
        code: code.slice(0, 24000),
        error: result.error.slice(-8000),
      })
      code = `response = await call_me(${JSON.stringify(Agent.REPAIR + '\n\n' + message)})\nprint(response.output_text)`
      summary = 'Repair the failed cell.'
    }
  }
}
