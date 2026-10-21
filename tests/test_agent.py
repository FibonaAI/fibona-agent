"""Core scheduling and stateless replay regressions; no credentials or network needed."""

import io
import json
import unittest

import httpx
from openai import OpenAI
from openai.types.responses import Response

from core.agent import INSTRUCTIONS, Agent, Mind, Slot, WakeResponse
from core.env import IPythonEnv


def message(text):
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def reasoning(index=1):
    return {"type": "reasoning", "id": f"rs_{index}", "summary": [], "encrypted_content": f"encrypted-{index}"}


def call(code, call_id="call_1", *, name="set_next_cell", arguments=None):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "status": "completed",
        "arguments": arguments if arguments is not None else json.dumps({"code": code, "summary": "Next step"}),
    }


class Fixture:
    def __init__(self, outputs):
        self.sent, self.raw_responses, self.raw_snapshots = [], [], []

        def handler(request):
            self.sent.append(json.loads(request.content))
            index = len(self.sent) - 1
            if index >= len(outputs):
                raise AssertionError("Unexpected extra model request")
            return httpx.Response(
                200,
                headers={"x-request-id": f"req_{index}"},
                json={
                    "id": f"resp_{index}",
                    "object": "response",
                    "created_at": 0,
                    "model": "test-model",
                    "status": "completed",
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "output": outputs[index],
                    "provider_trace": "preserve-this-extra-field",
                },
            )

        self.client = OpenAI(
            api_key="test-only",
            base_url="https://mock.invalid/v1",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        create = self.client.responses.create

        def capture(**kwargs):
            response = create(**kwargs)
            self.raw_responses.append(response)
            self.raw_snapshots.append(response.model_dump())
            return response

        self.client.responses.create = capture
        self.mind = Mind(self.client, "test-model", INSTRUCTIONS)


class WakeTests(unittest.TestCase):
    def fixture(self, outputs):
        fixture = Fixture(outputs)
        self.addCleanup(fixture.client.close)
        return fixture

    def test_success_preserves_model_output_and_returns_after_one_request(self):
        fixture = self.fixture([[reasoning(), message("Starting."), call("print(6)")]])
        slot = Slot()
        result = fixture.mind.wake("go", submit=slot.submit)

        self.assertEqual(len(fixture.sent), 1)
        self.assertEqual(slot.code, "print(6)")
        self.assertTrue(result.scheduled)
        self.assertIsInstance(result, Response)
        self.assertEqual(result.output_text, "Starting.")
        self.assertEqual([item.type for item in result.output], ["reasoning", "message", "function_call"])
        self.assertEqual(result.replay_items[-1]["call_id"], "call_1")
        self.assertEqual(json.loads(result.replay_items[-1]["output"]), {"scheduled": True})

    def test_tool_only_response_is_not_empty_or_replaced_with_synthetic_text(self):
        fixture = self.fixture([[call("print(6)")]])
        result = fixture.mind.wake("go", submit=Slot().submit)

        self.assertTrue(result.scheduled)
        self.assertEqual([item.type for item in result.output], ["function_call"])
        self.assertEqual(result.output_text, "")
        self.assertEqual(len(fixture.sent), 1)

    def test_raw_response_metadata_and_logging_are_preserved(self):
        fixture = self.fixture([[reasoning(), call("print(6)")]])
        logs = []
        result = fixture.mind.wake("go", submit=Slot().submit, log=lambda **entry: logs.append(entry))

        self.assertEqual(fixture.raw_responses[0].model_dump(), fixture.raw_snapshots[0])
        self.assertEqual(result.id, "resp_0")
        self.assertEqual(result._request_id, "req_0")
        self.assertEqual(result.provider_trace, "preserve-this-extra-field")
        logged = json.loads(logs[0]["response"].model_dump_json())
        self.assertEqual(logged["output"][-1]["name"], "set_next_cell")
        self.assertTrue(logged["scheduled"])
        self.assertEqual(logged["replay_items"][0]["encrypted_content"], "encrypted-1")
        self.assertEqual(logged["replay_items"][-1]["type"], "function_call_output")

    def test_replay_pairs_host_results_without_duplicating_original_input(self):
        fixture = self.fixture([[reasoning(), call("print(6)")], [message("Follow-up.")]])
        original = [{"role": "user", "content": "go"}]
        result = fixture.mind.wake(original, submit=Slot().submit)
        followup = fixture.mind.wake(
            [*original, *result.replay_items, {"role": "user", "content": "continue"}],
            submit=Slot().submit,
        )

        self.assertEqual(original, [{"role": "user", "content": "go"}])
        sent = fixture.sent[1]["input"]
        self.assertEqual(
            [item.get("type", item.get("role")) for item in sent],
            ["user", "reasoning", "function_call", "function_call_output", "user"],
        )
        self.assertEqual(sent[1]["encrypted_content"], "encrypted-1")
        self.assertEqual(sent[2]["call_id"], sent[3]["call_id"])
        self.assertFalse(followup.scheduled)
        self.assertEqual(followup.output_text, "Follow-up.")

    def test_rejected_submissions_retry_and_remain_in_replay_history(self):
        for invalid in (call("   "), call("", arguments="{")):
            with self.subTest(arguments=invalid["arguments"]):
                fixture = self.fixture([[reasoning(), invalid], [reasoning(2), call("print(6)", "call_2")]])
                slot = Slot()
                result = fixture.mind.wake("go", submit=slot.submit)

                self.assertEqual(len(fixture.sent), 2)
                self.assertEqual(slot.code, "print(6)")
                self.assertTrue(result.scheduled)
                failed = fixture.sent[1]["input"][-1]
                self.assertEqual(failed["call_id"], "call_1")
                self.assertFalse(json.loads(failed["output"])["scheduled"])
                self.assertIn("error", json.loads(failed["output"]))
                self.assertEqual(
                    [item["type"] for item in result.replay_items],
                    ["reasoning", "function_call", "function_call_output"] * 2,
                )
                results = [item for item in result.replay_items if item["type"] == "function_call_output"]
                self.assertEqual([json.loads(item["output"])["scheduled"] for item in results], [False, True])

    def test_closed_slot_reports_failure_before_returning_plain_reply(self):
        fixture = self.fixture([[call("print(6)")], [message("This cell has finished.")]])
        slot = Slot()
        slot.close()
        result = fixture.mind.wake("go", submit=slot.submit)

        self.assertFalse(result.scheduled)
        self.assertIsNone(slot.code)
        self.assertEqual(len(fixture.sent), 2)
        self.assertIn("finished", json.loads(result.replay_items[1]["output"])["error"])
        self.assertEqual(result.replay_items[-1]["type"], "message")

    def test_caller_tool_is_returned_without_a_fabricated_result(self):
        fixture = self.fixture([[reasoning(), call("unused", name="my_tool")]])
        result = fixture.mind.wake(
            "go",
            submit=Slot().submit,
            tools=[{"type": "function", "name": "my_tool", "parameters": {"type": "object", "properties": {}}}],
        )

        self.assertFalse(result.scheduled)
        self.assertEqual(result.output[-1].name, "my_tool")
        self.assertEqual([item["type"] for item in result.replay_items], ["reasoning", "function_call"])
        self.assertEqual(len(fixture.sent), 1)

    def test_plain_reply_does_not_report_an_earlier_calls_submission(self):
        fixture = self.fixture([[message("Just a reply.")]])
        slot = Slot()
        slot.submit("print('earlier')", "Earlier call")
        result = fixture.mind.wake("go", submit=slot.submit)

        self.assertIsInstance(result, WakeResponse)
        self.assertFalse(result.scheduled)
        self.assertEqual(result.output_text, "Just a reply.")
        self.assertEqual(slot.code, "print('earlier')")

    def test_forced_host_tool_does_not_trigger_an_acknowledgement_request(self):
        fixture = self.fixture([[call("print(6)")]])
        result = fixture.mind.wake(
            "go", submit=Slot().submit, tool_choice={"type": "function", "name": "set_next_cell"}
        )
        self.assertTrue(result.scheduled)
        self.assertEqual(len(fixture.sent), 1)

    def run_agent(self, fixture, code):
        output = io.StringIO()
        env = IPythonEnv(stdout=output)
        results = []

        def on_cell(index, summary, cell):
            self.assertLess(index, 4, "Unexpected extra cell or repair loop")

        Agent(fixture.mind).run(code, env=env, on_cell=on_cell, on_result=lambda index, result: results.append(result))
        return env, output.getvalue(), results

    def test_current_cell_receives_result_and_finishes_before_next_cell_runs(self):
        fixture = self.fixture([[call("events.append('next')\nprint(events)\nquit()")]])
        env, output, results = self.run_agent(fixture, "events=[]\nr=call_me('go')\nevents.append('tail')")

        self.assertEqual(output, "['tail', 'next']\n")
        self.assertEqual(len(results), 2)
        self.assertTrue(results[-1].quit)
        self.assertTrue(env.shell.user_ns["r"].scheduled)
        self.assertEqual(env.shell.user_ns["r"].output[-1].name, "set_next_cell")
        self.assertEqual(len(fixture.sent), 1)

    def test_multiple_calls_in_one_cell_keep_last_submission(self):
        fixture = self.fixture([[call("print('discarded')")], [call("print('selected')\nquit()", "call_2")]])
        _, output, results = self.run_agent(fixture, "call_me('first')\ncall_me('second')\nprint('tail')")

        self.assertEqual(output, "tail\nselected\n")
        self.assertEqual(len(fixture.sent), 2)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[-1].quit)

    def test_runtime_failure_still_uses_repair_cell(self):
        fixture = self.fixture(
            [[call("raise ValueError('deliberate')")], [call("print('repaired')\nquit()", "call_2")]]
        )
        _, output, results = self.run_agent(fixture, "call_me('go')")

        self.assertEqual(len(fixture.sent), 2)
        self.assertEqual(len(results), 4)
        self.assertIn("deliberate", results[1].error)
        self.assertIn("deliberate", fixture.sent[1]["input"][0]["content"])
        self.assertTrue(results[-1].quit)
        self.assertEqual(output, "repaired\n")


if __name__ == "__main__":
    unittest.main()
