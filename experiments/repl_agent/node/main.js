/** Run the agent in its persistent execution environment. */
import { readFile } from "node:fs/promises";
import { REPL } from "./repl.js";

const code = await readFile(new URL("./agent.js", import.meta.url), "utf8");
const repl = new REPL();
try {
  await repl.execute(code);
} finally {
  repl.close();
}
