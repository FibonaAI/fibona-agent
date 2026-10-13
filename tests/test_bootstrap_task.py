import unittest
from unittest.mock import Mock, patch

import fibona


class BootstrapTaskTest(unittest.TestCase):
    def test_minimal_task_from_cli_or_prompt(self):
        for remaining_args in (
            ["Build a game"],
            ["Build", "a", "game"],
            ["Quotes: 'hello'\nprint('not executed')"],
            [],
            [""],
        ):
            task = " ".join(remaining_args)
            with self.subTest(remaining_args=remaining_args):
                namespace = {}
                read_input = Mock(return_value="Interactive task")
                call_me = Mock()
                env = Mock()
                env.bind.side_effect = namespace.update
                namespace.update(input=read_input, call_me=call_me)

                def run_cell(source, namespace=namespace, **kwargs):
                    exec(source, namespace)

                argv = ["fibona.py", "--boot-mode", "minimal", *remaining_args]
                with (
                    patch("sys.argv", argv),
                    patch.object(fibona, "OpenAI"),
                    patch.object(fibona, "Mind"),
                    patch.object(fibona, "IPythonEnv", return_value=env),
                    patch.object(fibona, "Agent") as agent,
                ):
                    agent.return_value.run.side_effect = run_cell
                    self.assertEqual(fibona.main(), 0)

                env.bind.assert_called_once_with(argv=remaining_args)
                call_me.assert_called_once_with(input=task or "Interactive task")
                if task:
                    read_input.assert_not_called()
                else:
                    read_input.assert_called_once_with("Your task: ")
