"""Python core, launcher and real-terminal behavior, grouped by responsibility."""

import asyncio
import importlib.util
import io
import json
import os
import select
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from openai import AsyncOpenAI

from experiments.new_core.runtimes.python.runtime import REPL

BOOTSTRAP = (Path(__file__).resolve().parents[1] / "bootstraps" / "python" / "main.py").read_text(encoding="utf-8")
START = 'await run_agent("test task")'
MAIN = str(Path(__file__).resolve().parents[1] / "main.py")


class Item(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self).copy()


def response(code=None, *, call_id="call", arguments=None):
    output = []
    if code is not None or arguments is not None:
        output.append(
            Item(
                type="function_call",
                name="run_cell",
                call_id=call_id,
                arguments=arguments if arguments is not None else json.dumps({"code": code}),
            )
        )
    return SimpleNamespace(output=output, output_text="" if output else "Done")


class NewCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.console = io.StringIO()
        with redirect_stdout(self.console):
            self.repl = REPL()
        with (
            patch.dict(os.environ, {"FIBONA_SESSION": ""}),
            patch("builtins.input", side_effect=EOFError),
            redirect_stdout(self.console),
        ):
            result = await self.repl.execute(BOOTSTRAP)
        self.assertIsNone(result)
        self.ns = self.repl.shell.user_ns

    async def asyncTearDown(self):
        self.repl.close()

    async def resume_session(self, path):
        repl = REPL()
        try:
            with (
                patch.dict(os.environ, {"FIBONA_SESSION": str(path)}),
                patch("builtins.input", side_effect=EOFError),
                redirect_stdout(self.console),
            ):
                await repl.execute(BOOTSTRAP)
            self.assertNotIn("side_effect", repl.shell.user_ns)
            return repl.shell.user_ns["session"]
        finally:
            repl.close()

    async def launch(self, responses):
        self.contexts = []

        async def fake_model(context):
            self.contexts.append(context)
            return next(responses)

        self.repl.bind(request_model=fake_model)
        result = await asyncio.wait_for(self.repl.execute(START), 3)
        self.assertIsNone(result)
        return result

    def add_steps(self, count=5, output_size=400, path=None):
        if path:
            self.repl.bind(session=self.ns["Session"](path))
            self.repl.bind(context=self.ns["Context"](self.ns["session"]))
        session = self.ns["session"]
        session.append("message", message={"role": "user", "content": "Preserve my original goal and constraints"})
        for index in range(count):
            session.append(
                "model_response",
                status="completed",
                usage=None,
                output=[
                    {"type": "reasoning", "id": f"rs_{index}", "summary": [], "encrypted_content": "opaque"},
                    {
                        "type": "function_call",
                        "name": "run_cell",
                        "call_id": str(index),
                        "arguments": '{"code":"print(1)"}',
                    },
                ],
            )
            cell = session.append("cell_start", code="print(1)", parent_cell_id=None, call_id=str(index))
            session.append("cell_end", cell_id=cell, output="x" * output_size, error=None, status="completed")
        return session

    # Execution and output audiences

    async def test_raw_runtime_has_no_agent_output_or_error_policy(self):
        repl = REPL()
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertIsNone(await repl.execute("print('raw')"))
                with self.assertRaisesRegex(ValueError, "raw failure"):
                    await repl.execute("raise ValueError('raw failure')")
            self.assertEqual(output.getvalue(), "raw\n")
            with self.assertRaises(SyntaxError):
                await repl.execute("def broken(")
        finally:
            repl.close()

    async def test_nested_cells_have_parent_ids_and_separate_outputs(self):
        result = await self.ns["run_cell"](
            "import asyncio; await asyncio.sleep(0); print('outer'); child = await run_cell(\"print('inner')\")",
            call_id="outer-call",
        )
        log = self.ns["session"]
        outer, inner = log.cells
        self.assertEqual(inner["parent_cell_id"], outer["cell_id"])
        self.assertIsNone(inner["call_id"])
        self.assertEqual(outer, result)
        self.assertEqual(outer["output"], "outer\n")
        self.assertEqual(inner["output"], "inner\n")
        projected = self.ns["context"].build()
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["call_id"], "outer-call")
        self.assertNotIn("inner", projected[0]["output"])
        self.assertIsNone(self.ns["active_cell"].get())

    async def test_cells_share_state_and_return_errors_to_agent(self):
        await self.launch(
            iter(
                [
                    response("value = 41\nprint(value)", call_id="one"),
                    response("value += 1\nraise ValueError('repair me')", call_id="two"),
                    response("print(value)", call_id="three"),
                    response(),
                ]
            )
        )
        cells = self.ns["session"].cells
        self.assertEqual(
            [c["cell_id"] for c in cells], [r["id"] for r in self.ns["session"].records if r["type"] == "cell_start"]
        )
        self.assertEqual(cells[0]["output"], "41\n")
        self.assertIn("ValueError: repair me", cells[1]["error"])
        self.assertEqual(cells[2]["output"], "42\n")
        self.assertEqual(self.console.getvalue(), "")
        outputs = [m for m in self.contexts[-1] if m.get("type") == "function_call_output"]
        self.assertEqual([m["call_id"] for m in outputs], ["one", "two", "three"])

    async def test_syntax_error_is_returned_as_cell_result(self):
        await self.launch(iter([response("def broken("), response()]))
        self.assertIn("SyntaxError", self.ns["session"].cells[-1]["error"])

    async def test_concurrent_cells_rejected_but_concurrent_io_works(self):
        await self.launch(
            iter(
                [
                    response(
                        "import asyncio\nvalues = await asyncio.gather(asyncio.sleep(0, result=1), asyncio.sleep(0, result=2))\nprint(values)",
                        call_id="io",
                    ),
                    response(
                        "rejected = await asyncio.gather(run_cell('print(1)'), run_cell('print(2)'))", call_id="cells"
                    ),
                    response(),
                ]
            )
        )
        cells = self.ns["session"].cells
        self.assertEqual(cells[0]["output"], "[1, 2]\n")
        for result in self.ns["rejected"]:
            self.assertIn("Concurrent cells are not supported", result["error"])
        self.assertEqual(cells[1]["output"], "")

    async def test_only_explicit_display_reaches_user_in_loop(self):
        self.repl.bind(user_value="visible answer")
        result = response("print('hidden inspection'); display(user_value)")
        result.output_text = "hidden reply"
        final = response()
        final.output_text = "hidden final"
        await self.launch(iter([result, final]))
        self.assertIn("visible answer", self.console.getvalue())
        self.assertNotIn("hidden", self.console.getvalue())
        self.assertEqual(self.ns["session"].cells[-1]["output"], "hidden inspection\n")
        self.assertIn("hidden inspection", self.contexts[1][-1]["output"])
        self.assertNotIn("visible answer", json.dumps(self.ns["session"].records))
        self.assertNotIn("visible answer", json.dumps(self.contexts[1]))
        self.assertIn("display(user_value)", self.ns["session"].cells[-1]["code"])

    async def test_agent_input_prompt_bypasses_capture(self):
        self.assertNotIn("input", self.ns)
        with patch("builtins.input", return_value="answer") as read:
            result = await self.ns["run_cell"](
                "display('visible'); display('question', end=''); answer = input(); print(answer)"
            )
        read.assert_called_once_with()
        self.assertEqual(self.console.getvalue(), "visible\nquestion")
        self.assertEqual(result["output"], "answer\n")
        self.assertIsNone(result["error"])

    async def test_keyboard_interrupt_unwinds_agent_capture(self):
        with self.assertRaises(KeyboardInterrupt):
            await self.repl.execute("await run_cell(\"await run_cell('raise KeyboardInterrupt')\")")
        self.assertIsNone(self.repl.owner)
        result = await self.ns["run_cell"]("print('usable')")
        self.assertEqual(result["output"], "usable\n")

    async def test_cancel_stops_active_cell(self):
        entered = asyncio.Event()
        self.repl.bind(entered=entered)
        task = asyncio.create_task(self.repl.execute("import asyncio\nentered.set()\nawait asyncio.Event().wait()"))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.repl.owner)
        result = await self.ns["run_cell"]("print('still usable')")
        self.assertEqual(result["output"], "still usable\n")

    async def test_cancelling_parent_unwinds_child_and_closes_client(self):
        entered = asyncio.Event()

        class Client:
            closed = False

            async def close(self):
                self.closed = True

        client = Client()

        async def fake_model(context):
            return response("import asyncio\nentered.set()\nawait asyncio.Event().wait()")

        self.repl.bind(client=client, entered=entered, request_model=fake_model)
        task = asyncio.create_task(self.repl.execute(START))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(client.closed)
        self.assertIsNone(self.repl.owner)
        self.assertEqual(len(self.ns["session"].cells), 1)
        self.assertEqual([c["status"] for c in self.ns["session"].cells[-1:]], ["interrupted"])

    # Session facts and persistence

    async def test_response_usage_is_one_fact_and_outputs_are_not_stored_as_messages(self):
        result = response("print('ONCE_ONLY_OUTPUT')", call_id="fact-call")
        result.id = "response-fact"
        result.usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
        await self.launch(iter([result, response()]))
        records = self.ns["session"].records
        fact = next(r for r in records if r["type"] == "model_response")
        self.assertEqual(fact["response_id"], "response-fact")
        self.assertEqual(fact["usage"], {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        self.assertEqual(fact["status"], "completed")
        self.assertFalse(any(r["type"] in ("usage", "incomplete_response") for r in records))
        self.assertTrue(all("items" not in r for r in records))
        self.assertTrue(
            all(r["message"].get("type") != "function_call_output" for r in records if r["type"] == "message")
        )
        end = next(r for r in records if r["type"] == "cell_end")
        self.assertEqual(end["output"], "ONCE_ONLY_OUTPUT\n")
        start = records[end["cell_id"]]
        self.assertEqual(start["id"], end["cell_id"])
        self.assertEqual(start["call_id"], "fact-call")
        self.assertIsNone(start["parent_cell_id"])
        self.assertEqual(self.ns["session"].usage[0]["response_id"], fact["id"])

    async def test_session_contains_bootstrap_and_nested_execution(self):
        self.assertEqual(self.ns["session"].cells, [])
        sources = [r for r in self.ns["session"].records if r["type"] == "source"]
        self.assertEqual([r["code"] for r in sources], [BOOTSTRAP.rstrip()])
        result = await self.ns["run_cell"]("inner = await run_cell(\"print('direct child')\")")
        cells = self.ns["session"].cells
        self.assertEqual(cells[0], result)
        self.assertEqual(cells[1], self.ns["inner"])
        self.assertEqual(cells[1]["output"], "direct child\n")
        self.assertEqual(cells[0]["output"], "")
        before = len(cells)
        await self.repl.execute("host_value = 42")
        self.assertEqual(len(self.ns["session"].cells), before)
        result = await self.ns["run_cell"]("raise ValueError('direct failure')")
        self.assertIn("direct failure", result["error"])
        self.assertEqual(result["status"], "error")

    async def test_bootstrap_source_is_persisted_and_numbering_continues_on_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            for _ in range(2):
                repl = REPL()
                try:
                    with (
                        patch.dict(os.environ, {"FIBONA_SESSION": str(path)}),
                        patch("builtins.input", side_effect=EOFError),
                        redirect_stdout(self.console),
                    ):
                        await repl.execute(BOOTSTRAP)
                        await repl.shell.user_ns["run_cell"]("persisted_value = 42")
                    session = repl.shell.user_ns["session"]
                    self.assertEqual(
                        [r for r in session.records if r["type"] == "source"][-1]["code"], BOOTSTRAP.rstrip()
                    )
                    self.assertEqual(session.records[session.cells[-1]["cell_id"]]["type"], "cell_start")
                    self.assertTrue(all(c["status"] == "completed" for c in session.cells))
                finally:
                    repl.close()
            restored = self.ns["Session"](path)
            self.assertEqual(len(restored.cells), 2)
            self.assertEqual([r["code"] for r in restored.records if r["type"] == "source"], [BOOTSTRAP.rstrip()] * 2)
            self.assertLess(restored.cells[0]["cell_id"], restored.cells[1]["cell_id"])

    async def test_message_is_one_fact_and_active_parent_is_not_closed_early(self):
        log = self.ns["session"]
        message = {"role": "user", "content": "one message"}
        record_id = log.append("message", message=message)
        self.assertEqual(log.records[record_id]["message"], message)
        self.assertEqual(self.ns["context"].build(), [message])
        await self.launch(
            iter([response("await run_cell(\"repair_pending(); print('child')\"); print('parent')"), response()])
        )
        parent, child = log.cells
        self.assertEqual(child["parent_cell_id"], parent["cell_id"])
        self.assertEqual(parent["status"], "completed")
        self.assertEqual(child["status"], "completed")
        self.assertEqual(len([r for r in log.records if r["type"] == "cell_end"]), 2)
        self.assertFalse(any(r["type"] == "tool_error" for r in log.records))

    async def test_tool_result_is_projected_without_duplicate_storage(self):
        code = "value = 42; print(value)"
        result = await self.ns["run_cell"](code, call_id="source-check")
        session = self.ns["session"]
        message = json.loads(self.ns["context"].build()[-1]["output"])
        self.assertNotIn("code", message)
        self.assertEqual(message["output"], "42\n")
        self.assertEqual(result["code"], code)
        self.assertEqual(session.cells[-1], result)
        self.assertEqual([r["type"] for r in session.records[-2:]], ["cell_start", "cell_end"])
        self.assertEqual(session.records[-1]["cell_id"], session.records[-2]["id"])
        self.assertEqual(session.messages, [])

    async def test_session_restores_records_without_replaying_unfinished_call(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            session = self.ns["Session"](path)
            session.append("message", message={"role": "user", "content": "task"})
            session.append(
                "model_response",
                status="completed",
                output=[
                    {
                        "type": "function_call",
                        "name": "run_cell",
                        "call_id": "unfinished",
                        "arguments": '{"code":"side_effect()"}',
                    }
                ],
                usage=None,
            )
            original = path.read_bytes()
            loaded = self.ns["Session"](path)
            self.assertEqual(loaded.records, session.records)
            self.assertEqual(path.read_bytes(), original)
            restored = await self.resume_session(path)
            self.assertTrue(restored.resumed)
            self.assertEqual(restored.messages, session.messages)
            result = self.ns["Context"](restored).build()[-1]
            self.assertEqual(result["call_id"], "unfinished")
            self.assertIn("effects unknown", result["output"])
            self.assertEqual(restored.cells, [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_restart_closes_started_tool_once_without_duplicate_tool_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "facts.jsonl"
            log = self.ns["Session"](path)
            log.append(
                "model_response",
                status="completed",
                usage=None,
                output=[
                    {
                        "type": "function_call",
                        "name": "run_cell",
                        "call_id": "stopped",
                        "arguments": '{"code":"side_effect()"}',
                    }
                ],
            )
            parent = log.append("cell_start", code="side_effect()", parent_cell_id=None, call_id="stopped")
            child = log.append("cell_start", code="nested()", parent_cell_id=parent, call_id=None)
            restored = await self.resume_session(path)
            ends = [r for r in restored.records if r["type"] == "cell_end"]
            self.assertEqual([r["cell_id"] for r in ends], [child, parent])
            self.assertTrue(all(r["status"] == "interrupted" for r in ends))
            self.assertFalse(any(r["type"] == "tool_error" for r in restored.records))
            outputs = [m for m in self.ns["Context"](restored).build() if m.get("type") == "function_call_output"]
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0]["call_id"], "stopped")
            again = await self.resume_session(path)
            self.assertEqual(len([r for r in again.records if r["type"] == "cell_end"]), 2)

    async def test_log_is_append_only_and_recovers_unfinished_cells_once(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            session = self.ns["Session"](path)
            items = [{"role": "user", "content": "original"}]
            session.append("message", message=items[0])
            prefix = path.read_bytes()
            items[0]["content"] = "mutated caller data"
            session.append("cell_start", code="side_effect()", parent_cell_id=None, call_id=None)
            self.assertTrue(path.read_bytes().startswith(prefix))
            prefix = path.read_bytes()
            restored = self.ns["Session"](path)
            self.assertEqual(path.read_bytes(), prefix)
            self.assertEqual(restored.cells[0]["status"], "running")
            restored = await self.resume_session(path)
            self.assertTrue(path.read_bytes().startswith(prefix))
            self.assertEqual(restored.messages[0]["content"], "original")
            self.assertEqual(restored.cells[0]["status"], "interrupted")
            self.assertIn("effects unknown", restored.cells[0]["error"])
            self.assertEqual(restored.records[-2]["type"], "cell_end")
            content = path.read_bytes()
            self.ns["Session"](path)
            self.assertEqual(path.read_bytes(), content)
            again = await self.resume_session(path)
            self.assertEqual(sum(r["type"] == "cell_end" for r in again.records), 1)
            entries = [json.loads(line) for line in content.splitlines()]
            self.assertEqual([entry["id"] for entry in entries], list(range(len(entries))))

    async def test_log_recovers_only_an_incomplete_tail(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            session = self.ns["Session"](path)
            session.append("message", message={"role": "user", "content": "中文"})
            prefix = path.read_bytes()
            with path.open("ab") as file:
                file.write(b'{"id":2,"text":"\xe4')
            restored = self.ns["Session"](path)
            self.assertEqual(path.read_bytes(), prefix)
            restored.append("message", message={"role": "user", "content": "after recovery"})
            self.assertEqual(len(self.ns["Session"](path).messages), 2)
            # A complete record missing only its newline must be preserved.
            path.write_bytes(path.read_bytes().rstrip(b"\n"))
            restored = self.ns["Session"](path)
            self.assertEqual(len(restored.messages), 2)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            corrupted = path.read_bytes() + b"broken complete line\n"
            path.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError, "Invalid session log"):
                self.ns["Session"](path)
            self.assertEqual(path.read_bytes(), corrupted)

    async def test_previous_session_version_is_rejected_without_modification(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.jsonl"
            content = '{"type":"session","id":0,"version":3}\n'
            path.write_text(content)
            with self.assertRaisesRegex(ValueError, "version 4"):
                self.ns["Session"](path)
            self.assertEqual(path.read_text(), content)

    async def test_old_snapshot_is_rejected_without_modification(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            content = json.dumps({"version": 2, "id": "old", "messages": []}).encode()
            path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "old snapshots are not modified"):
                self.ns["Session"](path)
            self.assertEqual(path.read_bytes(), content)

    # Context boundaries and budget

    async def test_context_is_bound_to_its_log_not_the_global_session(self):
        other = self.ns["Session"]()
        other.append("message", message={"role": "user", "content": "other task"})
        view = self.ns["Context"](other)
        current = list(self.ns["session"].records)
        self.assertEqual(view.build(), [{"role": "user", "content": "other task"}])
        view.reset("only the other task")
        self.assertIn("only the other task", view.build()[0]["content"])
        self.assertEqual(self.ns["session"].records, current)
        self.assertEqual(other.messages[0]["content"], "other task")

    async def test_context_keeps_all_turns_since_boundary(self):
        session = self.add_steps(count=6)
        items = self.ns["context"].build()
        self.assertEqual(len([m for m in items if m.get("type") == "function_call_output"]), 6)
        self.assertEqual(len([m for m in items if m.get("type") == "function_call"]), 6)
        self.assertEqual(items[0], session.messages[0])

    async def test_reset_with_or_without_handoff_is_local_and_persistent(self):
        with TemporaryDirectory() as directory:
            session = self.add_steps(path=Path(directory) / "session.jsonl")
            original = list(session.records)
            model = AsyncMock()
            self.repl.bind(request_model=model)
            ctx = self.ns["context"]
            first = ctx.reset(handoff="Goal and verified progress; next check.")
            self.assertEqual(session.records[:first], original)
            self.assertEqual(
                ctx.build()[0]["content"],
                "Continuation context; verify against records:\nGoal and verified progress; next check.",
            )
            second = ctx.reset()
            self.assertIn("Recover intent", ctx.build()[0]["content"])
            self.assertEqual(session.records[second]["first_kept"], second)
            self.assertFalse(any(r["type"] == "mode" for r in session.records))
            model.assert_not_awaited()
            expected = ctx.build()
            restored = self.ns["Session"](session.path)
            self.assertEqual(self.ns["Context"](restored).build()[1:], expected)

    async def test_reset_validation_and_failed_write_leave_context_unchanged(self):
        self.add_steps()
        session = self.ns["session"]
        before = list(session.records)
        ctx = self.ns["context"]
        with self.assertRaises(TypeError):
            ctx.reset(handoff={})
        with patch.object(session, "append", side_effect=OSError("disk failed")):
            with self.assertRaises(OSError):
                ctx.reset("next")
        self.assertEqual(session.records, before)

    async def test_reset_preserves_entire_active_batch_and_nested_failure(self):
        first = response(
            "await run_cell(\"context.reset(handoff='continue after batch')\"); raise ValueError('partial effects')",
            call_id="first",
        )
        first.output += response("print('second completed')", call_id="second").output
        await self.launch(iter([first, response()]))
        continued = self.contexts[-1]
        calls = [m["call_id"] for m in continued if m.get("type") == "function_call"]
        outputs = [m for m in continued if m.get("type") == "function_call_output"]
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual([m["call_id"] for m in outputs], calls)
        self.assertIn("partial effects", outputs[0]["output"])
        self.assertIn("second completed", outputs[1]["output"])
        self.assertEqual(self.ns["session"].cells[1]["status"], "completed")
        self.assertEqual(self.ns["session"].cells[0]["status"], "error")

    async def test_large_tool_projection_keeps_full_saved_result(self):
        session = self.add_steps(1, output_size=1000)
        self.ns["context"].max_output_chars = 100
        projected = self.ns["context"].build()[-1]["output"]
        self.assertIn(f"session.records[{session.records[-1]['id']}]['output']", projected)
        self.assertLess(len(projected), 300)
        self.assertEqual(session.records[-1]["output"], "x" * 1000)

    async def test_usage_estimate_resets_and_pending_repair_keeps_history(self):
        session = self.add_steps(1000)
        session.append("model_response", status="completed", output=[], usage={"input_tokens": 900000})
        self.assertGreaterEqual(self.ns["context"].estimate(self.ns["context"].build()), 900000)
        self.ns["context"].reset("continue")
        self.assertLess(self.ns["context"].estimate(self.ns["context"].build()), 900000)
        original = list(session.records)
        session.append(
            "model_response",
            status="completed",
            usage=None,
            output=[{"type": "function_call", "call_id": "pending-new", "name": "run_cell", "arguments": "{}"}],
        )
        self.ns["repair_pending"]()
        self.assertEqual(session.records[: len(original)], original)
        result = self.ns["context"].build()[-1]
        self.assertEqual(result["call_id"], "pending-new")
        self.assertIn("effects unknown", result["output"])
        count = len(session.records)
        self.ns["repair_pending"]()
        self.assertEqual(len(session.records), count)
        session.append("model_response", status="completed", output=[], usage={"input_tokens": 50000})
        self.assertGreaterEqual(self.ns["context"].estimate(self.ns["context"].build()), 50000)

    async def test_near_budget_only_advises_agent_without_summary_request(self):
        self.repl.bind(context_window=10000)
        self.ns["model_options"]["max_output_tokens"] = 1000
        self.ns["context"].estimate = Mock(return_value=8000)
        model = AsyncMock(return_value=response())
        self.repl.bind(request_model=model)
        await self.ns["run_agent"]("continue")
        model.assert_awaited_once()
        self.assertIn("context.reset", model.call_args.args[0][-1]["content"])
        self.assertFalse(any(r["type"] == "context_reset" for r in self.ns["session"].records))

    # Model protocol and conversation

    async def test_request_model_uses_explicit_input_without_recording_or_execution(self):
        result = response("unexpected_execution = True")
        result.usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
        create = AsyncMock(return_value=result)
        self.repl.bind(client=SimpleNamespace(responses=SimpleNamespace(create=create)))
        session = self.ns["session"]
        before = list(session.records)
        supplied = [{"role": "user", "content": "explicit request"}]
        got = await self.ns["request_model"](supplied, tools=[], instructions="custom", max_output_tokens=100)
        self.assertIs(got, result)
        self.assertIs(create.call_args.kwargs["input"], supplied)
        self.assertEqual(create.call_args.kwargs["tools"], [])
        self.assertEqual(create.call_args.kwargs["instructions"], "custom")
        self.assertEqual(create.call_args.kwargs["max_output_tokens"], 100)
        self.ns["model_options"].update(model="test-model", max_output_tokens=2048)
        await self.ns["request_model"](supplied)
        self.assertEqual(create.call_args.kwargs["model"], "test-model")
        self.assertEqual(create.call_args.kwargs["max_output_tokens"], 2048)
        self.assertEqual(session.records, before)
        self.assertNotIn("unexpected_execution", self.ns)

    async def test_real_sdk_serializes_tool_result_and_reasoning_replay(self):
        requests = []

        def transport(request):
            requests.append(json.loads(request.content))
            output = (
                [
                    {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "opaque"},
                    {
                        "id": "fc_1",
                        "type": "function_call",
                        "name": "run_cell",
                        "call_id": "sdk-call",
                        "arguments": json.dumps({"code": "print(6 * 7)"}),
                        "status": "completed",
                    },
                ]
                if len(requests) == 1
                else [
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "42", "annotations": []}],
                    },
                ]
            )
            return httpx.Response(
                200,
                json={
                    "id": f"resp_{len(requests)}",
                    "object": "response",
                    "created_at": 0,
                    "model": "test",
                    "status": "completed",
                    "output": output,
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                },
            )

        client = AsyncOpenAI(
            api_key="test-only", http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        )
        self.repl.bind(client=client)
        result = await asyncio.wait_for(self.repl.execute(START), 3)
        self.assertIsNone(result)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]["input"][-1]["call_id"], "sdk-call")
        self.assertEqual(json.loads(requests[1]["input"][-1]["output"])["output"], "42\n")
        self.assertEqual(requests[1]["input"][-3]["encrypted_content"], "opaque")
        self.assertTrue(client.is_closed())

    async def test_malformed_tool_payloads_are_results_not_turn_failures(self):
        invalid = ["{", "[]", "null", "{}", '{"code":17}', '{"code":""}']
        unknown = response("must_not_run = True", call_id="unknown")
        unknown.output[0].name = "unknown_tool"
        await self.launch(
            iter(
                [
                    *(response(arguments=arguments, call_id=str(index)) for index, arguments in enumerate(invalid)),
                    unknown,
                    response("print('continued')", call_id="valid"),
                    response(),
                ]
            )
        )
        results = [
            json.loads(m["output"]) for m in self.ns["context"].build() if m.get("type") == "function_call_output"
        ]
        self.assertTrue(all(r.get("error") for r in results[:-1]))
        self.assertEqual(results[-1]["output"], "continued\n")
        self.assertEqual(len(self.ns["session"].cells), 1)
        self.assertNotIn("must_not_run", self.ns)

    async def test_incomplete_response_retries_without_replaying_partial_calls(self):
        incomplete = response("must_not_run = True", call_id="partial")
        incomplete.status = "incomplete"
        await self.launch(iter([incomplete, response("print('complete')", call_id="valid"), response()]))
        self.assertNotIn("must_not_run", self.ns)
        self.assertEqual(self.contexts[0], self.contexts[1])
        self.assertFalse(any(m.get("call_id") == "partial" for m in self.ns["session"].messages))
        self.assertEqual(self.ns["session"].cells[-1]["output"], "complete\n")

    async def test_repeated_incomplete_response_allows_next_user_turn(self):
        incomplete = response("must_not_run = True")
        incomplete.status = "incomplete"
        model = AsyncMock(side_effect=[incomplete, incomplete, response()])
        self.repl.bind(request_model=model)
        with patch("builtins.input", side_effect=["continue", EOFError]):
            await self.ns["run_agent"]("first", interactive=True)
        self.assertEqual(model.await_count, 3)
        self.assertEqual(self.ns["session"].cells, [])
        self.assertEqual(
            sum(r["type"] == "model_response" and r["status"] == "incomplete" for r in self.ns["session"].records), 2
        )
        continued = model.call_args_list[2].args[0]
        self.assertEqual([m["role"] for m in continued], ["user", "assistant", "user"])
        self.assertIn("This turn has ended", continued[1]["content"])
        self.assertNotIn("must_not_run", self.ns)
        self.assertEqual(
            [m["content"] for m in self.ns["session"].messages if m.get("role") == "user"], ["first", "continue"]
        )

    async def test_interactive_turns_keep_state_and_treat_slashes_as_text(self):
        contexts = []

        async def fake_model(context):
            contexts.append(context)
            return response()

        self.repl.bind(request_model=fake_model)
        with patch("builtins.input", side_effect=["second task", "/compact", "/status", "/exit", "/quit", EOFError]):
            await self.repl.execute('await run_agent("first task", interactive=True)')
        self.assertEqual(len(contexts), 6)
        users = [m["content"] for m in contexts[-1] if m.get("role") == "user"]
        self.assertEqual(users, ["first task", "second task", "/compact", "/status", "/exit", "/quit"])

    # Self-modification and bounded recovery

    async def test_cell_can_replace_policy_used_by_next_model_request(self):
        requests = []
        responses = iter(
            [
                response(
                    "instructions['working'] = 'changed instructions'\ncontext.build = lambda: [{'role': 'user', 'content': 'changed context'}]"
                ),
                response(),
            ]
        )

        async def create(**kwargs):
            requests.append(kwargs)
            return next(responses)

        class Client:
            responses = SimpleNamespace(create=create)
            closed = False

            async def close(self):
                self.closed = True

        client = Client()
        self.repl.bind(client=client)
        result = await asyncio.wait_for(self.repl.execute(START), 3)
        self.assertIsNone(result)
        self.assertEqual(
            requests[1]["instructions"],
            "\n\n".join(f"# {name}\n{text}" for name, text in self.ns["instructions"].items()),
        )
        self.assertEqual(
            [line for line in requests[0]["instructions"].splitlines() if line.startswith("# ")],
            ["# identity", "# environment", "# working", "# adaptation"],
        )
        self.assertIn("# working\nchanged instructions", requests[1]["instructions"])
        self.assertIn(self.ns["instructions"]["identity"], requests[0]["instructions"])
        self.assertIn(self.ns["instructions"]["identity"], requests[1]["instructions"])
        self.assertNotIn("changed instructions", requests[0]["instructions"])
        self.assertEqual(requests[1]["input"], [{"role": "user", "content": "changed context"}])
        self.assertTrue(client.closed)

    async def test_recovery_repairs_context_without_using_it_and_shares_prompt(self):
        saved = self.ns["context"]
        broken = SimpleNamespace(build=Mock(side_effect=RuntimeError("context damaged")))
        create = AsyncMock(side_effect=[response("context = saved_context"), response(), response()])
        self.repl.bind(
            saved_context=saved,
            context=broken,
            client=SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock()),
        )
        await self.ns["run_agent"]("original task")
        self.assertIs(self.ns["context"], saved)
        broken.build.assert_called_once()
        self.assertEqual(create.await_count, 3)
        prompts = [call.kwargs["instructions"] for call in create.call_args_list]
        self.assertEqual(len(set(prompts)), 1)
        self.assertEqual(len(create.call_args_list[0].kwargs["input"]), 1)
        self.assertIn("Recovery mode.", create.call_args_list[1].kwargs["input"][-1]["content"])

    async def test_recovery_can_replace_broken_work_loop(self):
        self.repl.bind(
            saved_loop=self.ns["agent_loop"], agent_loop=AsyncMock(side_effect=RuntimeError("broken work loop"))
        )
        model = AsyncMock(
            side_effect=[
                response("context.reset('reset during repair'); agent_loop = saved_loop"),
                response(),
                response(),
            ]
        )
        self.repl.bind(request_model=model)
        await self.ns["run_agent"]("keep working")
        self.assertIs(self.ns["agent_loop"], self.ns["saved_loop"])
        self.assertIn("Recovery mode.", model.call_args_list[1].args[0][-1]["content"])
        self.assertEqual(model.await_count, 3)
        self.assertIn("Recovery mode ended", model.call_args.args[0][-1]["content"])
        self.assertEqual(
            [m["content"] for m in self.ns["session"].messages if m.get("role") == "user"], ["keep working"]
        )

    async def test_recover_only_hands_off_without_invoking_work_loop(self):
        work = AsyncMock()
        self.repl.bind(agent_loop=work, request_model=AsyncMock(return_value=response()))
        self.assertTrue(await self.ns["recover"]("test failure"))
        work.assert_not_awaited()
        self.assertEqual(self.ns["session"].records[-1]["name"], "normal")

    async def test_overflow_enters_recovery_then_continues(self):
        self.ns["context"].estimate = Mock(side_effect=[1000000, 1000])
        model = AsyncMock(side_effect=[response(), response()])
        self.repl.bind(request_model=model)
        await self.ns["run_agent"]("original goal")
        self.assertEqual(model.await_count, 2)
        self.assertIn("Recovery mode.", model.call_args_list[0].args[0][-1]["content"])
        self.assertEqual(
            [r["name"] for r in self.ns["session"].records if r["type"] == "mode"], ["normal", "recovery", "normal"]
        )

    async def test_provider_overflow_uses_general_recovery(self):
        from openai import BadRequestError

        session = self.add_steps()
        calls = []

        async def fake_model(context):
            calls.append(context)
            if len(calls) == 1:
                raise BadRequestError(
                    "too long",
                    response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
                    body={"code": "context_length_exceeded"},
                )
            return response()

        self.repl.bind(request_model=fake_model)
        await self.ns["run_agent"]("continue")
        self.assertEqual(len(calls), 3)
        self.assertIn("too long", calls[1][-1]["content"])
        self.assertEqual(len([r for r in session.records if r["type"] == "context_reset"]), 1)

    async def test_transient_model_failure_recovers_with_diagnostics(self):
        model = AsyncMock(side_effect=[RuntimeError("temporary model failure"), response(), response()])
        self.repl.bind(request_model=model)
        await self.ns["run_agent"]("original task")
        self.assertEqual(model.await_count, 3)
        self.assertIn("temporary model failure", model.call_args_list[1].args[0][-1]["content"])

    async def test_incomplete_recovery_does_not_resume_work(self):
        broken = AsyncMock(side_effect=RuntimeError("broken work loop"))
        incomplete = response()
        incomplete.status = "incomplete"
        self.repl.bind(agent_loop=broken, request_model=AsyncMock(return_value=incomplete))
        await self.ns["run_agent"]("task")
        broken.assert_awaited_once()
        self.assertNotIn("Recovery mode ended", str(self.ns["session"].messages))

    async def test_recovery_failure_propagates_without_repeated_recovery_or_input(self):
        calls = []

        async def broken_model(context):
            calls.append(context)
            raise RuntimeError("model failed")

        self.repl.bind(request_model=broken_model)
        with patch("builtins.input") as read_input:
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                await self.repl.execute('await run_agent("hello", interactive=True)')
            read_input.assert_not_called()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len([r for r in self.ns["session"].records if r["type"] == "context_reset"]), 1)
        self.assertIsNone(self.repl.owner)

    async def test_model_failure_returns_through_start_cell(self):
        async def failing(context):
            raise RuntimeError("model unavailable")

        self.repl.bind(request_model=failing)
        with self.assertRaisesRegex(RuntimeError, "model unavailable"):
            await self.repl.execute(START)
        self.assertIsNone(self.repl.owner)

    async def test_cancelled_agent_does_not_start_recovery(self):
        self.repl.bind(request_model=AsyncMock(side_effect=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await self.ns["run_agent"]("task")
        self.assertFalse(any(r["type"] == "context_reset" for r in self.ns["session"].records))

    async def test_direct_agent_loop_repairs_pending_call_on_failure(self):
        self.repl.bind(
            request_model=AsyncMock(return_value=response("print('not executed')", call_id="direct")),
            run_cell=AsyncMock(side_effect=RuntimeError("executor failed")),
        )
        with self.assertRaisesRegex(RuntimeError, "executor failed"):
            await self.ns["agent_loop"]()
        results = [m for m in self.ns["context"].build() if m.get("type") == "function_call_output"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["call_id"], "direct")
        self.assertIn("effects unknown", results[0]["output"])

    async def test_bootstrap_reports_startup_failure_and_closes_client(self):
        client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("startup failed"))),
            close=AsyncMock(),
        )
        repl = REPL()
        try:
            with (
                patch.dict(os.environ, {"FIBONA_SESSION": ""}),
                patch("builtins.input", return_value="hello") as read_input,
                patch("openai.AsyncOpenAI", return_value=client),
                redirect_stdout(self.console),
                self.assertRaisesRegex(RuntimeError, "startup failed"),
            ):
                await repl.execute(BOOTSTRAP)
            read_input.assert_called_once_with("You: ")
            client.close.assert_awaited_once()
            self.assertIsNone(repl.owner)
            self.assertEqual(repl.shell.user_ns["session"].cells, [])
        finally:
            repl.close()


class LauncherTests(unittest.TestCase):
    def test_select_runtime_and_bootstrap_from_another_directory(self):
        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, MAIN, "--runtime", "python", "--bootstrap", "python"],
                cwd=directory,
                env={**os.environ, "FIBONA_SESSION": ""},
                input="",
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("You:", result.stdout)

    def test_arbitrary_bootstrap_needs_no_agent_entry_point_or_injected_metadata(self):
        with TemporaryDirectory() as directory:
            bootstrap = Path(directory) / "plain.py"
            bootstrap.write_text(
                "assert 'agent_source' not in globals()\n"
                "assert 'argv' not in globals()\n"
                "await run_cell('value = 40 + 2')\n"
                "print(value)\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, MAIN, "--bootstrap", str(bootstrap)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "42")

    def test_failed_arbitrary_bootstrap_propagates_without_reading_input(self):
        with TemporaryDirectory() as directory:
            bootstrap = Path(directory) / "broken.py"
            bootstrap.write_text("raise ValueError('broken bootstrap')\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, MAIN, "--bootstrap", str(bootstrap)],
                input="print('must not execute')\n",
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken bootstrap", result.stderr)
        self.assertNotIn("must not execute", result.stdout)


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("readline"), "requires POSIX readline")
class TerminalTests(unittest.TestCase):
    def test_chinese_editing(self):
        import pty
        import termios

        bootstrap = Path(__file__).resolve().parents[1] / "bootstraps" / "python" / "main.py"
        script = f"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from experiments.new_core.runtimes.python.runtime import REPL

async def main():
    repl = REPL()
    try:
        with patch('builtins.input', side_effect=EOFError):
            await repl.execute(Path({str(bootstrap)!r}).read_text())
        async def model(context):
            message = next(m['content'] for m in reversed(context) if m.get('role') == 'user')
            repl.shell.user_ns['display']('RECEIVED:' + message)
            return SimpleNamespace(output=[])
        repl.bind(request_model=model)
        await repl.execute('await run_agent(interactive=True)')
    finally:
        repl.close()
asyncio.run(main())
"""
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[0] &= ~getattr(termios, "IUTF8", 0)
        attrs[6][termios.VERASE] = b"\x7f"
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "FIBONA_SESSION": "", "PYTHONIOENCODING": "utf-8:strict", "TERM": "dumb"},
        )
        os.close(slave)
        transcript = bytearray()

        def wait_for(marker, count=1):
            deadline = time.monotonic() + 5
            while transcript.count(marker) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                    self.fail("Timed out waiting for input: " + transcript.decode("utf-8", errors="backslashreplace"))
                try:
                    chunk = os.read(master, 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    self.fail("Terminal exited: " + transcript.decode("utf-8", errors="backslashreplace"))
                transcript.extend(chunk)

        try:
            wait_for(b"You: ", 1)
            os.write(master, b"hi\n")
            wait_for(b"You: ", 2)
            os.write(master, "你知道自己在cell里的代码是什么啊".encode() + b"\x7f" + "吗\n".encode())
            wait_for(b"You: ", 3)
            self.assertIn(b"RECEIVED:hi", transcript)
            self.assertIn("RECEIVED:你知道自己在cell里的代码是什么吗".encode(), transcript)
            os.write(master, b"\x04")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not select.select([master], [], [], max(0, deadline - time.monotonic()))[0]:
                    break
                try:
                    chunk = os.read(master, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                transcript.extend(chunk)
            self.assertEqual(process.wait(timeout=1), 0, transcript.decode("utf-8", errors="backslashreplace"))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            os.close(master)
