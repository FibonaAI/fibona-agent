"""Stopping a cell with SIGUSR1, and the run after it. Run: uv run python -m unittest tests.core_env_test

Stops are sent as a UI sends them, with SIGUSR1. Raised on the main thread, the signal lands right where it is
raised, so each case is deterministic; only a stop during a blocking call needs a second thread.
"""

import faulthandler
import signal
import threading
import time
import unittest
from types import SimpleNamespace

from core.agent import Agent
from core.env import IPythonEnv, Quit

STOP = "signal.raise_signal(signal.SIGUSR1)"


def setUpModule():
    faulthandler.dump_traceback_later(30, exit=True)  # A stop that never lands hangs; fail instead.


def tearDownModule():
    faulthandler.cancel_dump_traceback_later()


class StopTest(unittest.TestCase):
    def setUp(self):
        self.env = IPythonEnv()
        self.env.execute("import asyncio, signal, time")  # Also makes this env the owner of SIGUSR1's handler.
        self.ns = self.env.shell.user_ns

    def test_stop_in_cell_code(self):
        """A stop in the cell's own code raises Stop there: `except Exception` cannot catch it, output so far and
        earlier variables are kept, and the session keeps working."""
        self.env.execute("before = 1")
        result = self.env.execute(f"print('partial')\ntry:\n    {STOP}\n    after = 1\nexcept Exception:\n    pass")
        self.assertTrue(result.stopped)
        self.assertEqual(result.error, "Stopped by the user.")
        self.assertEqual(result.output, "partial\n")  # Kept; `except Exception` cannot swallow Stop.
        self.assertNotIn("after", self.ns)
        self.assertEqual(self.ns["before"], 1)
        self.assertIsNone(self.env.execute("x = 1").error)  # The session keeps working.

    def test_stop_interrupts_blocking_call(self):
        """A stop from another thread interrupts the main thread's blocking call (as a model request would be)
        instead of waiting for it to return."""
        ready = threading.Event()
        self.env.bind(ready=ready)
        main = threading.main_thread().ident
        threading.Thread(target=lambda: (ready.wait(), signal.pthread_kill(main, signal.SIGUSR1))).start()
        started = time.monotonic()
        result = self.env.execute("ready.set()\ntime.sleep(30)")
        self.assertTrue(result.stopped)
        self.assertLess(time.monotonic() - started, 5)

    def test_stop_cancels_awaiting_cell(self):
        """A cell suspended at await is off the stack, so its task is cancelled; its finally still runs.

        call_soon raises the signal once the task has suspended, when only the loop is on the stack."""
        code = "asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGUSR1)\n"
        result = self.env.execute(code + "try:\n    await asyncio.sleep(30)\nfinally:\n    print('cleaned')")
        self.assertTrue(result.stopped)
        self.assertEqual(result.output, "cleaned\n")

    def test_stopped_async_cell_does_not_resume(self):
        """A stopped async cell is really over: it does not resume while the next async cell runs the loop."""
        code = "asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGUSR1)\n"
        self.assertTrue(self.env.execute(code + "await asyncio.sleep(0.2)\nprint('resumed')\nresumed = 1").stopped)
        result = self.env.execute("await asyncio.sleep(0.5)")
        self.assertEqual(result.output, "")
        self.assertNotIn("resumed", self.ns)

    def test_own_cancelled_error_is_an_error(self):
        """A CancelledError the cell causes itself, with no stop, stays an ordinary error for repair."""
        result = self.env.execute("t = asyncio.ensure_future(asyncio.sleep(10))\nt.cancel()\nawait t")
        self.assertFalse(result.stopped)
        self.assertIn("CancelledError", result.error)

    def test_stop_between_cells_skips_next_cell(self):
        """A stop sent between cells is kept: the next cell is stopped before it runs, and only that one."""
        signal.raise_signal(signal.SIGUSR1)
        result = self.env.execute("ran = 1")
        self.assertTrue(result.stopped)
        self.assertNotIn("ran", self.ns)
        self.assertIsNone(self.env.execute("ran = 1").error)  # The request is used once.
        self.assertEqual(self.ns["ran"], 1)

    def test_stop_in_host_hook_waits_for_next_cell(self):
        """A stop landing in a host hook does not cut the hook short; the finished cell stands, and the next
        cell is stopped before it runs."""
        done = []

        def hook(result):
            signal.raise_signal(signal.SIGUSR1)
            done.append(True)  # The hook is not cut short.

        self.env.shell.events.register("post_run_cell", hook)
        result = self.env.execute("x = 1")
        self.env.shell.events.unregister("post_run_cell", hook)
        self.assertFalse(result.stopped)
        self.assertEqual(done, [True])
        result = self.env.execute("ran = 1")
        self.assertTrue(result.stopped)
        self.assertNotIn("ran", self.ns)


class FakeMind:
    """Records each wake and quits."""

    client = SimpleNamespace(api_key="")

    def __init__(self):
        self.inputs = []

    def wake(self, input=(), *, submit, log=None, **body):
        self.inputs.append(input)
        raise Quit

    def query(self, input, **params):
        return ""


class AgentStopTest(unittest.TestCase):
    def test_stopped_cell_waits_for_user_instead_of_repair(self):
        env = IPythonEnv(read_input=lambda prompt="": "carry on")
        mind, results = FakeMind(), []
        Agent(mind).run(f"import signal\nprint('working')\n{STOP}", env=env, on_result=lambda n, r: results.append(r))
        self.assertTrue(results[0].stopped)
        self.assertEqual(len(mind.inputs), 1)  # No repair wake: the only wake carries the user's reply.
        self.assertIn("stopped", mind.inputs[0])
        self.assertTrue(mind.inputs[0].endswith("carry on"))
        record = env.shell.user_ns["history"][0]
        self.assertEqual((record["output"], record["error"]), ("working\n", "Stopped by the user."))


if __name__ == "__main__":
    unittest.main()
