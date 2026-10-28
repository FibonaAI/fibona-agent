/** Persistent native Node REPL; no model, session, capture or interaction policy. */
import { AsyncLocalStorage } from "node:async_hooks";
import { start } from "node:repl";
import { PassThrough } from "node:stream";

export class REPL {
  scope = new AsyncLocalStorage();
  active = undefined;

  constructor() {
    this.input = new PassThrough();
    this.output = new PassThrough();
    this.output.resume();
    this.server = start({ input: this.input, output: this.output, terminal: false, prompt: "", useGlobal: false });
    // The native evaluator routes runtime exceptions through its domain, not eval's callback.
    this.server._domain.on("error", error => this.active?.reject(error));
    this.history = [""];
    this.bind({ run_cell: code => this.execute(code), In: this.history, console });
  }

  bind(names) {
    // Shadow native accessors locally rather than invoking setters on host globals.
    for (const [name, value] of Object.entries(names)) {
      Object.defineProperty(this.server.context, name, { value, writable: true, configurable: true, enumerable: true });
    }
  }

  async execute(code) {
    if (this.active !== this.scope.getStore()) {
      throw new Error("Concurrent cells are not supported; await run_cell directly");
    }
    const parent = this.active;
    const frame = {};
    this.active = frame;
    this.history.push(code);
    try {
      return await this.scope.run(frame, () => new Promise((resolve, reject) => {
        frame.reject = reject;
        this.server.eval(code + "\n", this.server.context, `cell-${this.history.length - 1}.js`, (error, value) => {
          if (error) reject(error);
          else resolve(value);
        });
      }));
    } finally {
      this.active = parent;
    }
  }

  close() {
    this.server.close();
    this.input.destroy();
    this.output.destroy();
    this.scope.disable();
  }
}
