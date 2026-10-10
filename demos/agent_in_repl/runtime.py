"""Agent in REPL: run the core agent with an editable session bootstrap."""

import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI

from core.agent import INSTRUCTIONS, Agent, Mind
from core.env import IPythonEnv


class REPLAgent(Agent):
    """Initialize editable working instructions and context in the supplied IPython session."""

    def bootstrap(self, task: str) -> str:
        """Initialize session helpers and wake the mind in the first cell."""
        source = Path(__file__).with_name("bootstrap.py").read_text(encoding="utf-8")
        return f"task = {task!r}\n{source}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the agent in REPL demo.")
    parser.add_argument("task", help="Task to run (quote it if it contains spaces).")
    parser.add_argument("--debug", action="store_true", help="Record cells and model calls in agent.log.")
    args = parser.parse_args()
    if not args.task.strip():
        parser.error("task must not be empty")
    try:
        with OpenAI() as client:
            mind = Mind(client, os.getenv("FIBONA_MODEL", "gpt-6-astra"), INSTRUCTIONS)
            env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
            REPLAgent(mind, cwd=".").run(args.task, env=env, debug=args.debug)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
