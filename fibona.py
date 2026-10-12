"""Run a built-in bootstrap or a Python file as Fibona's bootstrap cell."""

import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI

from core.agent import INSTRUCTIONS, Agent, Mind
from core.env import IPythonEnv


def main() -> int:
    bootstrap_dir = Path(__file__).resolve().parent / "bootstraps"
    bootstraps = {path.parent.name: path for path in sorted(bootstrap_dir.glob("*/init.py")) if path.is_file()}
    parser = argparse.ArgumentParser(description="Run a built-in bootstrap or a Python file as the bootstrap cell.")
    bootstrap = parser.add_mutually_exclusive_group(required=True)
    bootstrap.add_argument("--bootstrap-mode", choices=list(bootstraps), help="Select an included bootstrap.")
    bootstrap.add_argument("--bootstrap-file", type=Path, help="Python file to execute as the bootstrap cell.")
    parser.add_argument("--debug", action="store_true", help="Record cells and model calls in agent.log.")
    args = parser.parse_args()
    bootstrap_file = (
        args.bootstrap_file.expanduser() if args.bootstrap_file is not None else bootstraps[args.bootstrap_mode]
    )
    try:
        bootstrap_cell = bootstrap_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        parser.error(str(error))
    if not bootstrap_cell.strip():
        parser.error("bootstrap cell must contain Python code")
    try:
        with OpenAI() as client:
            mind = Mind(client, os.getenv("FIBONA_MODEL", "gpt-6-astra"), INSTRUCTIONS)
            env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
            Agent(mind, cwd=".").run(bootstrap_cell, env=env, debug=args.debug)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
