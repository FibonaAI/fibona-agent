import io
import os
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import fibona


class CliTest(unittest.TestCase):
    def test_task_and_interrupt(self):
        with (
            patch("sys.argv", ["fibona", "Summarize these files"]),
            patch.dict(os.environ, {"FIBONA_MODEL": "test-model"}),
            patch.object(fibona, "OpenAI") as client,
            patch.object(fibona, "Agent") as agent,
        ):
            self.assertEqual(fibona.main(), 0)
            mind = agent.call_args.args[0]
            self.assertIs(mind.client, client.return_value.__enter__.return_value)
            self.assertEqual(mind.model, "test-model")
            self.assertEqual(agent.call_args.kwargs["cwd"], ".")
            run = agent.return_value.run
            self.assertEqual(run.call_args.args, ("Summarize these files",))
            self.assertIs(run.call_args.kwargs["env"].read_input, input)
            run.side_effect = KeyboardInterrupt
            self.assertEqual(fibona.main(), 130)

    def test_missing_or_blank_task(self):
        for args in ([], ["   "]):
            with (
                self.subTest(args=args),
                patch("sys.argv", ["fibona", *args]),
                patch.object(fibona, "OpenAI") as client,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                fibona.main()
            self.assertEqual(error.exception.code, 2)
            client.assert_not_called()

    def test_debug_flag_in_both_entrypoints(self):
        import terminal

        for enabled in (False, True):
            flags = ["--debug"] if enabled else []
            with (
                self.subTest(enabled=enabled),
                patch("sys.argv", ["fibona", *flags, "task"]),
                patch.object(fibona, "OpenAI"),
                patch.object(fibona, "Agent") as agent,
            ):
                fibona.main()
                self.assertEqual(agent.return_value.run.call_args.kwargs["debug"], enabled)
            with (
                self.subTest(terminal=True, enabled=enabled),
                patch("sys.argv", ["terminal", *flags]),
                patch.object(terminal, "Terminal") as app,
            ):
                app.return_value.thread = None
                terminal.main()
                app.assert_called_once_with(debug=enabled)
