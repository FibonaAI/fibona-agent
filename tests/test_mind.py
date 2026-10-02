import json
import unittest
from functools import partial
from unittest.mock import Mock

import httpx
from openai import OpenAI

from core.agent import Mind


class MindTest(unittest.TestCase):
    def test_input_forms_and_protected_fields_on_wire(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(
                200, json={"id": "r", "object": "response", "created_at": 0, "model": "test", "output": []}
            )

        extra = {
            "model": "override",
            "instructions": "override",
            "input": [],
            "tools": [],
            "reasoning": {"effort": "low"},
            "store": True,
            "stream": True,
            "background": True,
            "parallel_tool_calls": True,
            "previous_response_id": "old",
            "provider_option": "kept",
        }
        with OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            for debug in (False, True):
                for positional in (False, True):
                    with self.subTest(debug=debug, positional=positional):
                        log = Mock() if debug else None
                        call_me = partial(
                            Mind(client, "test", "fixed").wake,
                            submit=Mock(),
                            context=lambda: "memory",
                            log=log,
                            extra_body=extra,
                        )
                        result = call_me("hello") if positional else call_me(input="hello")
                        body = requests[-1]
                        self.assertEqual(body["model"], "test")
                        self.assertEqual(body["instructions"], "fixed")
                        self.assertEqual(body["input"][-1], {"role": "user", "content": "hello"})
                        self.assertEqual(body["tools"][-1]["name"], "set_next_cell")
                        self.assertEqual(body["reasoning"], {"effort": "medium"})
                        for key in ("store", "stream", "background", "parallel_tool_calls"):
                            self.assertFalse(body.get(key, False))
                        self.assertNotIn("previous_response_id", body)
                        self.assertEqual(body["provider_option"], "kept")
                        if debug:
                            log.assert_called_once()
                            self.assertEqual(log.call_args.kwargs["request"]["input"], "hello")
                            self.assertIs(log.call_args.kwargs["response"], result)
        self.assertTrue(extra["store"])
        self.assertEqual(extra["instructions"], "override")
