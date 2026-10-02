import argparse
import os
import sys

from openai import OpenAI

from core.agent import INSTRUCTIONS, Agent, Mind
from core.env import IPythonEnv


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Fibona Agent.")
    parser.add_argument("task", help="Task to run (quote it if it contains spaces).")
    parser.add_argument("--debug", action="store_true", help="Record cells and model calls in agent.log.")
    args = parser.parse_args()
    if not args.task.strip():
        parser.error("task must not be empty")
    try:
        with OpenAI() as client:
            mind = Mind(client=client, model=os.getenv("FIBONA_MODEL", "gpt-6-astra"), instructions=INSTRUCTIONS)
            env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
            Agent(mind, cwd=".").run(args.task, env=env, debug=args.debug)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
