"""Read a Python file and start core with its source as the first cell."""

import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI

from core.agent import INSTRUCTIONS, Agent, Mind
from core.env import IPythonEnv


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Python file as Fibona's first cell.")
    parser.add_argument("cell_file", type=Path, help="Python file whose source is executed as the first cell.")
    parser.add_argument("--debug", action="store_true", help="Record cells and model calls in agent.log.")
    args = parser.parse_args()
    try:
        first_cell = args.cell_file.read_text(encoding="utf-8")
    except OSError as error:
        parser.error(str(error))
    if not first_cell.strip():
        parser.error("first-cell file must contain Python code")
    try:
        with OpenAI() as client:
            mind = Mind(client, os.getenv("FIBONA_MODEL", "gpt-6-astra"), INSTRUCTIONS)
            env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
            Agent(mind, cwd=".").run(first_cell, env=env, debug=args.debug)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
