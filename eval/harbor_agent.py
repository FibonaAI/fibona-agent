"""Harbor installed agent: runs Fibona with BOOTSTRAP once per task inside the task container.

run.py mounts /opt/fibona (wheels, uv and Python, synced by `make sync`), so install runs offline.
"""

import os
import shlex

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template

# The bootstrap cell: the terminal bootstrap's working context without its UI.
# The task comes from the command line. Nobody answers input(), so the agent ends the run with quit().
BOOTSTRAP = '''
from bootstraps.terminal.context import initialize_context

instructions = """
You are running unattended in a benchmark container: nobody reads your output or answers input().
When the task is done and verified, call quit().
""".strip()
task = " ".join(argv)  # fibona.py binds the arguments it did not consume.
build_context = initialize_context(globals())
call_me(  # Core binds call_me.
    input=[*build_context(), {"role": "user", "content": "Start the user's task and submit the next Python cell."}]
)
'''.lstrip()


class Fibona(BaseInstalledAgent):
    @staticmethod
    def name() -> str:
        return "fibona"

    async def install(self, environment) -> None:
        await self.exec_as_agent(
            environment,
            command="set -euo pipefail; UV_PYTHON_INSTALL_DIR=/opt/fibona/python /opt/fibona/dist/uv tool install "
            "--python 3.12 --offline --no-index --find-links /opt/fibona/dist /opt/fibona/dist/fibona_agent-*.whl; "
            f"printf %s {shlex.quote(BOOTSTRAP)} > $HOME/fibona-bootstrap.py",
        )

    @with_prompt_template
    async def run(self, instruction, environment, context) -> None:
        env = {k: os.environ[k] for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL") if os.environ.get(k)}
        env["FIBONA_MODEL"] = (self.model_name or "gpt-6-astra").split("/")[-1]
        await self.exec_as_agent(
            environment,
            command="$HOME/.local/bin/fibona --boot-mode $HOME/fibona-bootstrap.py "
            f"{shlex.quote(instruction)} < /dev/null 2>&1 | tee /logs/agent/fibona.txt",
            env=env,
        )
