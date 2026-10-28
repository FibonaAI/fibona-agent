import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.agent import Agent
from core.env import IPythonEnv


class LiveHistoryTests(unittest.TestCase):
    def test_current_output_and_finished_history(self):
        stream = io.StringIO()
        env = IPythonEnv(stdout=stream, stderr=stream)
        mind = Mock()
        next_cell = """
assert len(history) == 2
assert history[0]['status'] == 'completed'
assert previous['status'] == 'completed'
assert history[-1]['status'] == 'running'
assert history[0]['output'] == 'hello world!'
assert history[-1]['output'] == ''
assert previous['output'] == 'hello world!'
print('next', end='')
assert history[-1]['output'] == 'next'
quit()
"""

        def wake(*, submit, **kwargs):
            self.assertEqual(env.shell.user_ns["history"][-1]["output"], "hello world")
            submit(next_cell, "Next cell")

        mind.wake.side_effect = wake
        code = """
import sys
assert len(history) == 1
assert history[-1]['status'] == 'running'
assert history[-1]['output'] == ''
previous = history[-1]
print('hello', end='')
assert previous['output'] == 'hello'
sys.stderr.write(' world')
assert previous['output'] == 'hello world'
call_me()
print('!', end='')
"""
        results = []
        with tempfile.TemporaryDirectory() as cwd:
            Agent(mind, cwd=cwd).run(code, env=env, on_result=lambda i, r: results.append(r))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.error is None for r in results))
        self.assertTrue(results[-1].quit)
        self.assertEqual(env.shell.user_ns["history"][-1]["status"], "completed")
        self.assertEqual(stream.getvalue(), "hello world!next")

    def test_failure_finalizes_current_record(self):
        env = IPythonEnv()
        mind = Mock()

        def repair(*args, submit, **kwargs):
            history = env.shell.user_ns["history"]
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["status"], "error")
            self.assertEqual(history[-1]["status"], "running")
            self.assertEqual(history[0]["output"], "before failure\n")
            self.assertIn("ValueError: broken", history[0]["error"])
            self.assertEqual(env.shell.user_ns["previous"], history[0])
            self.assertEqual(history[-1]["output"], "")
            submit("quit()", "Done")
            return SimpleNamespace(output_text="repaired")

        mind.wake.side_effect = repair
        with tempfile.TemporaryDirectory() as cwd:
            Agent(mind, cwd=cwd).run(
                "previous = history[-1]\nprint('before failure')\nraise ValueError('broken')", env=env
            )
        mind.wake.assert_called_once()


if __name__ == "__main__":
    unittest.main()
