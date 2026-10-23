/** Select an execution environment and evaluate the bootstrap as cell 0. */
import { readFile, stat } from "node:fs/promises";
import { resolve, extname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: {
  runtime: { type: "string", default: "node" },
  bootstrap: { type: "string", default: "node" },
} });
const directory = fileURLToPath(new URL(".", import.meta.url));
let bootstrap = resolve(values.bootstrap);
if ((await stat(bootstrap).catch(error => {
  if (error.code !== "ENOENT") throw error;
}))?.isDirectory()) {
  bootstrap = join(bootstrap, "main.js");
} else if (extname(bootstrap) !== ".js") {
  bootstrap = join(directory, "bootstraps", values.bootstrap, "main.js");
}
const code = await readFile(bootstrap, "utf8");
const { REPL } = await import(pathToFileURL(join(directory, "runtimes", values.runtime, "runtime.js")));
const repl = new REPL();
try {
  await repl.execute(code);
} finally {
  repl.close();
}
