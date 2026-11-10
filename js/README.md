# fibona-agent (JavaScript)

Fibona's core in JavaScript: the model writes the next cell of the program it runs in. A one-to-one port of
[`core/`](https://github.com/FibonaAI/fibona-agent/tree/main/core); the
[project README](https://github.com/FibonaAI/fibona-agent#readme) explains the idea.

## Install

```bash
npm install https://github.com/FibonaAI/fibona-agent/releases/download/js-v0.1.0/fibona-agent-0.1.0.tgz
npm install openai  # optional: the client used below
```

The package has no runtime dependencies. The client is yours to supply: `new OpenAI()`, or any object whose
`responses.create(body, { signal })` resolves to a Response with `output_text`.

## Use

```js
import OpenAI from 'openai'
import { Agent, BOOTSTRAP, INSTRUCTIONS, JSEnv, Mind } from 'fibona-agent'

const env = new JSEnv({ stdout: text => process.stdout.write(text), stderr: text => process.stderr.write(text) })
env.bind({ task: 'Print the first 10 primes, then call quit().' })

const mind = new Mind(new OpenAI(), 'gpt-6-astra', INSTRUCTIONS)
await new Agent(mind).run(BOOTSTRAP, { env })
```

- The first argument of `run` is the bootstrap cell; everything after it is chosen by the model. `BOOTSTRAP` is the
  default one: it reads `task` (or asks `input()`), sets up `task_state`, `variables()` and `build_context()`, and merges
  into `instructions` and `var_docs` if you bound them. It is `[CONTEXT, STOP, START].join('\n\n')`: use the
  parts to leave one out or put your own in between. Any cell source works, e.g. `'await call_me(task)'`.
- `JSEnv` takes `readInput` for `input()`, `Agent` takes `log` for JSONL records, and `run` takes `signal` to end the run.
- To stop one cell and keep the run, call the session's `stop()` (defined by `BOOTSTRAP`) from your Ctrl+C or button:
  the cell's model call fails, and the next cell waits for the user's message via `input()`.
- Without a module system, load `dist/agent.global.js` and use the `Fibona` global.

Cells are model-written code evaluated in the global scope of your process, with everything it can reach. Run one
agent per realm, and only where that is acceptable.
