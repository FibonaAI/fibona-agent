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
    parser = argparse.ArgumentParser(
        description="Run a built-in bootstrap or a Python file as the bootstrap cell.", allow_abbrev=False
    )
    parser.add_argument(
        "--boot-mode", required=True, help="minimal, terminal, or a Python file to execute as the bootstrap cell."
    )
    args, remaining_args = parser.parse_known_args()
    bootstrap_file = (
        bootstrap_dir / args.boot_mode / "init.py"
        if args.boot_mode in {"minimal", "terminal"}
        else Path(args.boot_mode).expanduser()
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
            env.bind(argv=remaining_args)  # Arguments this launcher did not consume, for the bootstrap to read.
            Agent(mind, cwd=".").run(bootstrap_cell, env=env)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
