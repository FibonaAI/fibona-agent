"""Fibona's run logs (*.jsonl, see core) as an ATIF trajectory, which `harbor view` shows: see convert."""

import json
from pathlib import Path

from harbor.models.trajectories import Trajectory

MAX_CONTENT = 64_000  # Characters per message, code or cell output in trajectory.json; the run logs keep everything.
HOST_CELLS = {"Run the bootstrap cell.", "Repair the failed cell."}  # Summaries of the cells core writes itself.


def _clip(text: str) -> str:
    if len(text) <= MAX_CONTENT:
        return text
    half = MAX_CONTENT // 2
    return f"{text[:half]}\n[... {len(text) - MAX_CONTENT} characters omitted ...]\n{text[-half:]}"


def _text(parts) -> str:
    """The text of a Responses content or summary list."""
    if isinstance(parts, str):
        return parts
    return "\n".join(p.get("text") or "" for p in parts or [] if isinstance(p, dict))


def _arguments(raw: str) -> dict:
    try:
        arguments = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"raw": _clip(str(raw))}
    if not isinstance(arguments, dict):
        return {"value": arguments}
    return {k: _clip(v) if isinstance(v, str) else v for k, v in arguments.items()}


def _cell_result(record: dict) -> str:
    parts = [record.get("output") or ""]
    if record.get("error"):
        parts.append("Error:\n" + record["error"])
    if record.get("quit"):
        parts.append("(quit)")
    return _clip("\n".join(p for p in parts if p))


def convert(logs_dir: Path, model_name: str | None = None) -> Trajectory | None:
    """Fibona's run logs (*.jsonl, see core) as an ATIF trajectory.

    A model call is an agent step; the cell its set_next_cell chose runs later, and that cell's result is the
    call's observation. Bootstrap and repair cells, which core writes itself, are system steps; so are model errors.
    instruction.txt, written by run, is the first (user) step.
    """
    steps: list[dict] = []

    def add(**step) -> dict:
        steps.append({"step_id": len(steps) + 1, **step})
        return steps[-1]

    def observe(step: dict, call_id: str | None, content: str) -> None:
        results = step.setdefault("observation", {"results": []})["results"]
        results.append({"source_call_id": call_id, "content": content})

    if (logs_dir / "instruction.txt").exists():
        add(source="user", message=(logs_dir / "instruction.txt").read_text())
    chosen = None  # (step, call_id) of the latest accepted set_next_cell: the cell to run next.
    running = None  # Where the running cell's result goes, likewise.
    for path in sorted(logs_dir.glob("*.jsonl")):
        with open(path) as lines:
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event, time = record.get("event"), record.get("time")
                if event == "cell":
                    if chosen is not None and record.get("summary") not in HOST_CELLS:
                        running, chosen = chosen, None
                        continue
                    if chosen is not None:
                        observe(*chosen, "Not run: the cell that chose it failed, so core ran a repair cell.")
                        chosen = None
                    step = add(
                        timestamp=time,
                        source="system",
                        message=record.get("summary") or "Run a cell.",
                        extra={"code": _clip(record.get("code") or "")},
                    )
                    running = (step, None)
                elif event == "cell_result" and running is not None:
                    observe(*running, _cell_result(record))
                    running = None
                elif event == "model_error":
                    error = record.get("error") or {}
                    add(
                        timestamp=time,
                        source="system",
                        message=f"Model call failed: {error.get('type')}: {error.get('message')}",
                    )
                elif event == "model_response":
                    response = record.get("response") or {}
                    output = response.get("output") or []
                    step = add(
                        timestamp=time,
                        source="agent",
                        model_name=response.get("model"),
                        message=_clip(
                            "\n\n".join(_text(o.get("content")) for o in output if o.get("type") == "message")
                        ),
                    )
                    reasoning = "\n\n".join(
                        _text(o.get("summary")) or _text(o.get("content"))
                        for o in output
                        if o.get("type") == "reasoning"
                    )
                    if reasoning:
                        step["reasoning_content"] = _clip(reasoning)
                    calls = [o for o in output if o.get("type") == "function_call"]
                    if calls:
                        step["tool_calls"] = [
                            {
                                "tool_call_id": c["call_id"],
                                "function_name": c["name"],
                                "arguments": _arguments(c.get("arguments")),
                            }
                            for c in calls
                        ]
                    usage = response.get("usage") or {}
                    metrics = {
                        "prompt_tokens": usage.get("input_tokens"),
                        "completion_tokens": usage.get("output_tokens"),
                        "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
                        "cost_usd": usage.get("cost"),
                    }
                    if any(v is not None for v in metrics.values()):
                        step["metrics"] = {k: v for k, v in metrics.items() if v is not None}
                    if record.get("source") != "call_me":
                        step["extra"] = {"source": record.get("source")}
                    # set_next_cell results are in the output too; the last accepted one chooses the next cell.
                    results = {
                        o.get("call_id"): o.get("output") for o in output if o.get("type") == "function_call_output"
                    }
                    for call in calls:
                        if call["name"] != "set_next_cell":
                            continue
                        result = results.get(call["call_id"]) or ""
                        try:
                            scheduled = json.loads(result).get("scheduled")
                        except (AttributeError, json.JSONDecodeError):
                            scheduled = False
                        if not scheduled:
                            observe(step, call["call_id"], result or "Not scheduled.")
                            continue
                        if chosen is not None:
                            observe(*chosen, "Not run: replaced by a later set_next_cell.")
                        chosen = (step, call["call_id"])
    if not steps:
        return None
    if chosen is not None:
        observe(*chosen, "Not run: the run ended first.")
    if running is not None:
        observe(*running, "No result: the run ended during this cell.")
    # Usage totals reach Harbor through populate_context_post_run.
    return Trajectory.model_validate(
        {"agent": {"name": "fibona", "version": "unknown", "model_name": model_name}, "steps": steps}
    )
