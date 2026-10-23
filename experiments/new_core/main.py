"""Create an execution environment and evaluate the selected bootstrap."""

import argparse
import asyncio
import sys
from pathlib import Path
from runpy import run_path


async def main():
    parser = argparse.ArgumentParser(description="Launch a runtime and a cell-resident bootstrap.")
    parser.add_argument("--runtime", default="python")
    parser.add_argument("--bootstrap", default="python")
    selected = parser.parse_args()
    directory = Path(__file__).resolve().parent
    bootstrap = Path(selected.bootstrap)
    if bootstrap.is_dir():
        bootstrap = bootstrap / "main.py"
    elif bootstrap.suffix != ".py":
        bootstrap = directory / "bootstraps" / selected.bootstrap / "main.py"
    code = bootstrap.read_text(encoding="utf-8")
    runtime = run_path(str(directory / "runtimes" / selected.runtime / "runtime.py"))
    repl = runtime["REPL"]()
    try:
        await repl.execute(code)
    finally:
        repl.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
