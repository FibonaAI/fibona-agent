import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai.types.responses import Response, ResponseFunctionToolCall

from core.agent import INSTRUCTIONS, Agent, Mind
from core.env import IPythonEnv
from demos.agent_in_repl import runtime
from demos.agent_in_repl.runtime import REPLAgent

ROOT = Path(__file__).resolve().parents[2]


def responses_for(*cells):
    """Script next-cell decisions while keeping the real core model-call protocol."""
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


class AgentInREPLTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "tmp/tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.cwd = Path(temporary.name)
        environment = patch.dict(os.environ, {"IPYTHONDIR": str(self.cwd / "ipython")})
        environment.start()
        self.addCleanup(environment.stop)
        self.create = Mock()
        client = SimpleNamespace(api_key="test-key", responses=SimpleNamespace(create=self.create))
        self.mind = Mind(client, "test-model", INSTRUCTIONS)
        self.env = IPythonEnv()
        self.addCleanup(self.env.shell.history_manager.end_session)
        self.cells, self.results = [], []

    def run_cells(self, *cells, agent_class=REPLAgent, debug=False):
        self.create.side_effect = responses_for(*cells)
        agent_class(self.mind, cwd=self.cwd).run(
            "test task",
            env=self.env,
            on_cell=lambda number, summary, code: self.cells.append(code),
            on_result=lambda number, result: self.results.append(result),
            debug=debug,
        )

    def test_core_sends_only_explicit_input(self):
        self.run_cells("answer = 6 * 7\ncall_me(input=str(answer))", "quit()", agent_class=Agent)
        initial = self.create.call_args_list[0].kwargs
        self.assertEqual(initial["instructions"], INSTRUCTIONS)
        self.assertEqual(initial["input"], [{"role": "user", "content": Agent.START + "\n\nUser task:\ntest task"}])
        self.assertEqual(self.create.call_args_list[2].kwargs["input"], [{"role": "user", "content": "42"}])
        self.assertNotIn("build_context", self.env.shell.user_ns)
        self.assertNotIn("var_docs", self.env.shell.user_ns)
        self.assertNotIn("variables", self.env.shell.user_ns)
        self.assertNotIn("task", self.env.shell.user_ns)
        self.assertNotIn("source_code", self.env.shell.user_ns)
        self.assertTrue(all(result.error is None for result in self.results))

    def test_omitted_context_does_not_read_demo_helpers(self):
        self.run_cells(
            "instructions = 'Do not inject this'\n"
            "def build_context():\n    raise AssertionError('must not be called')\n"
            "call_me(input='Continue without context')",
            "quit()",
        )
        request = self.create.call_args_list[2].kwargs
        self.assertEqual(request["input"], [{"role": "user", "content": "Continue without context"}])
        self.assertEqual(request["instructions"], INSTRUCTIONS)
        self.assertTrue(all(result.error is None for result in self.results))

    def test_bootstrap_is_the_first_observed_and_recorded_cell(self):
        self.env.bind(existing_value=42)
        initialized = []
        self.create.side_effect = responses_for("assert existing_value == 42\nquit()")
        REPLAgent(self.mind, cwd=self.cwd).run(
            "test task",
            env=self.env,
            on_cell=lambda number, summary, code: initialized.append(
                callable(self.env.shell.user_ns.get("build_context"))
            ),
        )
        self.assertEqual(initialized, [False, True])
        namespace = self.env.shell.user_ns
        self.assertEqual(namespace["task"], "test task")
        source = (ROOT / "demos/agent_in_repl/bootstrap.py").read_text()
        self.assertNotIn("repl_source", namespace)
        self.assertEqual(namespace["source_code"]["core/agent.py"], (ROOT / "core/agent.py").read_text())
        self.assertEqual(namespace["source_code"]["core/env.py"], (ROOT / "core/env.py").read_text())
        self.assertEqual(set(namespace["source_code"]), {"core/agent.py", "core/env.py"})
        self.assertEqual(namespace["variables"]()["existing_value"], "int")
        self.assertEqual(namespace["history"][0]["code"], f"task = 'test task'\n{source}")
        self.assertEqual(len(namespace["history"]), 1)
        self.assertIs(self.mind.instructions, INSTRUCTIONS)
        self.assertNotIn("first_cell", vars(self.env))

    def test_bootstrap_source_is_selected_in_recent_context(self):
        self.run_cells("call_me(input=build_context())", "quit()")
        namespace = self.env.shell.user_ns
        bootstrap = namespace["history"][0]["code"]
        initial = self.create.call_args_list[0].kwargs["input"][1]["content"]
        self.assertIn("Recent task cells:\nnone yet", initial)
        selected = self.create.call_args_list[2].kwargs["input"][1]["content"]
        self.assertIn("### cell 0 · Start the task.", selected)
        self.assertIn(bootstrap.strip(), selected)
        namespace["RECENT"] = 1
        selected = namespace["build_context"]()[1]["content"]
        self.assertIn("cell 0: Start the task. [ok]", selected)
        self.assertNotIn(bootstrap.strip(), selected)
        self.assertEqual(namespace["history"][0]["code"], bootstrap)

    def test_bootstrap_supports_top_level_await_and_ipython_magics(self):
        source = (
            "import asyncio\nawait asyncio.sleep(0)\n%precision 4\n"
            "bootstrap_awaited = True\ncall_me(input='Start task')"
        )
        with patch("demos.agent_in_repl.runtime.Path.read_text", return_value=source):
            self.run_cells("assert bootstrap_awaited\nquit()")
        self.assertEqual(self.cells[0], f"task = 'test task'\n{source}")
        self.assertTrue(all(result.error is None for result in self.results))

    def test_agent_can_select_and_redefine_its_context(self):
        self.run_cells(
            "value = 42\nvar_docs['value'] = 'measured answer'\ncall_me(input=[*build_context(), {'role': 'user', 'content': 'Continue'}])",
            "instructions = 'My revised working instructions'\n"
            "def build_context():\n    return [{'role': 'developer', 'content': instructions}, {'role': 'user', 'content': str(value)}]\n"
            "call_me(input=[*build_context(), {'role': 'user', 'content': 'Continue again'}])",
            "quit()",
        )
        default = self.create.call_args_list[2].kwargs["input"][1]["content"]
        self.assertIn("value: int (measured answer)", default)
        self.assertIn("Startup task: test task", default.splitlines())
        self.assertIn("Current goal: test task", default.splitlines())
        revised = self.create.call_args_list[4].kwargs
        self.assertEqual(
            revised["input"],
            [
                {"role": "developer", "content": "My revised working instructions"},
                {"role": "user", "content": "42"},
                {"role": "user", "content": "Continue again"},
            ],
        )
        self.assertEqual(revised["instructions"], INSTRUCTIONS)
        self.assertTrue(all(result.error is None for result in self.results))

    def test_demo_source_information_can_be_edited_without_host_refresh(self):
        self.run_cells(
            "source_code['note'] = 'My source notes'\ncall_me(input='Continue')",
            "assert source_code['note'] == 'My source notes'\nquit()",
        )
        self.assertEqual(self.create.call_args_list[2].kwargs["input"], [{"role": "user", "content": "Continue"}])
        self.assertTrue(all(result.error is None for result in self.results))

    def test_variable_selection_is_session_code_and_can_be_redefined(self):
        self.run_cells(
            "import math\n_private = 1\nvalues = [1, 2, 3]\n"
            "selected = variables()\n"
            "assert selected['values'] == 'list[3]'\n"
            "assert not {'math', '_private', 'task', 'history', 'call_me', 'input', 'source_code'} & selected.keys()\n"
            "def variables():\n    return {'chosen': 'custom description'}\n"
            "call_me(input=build_context())",
            "call_me(input=build_context())",
            "quit()",
        )
        for index in (2, 4):
            self.assertIn("chosen: custom description", self.create.call_args_list[index].kwargs["input"][1]["content"])
        self.assertTrue(all(result.error is None for result in self.results))

    def test_broken_variable_selection_does_not_break_recovery(self):
        self.run_cells(
            "value = 42\ndef variables():\n    raise ValueError('selection broken')\ncall_me(input=build_context())",
            "def variables():\n    return {'value': 'int'}\ncall_me(input=build_context())",
            "assert value == 42\nquit()",
        )
        recovery = self.create.call_args_list[2].kwargs["input"][0]["content"]
        self.assertIn("selection broken", recovery)
        self.assertTrue(recovery.startswith(Agent.REPAIR))
        self.assertIn("value: int", self.create.call_args_list[4].kwargs["input"][1]["content"])
        self.assertTrue(all(result.error is None for result in self.results[2:]))

    def test_context_window_selects_host_records_without_editing_them(self):
        self.run_cells("quit()")
        namespace = self.env.shell.user_ns
        records = [
            {"code": f"value = {number}", "summary": f"Step {number}", "output": "", "error": None}
            for number in range(12)
        ]
        namespace["history"] = records
        namespace["RECENT"] = 2
        context = namespace["build_context"]()[1]["content"]
        self.assertIn("cell 9: Step 9 [ok]", context)
        self.assertEqual(context.count("### cell "), 2)
        self.assertIn("value = 10\n\n### cell 11", context)
        self.assertEqual(records[0]["code"], "value = 0")
        namespace["RECENT"] = 0
        context = namespace["build_context"]()[1]["content"]
        self.assertNotIn("### cell ", context)
        self.assertIn("cell 11: Step 11 [ok]", context)

    def test_syntax_error_recovers_in_the_same_environment(self):
        self.run_cells("if :", "repaired = True\nquit()")
        self.assertIn("SyntaxError", self.results[1].error)
        recovery = self.create.call_args_list[2].kwargs
        self.assertIn("SyntaxError", recovery["input"][0]["content"])
        self.assertTrue(recovery["input"][-1]["content"].startswith(Agent.REPAIR))
        self.assertEqual(len(recovery["input"]), 1)
        failed = json.loads(recovery["input"][0]["content"].split("\n\n", 1)[1])
        self.assertEqual(failed["task"], "test task")
        self.assertEqual(failed["code"], "if :")
        self.assertIn("SyntaxError", failed["error"])
        self.assertTrue(self.env.shell.user_ns["repaired"])
        self.assertTrue(all(result.error is None for result in self.results[2:]))

    def test_broken_context_helper_does_not_break_core_recovery(self):
        self.run_cells(
            "checkpoint = 'keep me'\n"
            "def build_context():\n    raise ValueError('context broken')\n"
            "call_me(input=[*build_context(), {'role': 'user', 'content': 'Continue'}])",
            "def build_context():\n    return [{'role': 'user', 'content': checkpoint}]\ncall_me(input=[*build_context(), {'role': 'user', 'content': 'Resume'}])",
            "assert checkpoint == 'keep me'\nquit()",
        )
        recovery = self.create.call_args_list[2].kwargs
        self.assertIn("context broken", recovery["input"][0]["content"])
        self.assertTrue(recovery["input"][-1]["content"].startswith(Agent.REPAIR))
        self.assertEqual(self.create.call_args_list[4].kwargs["input"][0]["content"], "keep me")
        self.assertTrue(all(result.error is None for result in self.results[2:]))

    def test_history_edits_do_not_change_original_recovery_records(self):
        self.run_cells(
            "history[0]['code'] = 'forged code'\nhistory.clear()\nraise ValueError('real failure')",
            "assert history[0]['code'].startswith('task = ')\nquit()",
        )
        self.assertIn("real failure", self.create.call_args_list[2].kwargs["input"][0]["content"])
        self.assertTrue(self.results[-1].quit)

    def test_full_output_is_retained_and_demo_only_clips_its_view(self):
        output = "A" * 700 + "MIDDLE_EVIDENCE" + "Z" * 700
        self.run_cells(
            f"print({output!r})\ncall_me(input='Inspect output next')",
            "full_output = history[1]['output']\n"
            "assert 'MIDDLE_EVIDENCE' in full_output\n"
            "call_me(input=[*build_context(), {'role': 'user', 'content': full_output}])",
            "quit()",
        )
        request = self.create.call_args_list[4].kwargs
        excerpt = request["input"][1]["content"].split("output:\n")[-1]
        self.assertNotIn("MIDDLE_EVIDENCE", excerpt)
        self.assertIn("history[1]['output']", excerpt)
        self.assertEqual(request["input"][-1]["content"], output + "\n")

    def test_recovery_requests_do_not_reset_failure_limit(self):
        with self.assertRaisesRegex(RuntimeError, "3 consecutive cells failed"):
            self.run_cells("if :", "if :", "if :")
        self.assertEqual(self.create.call_count, 6)
        self.assertEqual(sum("SyntaxError" in (result.error or "") for result in self.results), 3)

    def test_interrupt_does_not_request_recovery(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_cells("raise KeyboardInterrupt()")
        self.assertEqual(self.create.call_count, 2)

    def test_debug_logging_records_explicit_input(self):
        self.run_cells(
            "context_calls = 0\n"
            "def build_context():\n    global context_calls\n    context_calls += 1\n    return [{'role': 'user', 'content': 'Selected context'}]\n"
            "call_me(input=build_context())",
            "assert context_calls == 1\nquit()",
            debug=True,
        )
        records = [json.loads(line) for line in (self.cwd / "agent.log").read_text().splitlines()]
        selected = [
            r
            for r in records
            if r.get("request", {}).get("input", []) == [{"role": "user", "content": "Selected context"}]
        ]
        self.assertEqual(len(selected), 1)
        self.assertEqual([r["code"] for r in records if r["event"] == "cell"], self.cells)
        self.assertTrue(all("context" not in r.get("request", {}) for r in records))
        self.assertTrue(all(result.error is None for result in self.results))

    def test_invalid_cell_submission_can_be_corrected_before_execution(self):
        invalid = ResponseFunctionToolCall(
            type="function_call", name="set_next_cell", call_id="invalid", arguments="{broken json"
        )
        self.create.side_effect = [Response.model_construct(output=[invalid]), *responses_for("quit()")]
        Agent(self.mind, cwd=self.cwd).run("test task", env=self.env)
        failed = self.create.call_args_list[1].kwargs["input"][-1]
        self.assertEqual(failed["call_id"], "invalid")
        self.assertFalse(json.loads(failed["output"])["scheduled"])
        corrected = self.create.call_args_list[2].kwargs["input"][-1]
        self.assertTrue(json.loads(corrected["output"])["scheduled"])
        self.assertEqual(self.create.call_count, 3)

    def test_bootstrap_syntax_error_uses_core_recovery(self):
        with patch("demos.agent_in_repl.runtime.Path.read_text", return_value="if :"):
            self.run_cells("bootstrap_repaired = True\nquit()")
        self.assertIn("SyntaxError", self.results[0].error)
        recovery = self.create.call_args_list[0].kwargs["input"][0]["content"]
        self.assertTrue(recovery.startswith(Agent.REPAIR))
        failed = json.loads(recovery.split("\n\n", 1)[1])
        self.assertEqual(failed["code"], self.cells[0])
        self.assertEqual(failed["task"], "test task")
        self.assertTrue(self.env.shell.user_ns["bootstrap_repaired"])
        self.assertTrue(all(result.error is None for result in self.results[1:]))

    def test_bootstrap_recovery_preserves_partial_initialization(self):
        source = "checkpoint = 'keep me'\nraise ValueError('bootstrap interrupted')"
        with patch("demos.agent_in_repl.runtime.Path.read_text", return_value=source):
            self.run_cells(
                "assert checkpoint == 'keep me'\ncheckpoint = 'repaired'\ncall_me(input='Continue')",
                "assert checkpoint == 'repaired'\nquit()",
            )
        self.assertIn("bootstrap interrupted", self.results[0].error)
        self.assertEqual(self.env.shell.user_ns["checkpoint"], "repaired")
        self.assertTrue(all(result.error is None for result in self.results[1:]))

    def test_bootstrap_runs_in_the_agents_working_directory(self):
        initial_directory = Path.cwd()
        self.create.side_effect = responses_for("quit()")
        source = "import os\ninitial_cwd = os.getcwd()\ncall_me(input='Start task')"
        with patch("demos.agent_in_repl.runtime.Path.read_text", return_value=source):
            REPLAgent(self.mind, cwd=self.cwd).run("test task", env=self.env)
        self.assertEqual(self.env.shell.user_ns["initial_cwd"], str(self.cwd))
        self.assertEqual(Path.cwd(), initial_directory)

    def test_demo_entrypoint_runs_the_wrapper(self):
        self.create.side_effect = responses_for("assert callable(build_context)\nquit()")
        with patch.object(runtime, "OpenAI") as client, patch("sys.argv", ["demos.agent_in_repl.runtime", "test task"]):
            client.return_value.__enter__.return_value = self.mind.client
            self.assertEqual(runtime.main(), 0)
        initial = self.create.call_args_list[0].kwargs["input"]
        self.assertEqual([item["role"] for item in initial], ["developer", "user", "user"])
        self.assertIn("build_context()", initial[0]["content"])
        self.assertIn("Startup task: test task", initial[1]["content"].splitlines())
        self.assertIn("Current goal: test task", initial[1]["content"].splitlines())
        self.assertEqual(initial[2]["content"], Agent.START)

    def test_wrapper_can_create_its_own_environment(self):
        self.create.side_effect = responses_for("assert callable(build_context)\nquit()")
        REPLAgent(self.mind, cwd=self.cwd).run("test task")
        self.assertEqual(self.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
