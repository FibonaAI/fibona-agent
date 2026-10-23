"""The terminal bootstrap's Ctrl+C stop and its records of stopped cells. Run: uv run python -m unittest tests.terminal_test

The real Terminal runs under core with a fake mind; keys go through a prompt_toolkit pipe, output to DummyOutput.
"""

import faulthandler
import os
import signal
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from core.agent import Agent
from core.env import IPythonEnv, Quit

BOOTSTRAP = """
from bootstraps.terminal.terminal import Terminal
terminal = Terminal(get_ipython(), input=pipe, output=DummyOutput()).start()
call_me(input="go")
"""


def setUpModule():
    faulthandler.dump_traceback_later(30, exit=True)  # A stop that never lands hangs; fail instead.


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()


class FakeMind:
    """Chooses the given cells in order, then quits."""

    model = "fake"

    def __init__(self, cells):
        self.cells = list(cells)
        self.client = SimpleNamespace(api_key="", responses=SimpleNamespace(create=None))

    def wake(self, input=(), *, submit, log=None, **body):
        if not self.cells:
            raise Quit
        submit(self.cells.pop(0), "next")
        return SimpleNamespace(output_text="")

    def query(self, input, **params):
        return ""


class TerminalStopTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()  # The terminal saves its notebook under ~/.fibona.
        self.addCleanup(home.cleanup)
        self.addCleanup(os.environ.__setitem__, "HOME", os.environ["HOME"])
        os.environ["HOME"] = home.name
        self.env = IPythonEnv()
        self.ns = self.env.shell.user_ns

    def run_agent(self, cells, keys):
        """Run the terminal bootstrap; keys is a list of (status prefix to wait for, text to type)."""
        with create_pipe_input() as pipe:
            self.env.bind(pipe=pipe, DummyOutput=DummyOutput)

            def type_keys():
                for status, text in keys:
                    while not getattr(self.ns.get("terminal"), "status", "").startswith(status):
                        time.sleep(0.01)
                    pipe.send_text(text)

            threading.Thread(target=type_keys, daemon=True).start()
            Agent(FakeMind(cells)).run(BOOTSTRAP, env=self.env)
        return self.ns["terminal"].cells

    def test_ctrl_c_stops_running_cell(self):
        """Ctrl+C while a cell runs stops it (■), and the run waits for the user's message."""
        cells = self.run_agent(
            ["import time\ntime.sleep(30)"], [("Running · cell 1", "\x03"), ("Waiting for input", "carry on\r")]
        )
        self.assertEqual(cells[1]["state"], "■")
        self.assertEqual(cells[2]["summary"], "Wait for the user after a stop.")

    def test_cell_stopped_before_it_ran(self):
        """A stop landing after cell 1 finished: cell 1 stays ✓, and cell 2, which core stopped before it ran
        and so never reached IPython's events, is still shown, as ■."""

        def stop_after_cell_1(result):
            if result.info.raw_cell.startswith("call_me(input='more')"):
                signal.raise_signal(signal.SIGUSR1)

        self.env.shell.events.register("post_run_cell", stop_after_cell_1)
        cells = self.run_agent(["call_me(input='more')", "never_ran = 1"], [("Waiting for input", "carry on\r")])
        self.assertEqual(cells[1]["state"], "✓")
        self.assertEqual((cells[2]["state"], cells[2]["code"]), ("■", "never_ran = 1"))
        self.assertNotIn("never_ran", self.ns)
        self.assertEqual(cells[3]["summary"], "Wait for the user after a stop.")


if __name__ == "__main__":
    unittest.main()
