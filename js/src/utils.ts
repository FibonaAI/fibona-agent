// ↔ core/utils.py. Platform-free: the host decides where log records go.

/** One JSONL record `{time, run_id, cell, event, ...}`, as core/utils.py writes to logs/<time>-<run_id>.jsonl. */
export type LogSink = (record: Record<string, unknown>) => void
/** One cell's logger (core/utils.py: the LoggerAdapter carrying `cell`). */
export type Logger = ((event: string, data: Record<string, unknown>) => void) | undefined

/**
 * Stops the run, as Ctrl+C (KeyboardInterrupt) does in core/: an `AbortSignal`, or for hosts without one (bare
 * JavaScriptCore) any object with these members.
 */
export interface Signal {
  readonly aborted: boolean
  readonly reason?: unknown
  addEventListener(type: 'abort', listener: () => void): void
  removeEventListener(type: 'abort', listener: () => void): void
}

/** openai's `APIError`: `status` is the HTTP status, absent when no response came back. */
export class APIError extends Error {
  name = 'APIError'
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
  }
}

/** This class, or the openai SDK's (it carries `status` and `headers`). */
export const isAPIError = (error: unknown): error is { status?: number } =>
  error instanceof APIError || (error instanceof Error && 'status' in error && 'headers' in error)

/**
 * core/utils.py `_merge_extra_body`. The Python SDK merges `extra_body` into the body when sending, after the host's
 * rules, which only touch managed fields (lifted out first). With no SDK to merge later, merging it all now sends
 * the same request.
 */
export const mergeExtraBody = ({ extra_body, ...rest }: Record<string, any>): Record<string, any> => ({
  ...rest,
  ...extra_body,
})

/** core/utils.py `run_logger`: one run id; each cell gets a logger stamping it and its cell number. */
export function runLogger(sink?: LogSink): (cell: number) => Logger {
  const runId = uuid()
  return cell =>
    sink && ((event, data) => sink({ time: new Date().toISOString(), run_id: runId, cell, event, ...data }))
}

/** `uuid4().hex`. ponytail: Math.random where there is no crypto (bare JavaScriptCore); ids, not secrets. */
export const uuid = (): string =>
  (globalThis as any).crypto?.randomUUID?.().replaceAll('-', '') ??
  Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
