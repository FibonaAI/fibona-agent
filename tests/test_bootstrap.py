import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai.types.responses import Response, ResponseFunctionToolCall

from core.agent import INSTRUCTIONS, Agent, History, Mind
from core.env import CellResult, IPythonEnv

ROOT = Path(__file__).resolve().parents[1]


def responses_for(*cells):
    """Each wake schedules one cell, then receives its tool acknowledgement."""
    responses = []
    for number, code in enumerate(cells):
        tool = ResponseFunctionToolCall(
            type="function_call",
            name="set_next_cell",
            call_id=f"call_{number}",
            arguments=json.dumps({"code": code, "summary": f"Step {number}"}),
        )
        responses.extend(
            [
                Response.model_construct(status="completed", output=[tool]),
                Response.model_construct(status="completed", output=[]),
            ]
        )
    return responses


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.cwd = Path(temporary.name)
        environment = patch.dict(os.environ, {"IPYTHONDIR": str(self.cwd / "ipython")})
        environment.start()
        self.addCleanup(environment.stop)
        self.create = Mock()
        client = SimpleNamespace(api_key="test-key", responses=SimpleNamespace(create=self.create))
        self.agent = Agent(Mind(client, "test-model", INSTRUCTIONS), cwd=self.cwd)
        self.env = IPythonEnv()
        self.addCleanup(self.env.shell.history_manager.end_session)
        self.cells, self.results = [], []

    def run_cells(self, *cells, debug=False):
        self.create.side_effect = responses_for(*cells)
        self.agent.run(
            "test task",
            env=self.env,
            on_cell=lambda number, summary, code: self.cells.append(code),
            on_result=lambda number, result: self.results.append(result),
            debug=debug,
        )

    def test_bootstrap_runs_once_and_editable_policy_persists(self):
        self.run_cells(
            "instructions = 'Use the revised policy.'\n"
            "checkpoint = 'Remember 42'\n"
            "var_docs['value'] = 'an observed result'\n"
            "value = 42\n"
            "def build_context():\n"
            "    return checkpoint + ':' + str(value)\n"
            "response = call_me(instructions=instructions, context=build_context(), input='Continue')",
            "assert value == 42\nassert var_docs['value'] == 'an observed result'\nquit()",
        )
        self.assertEqual(self.cells[0], (ROOT / "core/bootstrap.py").read_text())
        self.assertEqual(len(self.cells), 3)
        request = self.create.call_args_list[2].kwargs
        self.assertEqual(request["instructions"], INSTRUCTIONS)
        self.assertEqual(
            request["input"],
            [
                {"role": "developer", "content": "Use the revised policy."},
                {"role": "user", "content": "Remember 42:42"},
                {"role": "user", "content": "Continue"},
            ],
        )
        self.assertEqual(self.env.shell.user_ns["source_code"]["core/bootstrap.py"], self.cells[0])
        feedback = self.create.call_args_list[1].kwargs["input"][-1]
        self.assertEqual(feedback["type"], "function_call_output")
        self.assertEqual(json.loads(feedback["output"]), {"scheduled": True})

    def test_default_context_reflects_variables_and_history(self):
        self.run_cells(
            "value = 42\nvar_docs['value'] = 'measured answer'\ncall_me(instructions=instructions, context=build_context(), input='Continue')",
            "assert history[1]['code'].startswith('value =')\ncall_me(instructions=instructions, context=build_context(), input='Continue again')",
            "quit()",
        )
        first_context = self.create.call_args_list[2].kwargs["input"][1]["content"]
        self.assertIn("value: int (measured answer)", first_context)
        next_context = self.create.call_args_list[4].kwargs["input"][1]["content"]
        self.assertIn(self.cells[1], next_context)

    def test_context_history_window_and_format(self):
        self.run_cells("quit()")
        initial_context = self.create.call_args_list[0].kwargs["input"][1]["content"]
        self.assertTrue(initial_context.endswith("none yet"))
        namespace = self.env.shell.user_ns
        self.assertNotIn("notes", namespace)
        self.assertEqual(namespace["RECENT"], 10)
        namespace["history"] = [
            {"code": f"value = {number}", "summary": f"Step {number}", "output": "", "error": None}
            for number in range(11)
        ]
        context = namespace["build_context"]()
        self.assertIn("cell 0: Step 0 [ok]", context)
        self.assertNotIn("### cell 0", context)
        self.assertEqual(context.count("### cell "), 10)
        namespace["RECENT"] = 2
        context = namespace["build_context"]()
        self.assertIn("- Older cells (summary and result; read `history[i]` in a cell for the rest):", context)
        self.assertIn("cell 8: Step 8 [ok]", context)
        self.assertEqual(context.count("### cell "), 2)
        self.assertIn("value = 9\n\n### cell 10", context)
        self.assertNotIn("Working notes", context)

    def test_injected_variable_selection_is_editable_and_persists(self):
        self.env.shell.user_ns["preexisting"] = [42]
        self.run_cells(
            "import math\n_private = 1\nvalues = [1, 2, 3]\n"
            "selected = variables()\n"
            "assert selected['values'] == 'list[3]'\n"
            "assert selected['preexisting'] == 'list[1]'\n"
            "assert not {'math', '_private', 'task', 'history', 'call_me', 'input', 'source_code'} & selected.keys()\n"
            "def variables():\n    return {'chosen': 'custom description'}\n"
            "call_me(context=build_context(), input='Continue')",
            "call_me(context=build_context(), input='Continue again')",
            "quit()",
        )
        self.assertTrue(all(result.error is None for result in self.results))
        for index in (2, 4):
            context = self.create.call_args_list[index].kwargs["input"][0]["content"]
            self.assertIn("chosen: custom description", context)
        self.assertIn("variables", self.env.bound)
        self.assertNotIn("_start_message", self.env.shell.user_ns)

    def test_broken_variable_selection_does_not_break_recovery(self):
        self.run_cells(
            "values = [1, 2, 3]\n_original_variables = variables\n"
            "def variables():\n    raise ValueError('variable selection broken')\n"
            "call_me(context=build_context(), input='Continue')",
            "variables = _original_variables\ncall_me(context=build_context(), input='Resume')",
            "assert variables()['values'] == 'list[3]'\nquit()",
        )
        self.assertIn("variable selection broken", self.results[1].error)
        recovery = self.create.call_args_list[2].kwargs
        context = json.loads(recovery["input"][0]["content"])
        self.assertEqual(context["variables"]["values"], "list[3]")
        resumed = self.create.call_args_list[4].kwargs
        self.assertIn("values: list[3]", resumed["input"][0]["content"])
        self.assertTrue(all(result.error is None for result in self.results[2:]))

    def test_omitted_instructions_and_context_are_not_injected(self):
        self.run_cells(
            "instructions = 'Do not inject this'\n"
            "def build_context():\n    raise AssertionError('must not be called')\n"
            "call_me('Continue without defaults')",
            "quit()",
        )
        request = self.create.call_args_list[2].kwargs
        self.assertEqual(request["instructions"], INSTRUCTIONS)
        self.assertEqual(request["input"], [{"role": "user", "content": "Continue without defaults"}])

    def test_caller_selects_instructions_and_structured_context(self):
        self.run_cells(
            "selected = [{'role': 'assistant', 'content': 'Earlier conclusion'}]\n"
            "call_me(instructions='Selected rules', context=selected, "
            "input=[{'role': 'user', 'content': 'Next step'}], "
            "extra_body={'instructions': 'Must not replace host instructions'})",
            "quit()",
            debug=True,
        )
        request = self.create.call_args_list[2].kwargs
        self.assertEqual(request["instructions"], INSTRUCTIONS)
        self.assertNotIn("instructions", request["extra_body"])
        self.assertEqual(
            request["input"],
            [
                {"role": "developer", "content": "Selected rules"},
                {"role": "assistant", "content": "Earlier conclusion"},
                {"role": "user", "content": "Next step"},
            ],
        )
        records = [json.loads(line) for line in (self.cwd / "agent.log").read_text().splitlines()]
        logged = next(r["request"] for r in records if r.get("request", {}).get("instructions") == "Selected rules")
        self.assertEqual(logged["context"], [{"role": "assistant", "content": "Earlier conclusion"}])
        self.assertEqual(logged["host_instructions"], INSTRUCTIONS)

    def test_syntax_error_reaches_host_recovery(self):
        self.run_cells("if :", "repaired = True\nquit()")
        self.assertIn("SyntaxError", self.results[1].error)
        self.assertEqual(self.cells[2], "response = _recover()\nprint(response.output_text)")
        request = self.create.call_args_list[2].kwargs
        self.assertEqual(len(request["input"]), 2)
        context = json.loads(request["input"][0]["content"])
        self.assertEqual(context["recent_cells"][-1]["code"], "if :")
        self.assertIn("SyntaxError", context["recent_cells"][-1]["error"])
        self.assertTrue(self.env.shell.user_ns["repaired"])

    def test_broken_context_and_policy_do_not_break_recovery(self):
        self.run_cells(
            "instructions = None\ncheckpoint = 'preserve me'\n"
            "def build_context():\n    raise ValueError('context broken')\ncall_me(instructions=instructions, context=build_context(), input='Continue')",
            "instructions = 'Repaired policy'\ndef build_context():\n    return checkpoint\ncall_me(instructions=instructions, context=build_context(), input='Resume')",
            "assert checkpoint == 'preserve me'\nquit()",
            debug=True,
        )
        recovery = self.create.call_args_list[2].kwargs
        self.assertEqual(len(recovery["input"]), 2)
        self.assertIn("context broken", recovery["input"][0]["content"])
        resumed = self.create.call_args_list[4].kwargs
        self.assertEqual(resumed["input"][0]["content"], "Repaired policy")
        self.assertEqual(resumed["input"][1]["content"], "preserve me")
        records = [json.loads(line) for line in (self.cwd / "agent.log").read_text().splitlines()]
        self.assertIn("context broken", self.results[1].error)
        self.assertEqual(sum(r["event"] == "call_me" for r in records), 2)
        self.assertTrue(any(r["event"] == "recover" for r in records))
        resumed_log = next(r for r in records if r.get("request", {}).get("instructions") == "Repaired policy")
        self.assertEqual(resumed_log["request"]["context"], "preserve me")

    def test_recovery_requests_do_not_reset_failure_limit(self):
        with self.assertRaisesRegex(RuntimeError, "3 consecutive cells failed"):
            self.run_cells("if :", "if :", "if :")
        self.assertEqual(self.create.call_count, 6)
        self.assertEqual(sum("SyntaxError" in (r.error or "") for r in self.results), 3)

    def test_failed_cell_discards_successor_and_keeps_side_effects(self):
        self.run_cells(
            "value = 42\ncall_me(instructions=instructions, context=build_context(), input='Schedule')\nraise ValueError('broken after scheduling')",
            "raise AssertionError('stale successor ran')",
            "assert value == 42\nquit()",
        )
        self.assertNotIn("raise AssertionError('stale successor ran')", self.cells)
        self.assertTrue(self.results[-1].quit)

    def test_interrupt_does_not_request_recovery(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_cells("raise KeyboardInterrupt()")
        self.assertEqual(self.create.call_count, 2)

    def test_full_output_can_be_retrieved_after_context_clipping(self):
        self.run_cells(
            "print('A' * 700 + 'MIDDLE_EVIDENCE' + 'Z' * 700)\ncall_me(instructions=instructions, context=build_context(), input='Continue')",
            "full_output = history[1]['output']\n"
            "assert 'MIDDLE_EVIDENCE' in full_output\n"
            "call_me(instructions=instructions, context=build_context(), input='Read the complete output: ' + full_output)",
            "quit()",
        )
        request = self.create.call_args_list[4].kwargs
        context = request["input"][1]["content"]
        excerpt = context.split("output:\n")[-1]
        self.assertNotIn("MIDDLE_EVIDENCE", excerpt)
        self.assertIn("full output: history[1]['output']", excerpt)
        self.assertIn("A" * 700 + "MIDDLE_EVIDENCE" + "Z" * 700, request["input"][-1]["content"])

    def test_history_copy_edits_do_not_change_recovery_records(self):
        self.run_cells(
            "history[0]['code'] = 'forged code'\n"
            "history[0]['summary'] = 'forged summary'\n"
            "history.clear()\ncheckpoint = 'my own memory'\nraise ValueError('real failure')",
            "assert history[0]['code'] == source_code['core/bootstrap.py']\n"
            "assert history[0]['summary'] != 'forged summary'\n"
            "assert checkpoint == 'my own memory'\nquit()",
        )
        context = json.loads(self.create.call_args_list[2].kwargs["input"][0]["content"])
        self.assertEqual(context["recent_cells"][0]["code"], self.cells[0])
        self.assertIn("real failure", context["recent_cells"][-1]["error"])

    def test_recovery_clipping_and_snapshot_edits_leave_originals_intact(self):
        history = History("task")
        output = "A" * 700 + "MIDDLE_EVIDENCE" + "Z" * 700
        history.record("print(data)", "Inspect data", CellResult(output, error="ValueError: failed"))
        copy = history.snapshot()
        copy[0].update(code="changed", summary="changed", output="changed", error=None)
        context = json.loads(history.recovery_context({}))
        self.assertNotIn("MIDDLE_EVIDENCE", context["recent_cells"][0]["output"])
        self.assertEqual(
            history.snapshot(),
            [{"code": "print(data)", "summary": "Inspect data", "output": output, "error": "ValueError: failed"}],
        )

    def test_quitting_cell_is_recorded(self):
        history = History("test task")
        with patch("core.agent.History", return_value=history):
            self.run_cells("print('finished')\nquit()")
        self.assertEqual(history.cells, 2)
        self.assertEqual(history.snapshot()[-1]["output"], "finished\n")


if __name__ == "__main__":
    unittest.main()
