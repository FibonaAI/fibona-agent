"""The terminal bootstrap's stop, end to end. Run: uv run python -m unittest bootstraps.terminal.stop_test

The real bootstrap cell (init.py) runs under core with a fake mind; keys go through a prompt_toolkit pipe and
output to DummyOutput, so a stop is sent as the user sends it, with Ctrl+C.
"""

import faulthandler
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from core.agent import Agent
from core.env import IPythonEnv, Quit

INIT = (Path(__file__).parent / "init.py").read_text()
UI = "Terminal(get_ipython())"
assert UI in INIT
BOOTSTRAP = INIT.replace(UI, "Terminal(get_ipython(), input=pipe, output=DummyOutput())")


def setUpModule():
    faulthandler.dump_traceback_later(60, exit=True)  # A stop that never lands hangs; fail instead.


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()


class FakeMind:
    """Chooses the given cells in order, recording each wake, then quits."""

    model = "fake"

    def __init__(self, cells):
        self.cells, self.inputs = list(cells), []
        self.client = SimpleNamespace(api_key="", responses=SimpleNamespace(create=None))

    def wake(self, input=(), *, submit, log=None, **body):
        self.inputs.append(input)
        if not self.cells:
            raise Quit
        submit(self.cells.pop(0), "next")
        return SimpleNamespace(output_text="")

    def query(self, input, **params):
        return ""


class StopTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()  # The terminal saves its notebook under ~/.fibona.
        self.addCleanup(home.cleanup)
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ["HOME"])
        os.environ["HOME"] = home.name
        self.env = IPythonEnv()
        self.ns = self.env.shell.user_ns

    def run_bootstrap(self, cells, keys):
        """Run init.py; keys is a list of (status prefix to wait for, text to type) after the task is entered."""
        mind = FakeMind(cells)
        with create_pipe_input() as pipe:
            self.env.bind(pipe=pipe, DummyOutput=DummyOutput)

            def status():
                return getattr(self.ns.get("terminal"), "status", "")

            def type_keys():
                for waiting, text in [("Waiting for input", "the task\r"), *keys]:
                    while not status().startswith(waiting):
                        time.sleep(0.01)
                    pipe.send_text(text)
                    while status().startswith(waiting):  # Until the UI has taken the keys.
                        time.sleep(0.01)

            threading.Thread(target=type_keys, daemon=True).start()
            Agent(mind).run(BOOTSTRAP, env=self.env)
        return mind, self.ns["terminal"].cells

    def test_ctrl_c_stops_cell_and_waits_with_context(self):
        """Ctrl+C stops a running cell (■); core's repair cell instead waits for the user and wakes the mind with
        the bootstrap's context and the user's message."""
        mind, cells = self.run_bootstrap(
            ["import time\ntime.sleep(30)"], [("Running · cell 1", "\x03"), ("Waiting for input", "carry on\r")]
        )
        self.assertEqual(cells[1]["state"], "■")
        self.assertEqual(cells[2]["summary"], "Wait for the user after a stop.")
        wake = mind.inputs[1]
        self.assertEqual(wake[0]["role"], "developer")  # build_context's instructions.
        self.assertIn("stopped cell 1", wake[-1]["content"])
        self.assertTrue(wake[-1]["content"].endswith("carry on"))

    def test_stop_cancels_awaiting_cell(self):
        """A cell suspended at await is cancelled, not resumed later while the next async cell runs the loop.

        call_soon raises the signal once the cell's task has suspended, when only the loop is on the stack."""
        awaiting = "import asyncio\nasyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGUSR1)\n"
        _, cells = self.run_bootstrap(
            [awaiting + "await asyncio.sleep(0.3)\nprint('resumed')", "await asyncio.sleep(0.6)\ncall_me(input='x')"],
            [("Waiting for input", "carry on\r")],
        )
        self.assertEqual(cells[1]["state"], "■")
        self.assertNotIn("resumed", "".join(record["output"] for record in self.ns["history"]))

    def test_stop_in_host_hook_lands_in_next_cell(self):
        """A stop landing in a host hook does not cut it short; it is resent until it lands in the next cell."""

        def stop_after_cell_1(result):
            if result.info.raw_cell.startswith("call_me(input='more')"):
                signal.raise_signal(signal.SIGUSR1)

        self.env.shell.events.register("post_run_cell", stop_after_cell_1)
        _, cells = self.run_bootstrap(
            ["call_me(input='more')", "import time\ntime.sleep(30)"], [("Waiting for input", "carry on\r")]
        )
        self.assertEqual((cells[1]["state"], cells[2]["state"]), ("✓", "■"))

    def test_broken_context_falls_back_to_repair(self):
        """If the wait after a stop fails (build_context is broken), core's next repair cell runs as usual."""
        mind, cells = self.run_bootstrap(
            ["build_context = None\nimport time\ntime.sleep(30)"],
            [("Running · cell 1", "\x03"), ("Waiting for input", "carry on\r")],
        )
        self.assertEqual(cells[2]["state"], "✗")
        self.assertTrue(mind.inputs[1].startswith("The previous cell failed"))


if __name__ == "__main__":
    unittest.main()
