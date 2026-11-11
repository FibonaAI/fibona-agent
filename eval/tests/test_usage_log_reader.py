import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from plugins.agent import Fibona


class UsageLogReaderTests(unittest.TestCase):
    def collect(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "run.jsonl").write_text(text, encoding="utf-8")
            agent = SimpleNamespace(logs_dir=path, model_name="test", logger=Mock())
            context = SimpleNamespace(metadata=None)
            with patch("plugins.agent.convert", return_value=None):
                Fibona.populate_context_post_run(agent, context)
            return context, agent.logger

    def record(self):
        return json.dumps(
            {
                "event": "model_response",
                "response": {
                    "output_text": "unicode: \x85\u2028\u2029",
                    "usage": {
                        "input_tokens": 100,
                        "input_tokens_details": {"cached_tokens": 50},
                        "output_tokens": 20,
                        "cost": 0.1,
                    },
                },
            },
            ensure_ascii=False,
        )

    def test_unicode_line_separators_are_not_record_boundaries(self):
        context, logger = self.collect(self.record() + "\n" + self.record() + "\n")
        self.assertEqual(context.n_input_tokens, 200)
        self.assertEqual(context.n_cache_tokens, 100)
        self.assertEqual(context.n_output_tokens, 40)
        self.assertAlmostEqual(context.cost_usd, 0.2)
        self.assertFalse(context.metadata["incomplete"])
        logger.warning.assert_not_called()

    def test_real_malformed_lines_report_once_and_preserve_unknown_totals(self):
        context, logger = self.collect(self.record() + '\n{"truncated":\ninvalid\n')
        self.assertIsNone(context.n_input_tokens)
        self.assertTrue(context.metadata["incomplete"])
        logger.warning.assert_called_once()
        self.assertEqual(logger.warning.call_args.args[2], [2, 3])


if __name__ == "__main__":
    unittest.main()
