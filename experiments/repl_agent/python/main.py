"""Run the agent in its persistent execution environment."""

import asyncio
import sys
from pathlib import Path

from repl import REPL


async def main():
    code = Path(__file__).with_name("agent.py").read_text(encoding="utf-8")
    repl = REPL()
    try:
        await repl.execute(code)
    finally:
        repl.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
