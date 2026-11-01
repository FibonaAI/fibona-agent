"""Run a built-in bootstrap or a Python file as Fibona's bootstrap cell."""

import argparse
import os
import sys
from functools import partial
from pathlib import Path

from openai import OpenAI

from core.agent import INSTRUCTIONS, REASONING_EFFORTS, Agent, Mind
from core.env import IPythonEnv


def main() -> int:
    bootstrap_dir = Path(__file__).resolve().parent / "bootstraps"
    parser = argparse.ArgumentParser(
        description="Run a built-in bootstrap or a Python file as the bootstrap cell.", allow_abbrev=False
    )
    parser.add_argument(
        "--boot-mode", required=True, help="minimal, terminal, or a Python file to execute as the bootstrap cell."
    )
    parser.add_argument(
        "--lowest-reasoning-effort",
        choices=REASONING_EFFORTS,
        default="medium",
        help="Minimum reasoning effort for call_me (default: medium).",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default="medium",
        help="Default reasoning effort for call_me, raised to the minimum if needed (default: medium).",
    )
    speed = parser.add_mutually_exclusive_group()
    speed.add_argument(
        "--fast", dest="service_tier", action="store_const", const="priority", help="Use priority service tier."
    )
    speed.add_argument(
        "--ultrafst",
        "--ultrafast",
        dest="service_tier",
        action="store_const",
        const="ultrafast",
        help="Use ultrafast service tier.",
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
    model = os.getenv("FIBONA_MODEL", "gpt-6-astra")
    api_key = os.getenv("FIBONA_API_KEY")
    if not api_key:
        parser.error("Set FIBONA_API_KEY to your API key.")
    try:
        with OpenAI(
            api_key=api_key,
            base_url=os.getenv("FIBONA_BASE_URL", "https://api.openai.com/v1"),
            max_retries=int(os.getenv("FIBONA_API_RETRIES", "2")),
        ) as client:
            if args.service_tier:
                client.responses.create = partial(client.responses.create, service_tier=args.service_tier)
            mind = Mind(
                client,
                model,
                INSTRUCTIONS,
                lowest_reasoning_effort=args.lowest_reasoning_effort,
                reasoning_effort=args.reasoning_effort,
            )
            env = IPythonEnv(stdout=sys.stdout, stderr=sys.stderr, read_input=input)
            env.bind(argv=remaining_args)  # Arguments this launcher did not consume, for the bootstrap to read.
            Agent(mind, cwd=".").run(bootstrap_cell, env=env)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
