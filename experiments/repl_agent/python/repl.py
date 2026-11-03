"""Persistent Python cell execution; no agent or session policy."""

import asyncio

from IPython.core.interactiveshell import InteractiveShell
from traitlets.config import Config


class REPL:
    """Execute nested cells in one task with shared globals and top-level await."""

    def __init__(self):
        config = Config()
        config.HistoryAccessor.enabled = False
        self.shell = InteractiveShell(config=config)
        self.shell.ast_node_interactivity = "none"
        self.shell._showtraceback = lambda *args, **kwargs: None
        self.owner = None
        self.bind(run_cell=self.execute)

    def bind(self, **names):
        self.shell.user_ns.update(names)

    async def execute(self, code):
        task = asyncio.current_task()
        if self.owner is not None and self.owner is not task:
            raise RuntimeError("Concurrent cells are not supported; await run_cell directly")
        previous_owner = self.owner
        self.owner = task
        try:
            result = await self.shell.run_cell_async(
                code, store_history=True, transformed_cell=self.shell.transform_cell(code)
            )
            error = result.error_before_exec or result.error_in_exec
            if error is not None:
                raise error
        finally:
            self.owner = previous_owner

    def close(self):
        self.shell.cleanup()
