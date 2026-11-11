import io
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from openai import APIError, Stream
from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall, ResponseTextDeltaEvent
from openai.types.responses.response_usage import ResponseUsage

from core.agent import Mind
from core.utils import JsonFormatter


def response(identifier, *, tokens=100, output=None, status="completed"):
    return Response.model_construct(
        id=identifier,
        model="test-model",
        status=status,
        error=None,
        output=output or [],
        usage=ResponseUsage.model_validate(
            {
                "input_tokens": tokens,
                "input_tokens_details": {"cached_tokens": tokens // 2, "cache_write_tokens": 7},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 3},
                "total_tokens": tokens + 10,
                "cost": 0.123,
            }
        ),
    )


class ApiLoggingTests(unittest.TestCase):
    def setUp(self):
        self.buffer = io.StringIO()
        handler = logging.StreamHandler(self.buffer)
        handler.setFormatter(JsonFormatter("test-run"))
        logger = logging.Logger("test")
        logger.addHandler(handler)
        self.logger = logging.LoggerAdapter(logger, {"cell": 4})
        self.client = SimpleNamespace(responses=SimpleNamespace(create=Mock()))
        self.mind = Mind(self.client, "test-model", "instructions")

    def records(self, event):
        return [r for line in self.buffer.getvalue().splitlines() if (r := json.loads(line))["event"] == event]

    def test_wake_records_each_round_without_changing_logical_usage(self):
        call = ResponseFunctionToolCall.model_construct(
            id="fc1",
            type="function_call",
            call_id="tool1",
            name="set_next_cell",
            arguments=json.dumps({"code": "print(1)", "summary": "test"}),
        )
        self.client.responses.create.side_effect = [response("r1", output=[call]), response("r2", tokens=200)]
        result = self.mind.wake("test", submit=Mock(), logger=self.logger)
        requests, replies = self.records("api_request"), self.records("api_response")
        self.assertEqual([r["usage"]["input_tokens"] for r in replies], [100, 200])
        self.assertEqual([r["api_call_index"] for r in replies], [1, 2])
        self.assertEqual(len({r["request_id"] for r in replies}), 1)
        self.assertEqual(len({r["api_call_id"] for r in replies}), 2)
        self.assertEqual([r["api_call_id"] for r in replies], [r["api_call_id"] for r in requests])
        self.assertEqual(replies[0]["usage"]["input_tokens_details"]["cache_write_tokens"], 7)
        self.assertEqual(replies[0]["usage"]["cost"], 0.123)
        self.assertEqual(replies[0]["response"]["output"][0], call.model_dump(mode="json"))
        self.assertEqual(replies[0]["response"]["usage"]["input_tokens"], 100)
        self.assertEqual(replies[1]["response"]["usage"]["input_tokens"], 200)
        self.assertEqual(requests[0]["request"]["input"], [{"role": "user", "content": "test"}])
        self.assertEqual(requests[1]["request"]["input"][-1]["type"], "function_call_output")
        self.assertIn("instructions", requests[0]["request"])
        self.assertIn("tools", requests[0]["request"])
        self.assertEqual(result.usage.input_tokens, 100)
        self.assertEqual(result.usage.output_tokens, 20)
        self.assertEqual(len(self.records("model_response")), 1)

    def test_error_after_success_keeps_first_round_usage(self):
        call = ResponseFunctionToolCall.model_construct(
            id="fc1",
            type="function_call",
            call_id="tool1",
            name="set_next_cell",
            arguments=json.dumps({"code": "print(1)", "summary": "test"}),
        )
        self.client.responses.create.side_effect = [response("r1", output=[call]), RuntimeError("offline")]
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.mind.wake("test", submit=Mock(), logger=self.logger)
        self.assertEqual(len(self.records("api_response")), 1)
        self.assertEqual(self.records("api_error")[0]["api_call_index"], 2)
        self.assertFalse(self.records("model_response"))

    def test_failed_response_usage_is_recorded_before_wake_raises(self):
        self.client.responses.create.return_value = response("failed", status="failed")
        with self.assertRaises(APIError):
            self.mind.wake("test", submit=Mock(), logger=self.logger)
        self.assertEqual(self.records("api_response")[0]["status"], "failed")
        self.assertEqual(self.records("api_response")[0]["usage"]["input_tokens"], 100)

    def test_query_stream_preserves_identity_events_and_usage(self):
        end = ResponseCompletedEvent.model_construct(type="response.completed", response=response("stream-end"))
        delta = ResponseTextDeltaEvent.model_construct(type="response.output_text.delta", delta="full streamed text")
        stream = Stream.__new__(Stream)
        stream._iterator = iter([delta, end])
        self.client.responses.create.return_value = stream
        actual = self.mind.query("test", stream=True, logger=self.logger)
        self.assertIs(actual, stream)
        self.assertEqual(len(list(actual)), 2)
        self.assertEqual(self.records("api_response")[0]["response_id"], "stream-end")
        events = self.records("api_stream_event")
        self.assertEqual(events[0]["stream_event"]["delta"], "full streamed text")
        self.assertEqual(events[1]["stream_event"]["response"]["id"], "stream-end")
        self.assertFalse(self.records("api_incomplete"))

    def test_complete_response_is_not_truncated(self):
        original = response("long-response").model_copy(update={"provider_extension": {"text": "x" * 100_000}})
        self.client.responses.create.return_value = original
        self.mind.query("long input" * 10_000, logger=self.logger)
        logged = self.records("api_response")[0]["response"]
        self.assertEqual(logged, original.model_dump(mode="json"))
        self.assertEqual(self.records("api_request")[0]["request"]["input"], "long input" * 10_000)

    def test_api_error_keeps_sdk_error_body(self):
        error = APIError("provider failed", request=None, body={"error": {"code": "quota", "detail": "full detail"}})
        self.client.responses.create.side_effect = error
        with self.assertRaises(APIError):
            self.mind.query("test", logger=self.logger)
        self.assertEqual(self.records("api_error")[0]["response_body"], error.body)

    def test_incomplete_stream_is_not_logged_as_zero_usage(self):
        stream = Stream.__new__(Stream)
        stream._iterator = iter([])
        self.client.responses.create.return_value = stream
        list(self.mind.query("test", stream=True, logger=self.logger))
        self.assertEqual(len(self.records("api_incomplete")), 1)
        self.assertFalse(self.records("api_response"))

    def test_query_nonstream_is_logged_once_at_each_level(self):
        self.client.responses.create.return_value = response("plain")
        self.mind.query("test", logger=self.logger)
        self.assertEqual(len(self.records("api_request")), 1)
        self.assertEqual(len(self.records("api_response")), 1)
        self.assertEqual(len(self.records("model_response")), 1)


if __name__ == "__main__":
    unittest.main()
