import json
import runpy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class ReactLoopTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.outer = Mock()
        with (
            patch("bootstraps.terminal.context.initialize_context", return_value=lambda: []),
            patch("signal.setitimer") as timer,
        ):
            namespace = runpy.run_path(
                "eval/plugins/bootstrap.py",
                init_globals={
                    "argv": ["test task"],
                    "call_me": self.outer,
                    "call_llm_api": self.api,
                    "model_reasoning_effort": "max",
                },
            )
        timer.assert_not_called()
        self.loop = namespace["run_react"]

    def response(self, *output, status="completed"):
        return SimpleNamespace(status=status, output=list(output))

    def call(self, arguments='{"value": 2}'):
        return SimpleNamespace(type="function_call", name="double", call_id="call-1", arguments=arguments)

    def test_no_default_tools_and_configured_reasoning(self):
        response = self.response()
        self.api.return_value = response
        self.assertIs(self.loop([]), response)
        request = self.api.call_args.kwargs
        self.assertNotIn("tools", request)
        self.assertEqual(request["reasoning"], {"effort": "max"})
        self.assertEqual(request["include"], ["reasoning.encrypted_content"])
        self.assertFalse(request["store"])

    def test_tool_observations_and_reasoning_are_replayed(self):
        reasoning = SimpleNamespace(type="reasoning", encrypted_content="opaque")
        call = self.call()
        final = self.response()
        self.api.side_effect = [self.response(reasoning, call), final]
        messages = [{"role": "user", "content": "Double two"}]
        tools = [{"type": "function", "name": "double"}]
        self.assertIs(self.loop(messages, tools=tools, handlers={"double": lambda value: value * 2}), final)
        self.assertIs(messages[1], reasoning)
        self.assertEqual(messages[-1], {"type": "function_call_output", "call_id": "call-1", "output": "4"})
        self.assertEqual(self.api.call_args_list[1].kwargs["input"], messages)
        self.assertEqual(len(self.api.call_args_list[0].kwargs["input"]), 1)
        self.outer.assert_called_once()

    def test_tool_error_is_an_observation(self):
        self.api.side_effect = [self.response(self.call("invalid JSON")), self.response()]
        messages = []
        handler = Mock()
        self.loop(messages, handlers={"double": handler})
        handler.assert_not_called()
        self.assertIn("error", json.loads(messages[-1]["output"]))

    def test_turn_limit_keeps_resumable_history(self):
        self.api.return_value = self.response(self.call())
        messages = []
        self.assertIsNone(self.loop(messages, handlers={"double": lambda value: value}, max_turns=1))
        self.assertEqual(messages[-1]["type"], "function_call_output")
        final = self.response()
        self.api.return_value = final
        self.assertIs(self.loop(messages), final)

    def test_incomplete_response_does_not_execute_tools(self):
        self.api.return_value = self.response(self.call(), status="incomplete")
        handler = Mock()
        messages = []
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.loop(messages, handlers={"double": handler})
        handler.assert_not_called()
        self.assertEqual(messages, [])
