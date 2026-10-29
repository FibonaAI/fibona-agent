"""Read-only ATIF projection of the REPL agent's native session records."""

import base64
import json
from datetime import datetime
from pathlib import Path

from harbor.models.trajectories import Trajectory
from plugins.utils import _text


def _arguments(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": str(raw)}
    return value if isinstance(value, dict) else {"value": value}


def _cell_result(record):
    return "\n".join(
        part
        for part in [record.get("output") or "", "Error:\n" + record["error"] if record.get("error") else ""]
        if part
    )


def _timing(start, end=None):
    timing = {"started_at": start.get("time"), "finished_at": (end or {}).get("time"), "duration_seconds": None}
    try:
        seconds = (
            datetime.fromisoformat(timing["finished_at"]) - datetime.fromisoformat(timing["started_at"])
        ).total_seconds()
        if seconds >= 0:
            timing["duration_seconds"] = seconds
    except (TypeError, ValueError):
        pass
    return timing


def convert(logs_dir: Path, model_name: str | None = None) -> Trajectory | None:
    path = logs_dir / "session.jsonl"
    if not path.is_file():
        return None
    steps, calls, cells, requests = [], {}, {}, {}
    session_id = None
    records, unreadable = [], []
    cell_details = {}

    def add(record, source, message, **fields):
        step = dict(step_id=len(steps) + 1, timestamp=record.get("time"), source=source, message=message, **fields)
        steps.append(step)
        return step

    def observe(target, content):
        step, call_id = target
        step.setdefault("observation", {"results": []})["results"].append(
            {"source_call_id": call_id, "content": content}
        )

    with path.open("rb") as source:
        for line_number, line in enumerate(source, 1):
            try:
                record = json.loads(line)
            except ValueError:
                unreadable.append({"line": line_number, "base64": base64.b64encode(line).decode("ascii")})
                add({}, "system", "Unreadable session record; some trace evidence is missing.")
                continue
            records.append(record)
            if not isinstance(record, dict) or "type" not in record:
                add({}, "system", "Unrecognized session record: " + json.dumps(record, ensure_ascii=False))
                continue
            kind = record["type"]
            if kind == "session":
                session_id = record.get("session_id")
            elif kind == "source":
                continue  # Full snapshots remain in native_session_records, outside the action timeline.
            elif kind == "message":
                message = record["message"]
                role = message.get("role")
                add(record, role if role in {"user", "system", "agent"} else "system", _text(message.get("content")))
            elif kind == "model_request":
                requests[record["id"]] = record
            elif kind == "model_response":
                request = requests.pop(record.get("request_id"), {})
                raw_output = record.get("output")
                output = [item for item in raw_output if isinstance(item, dict)] if isinstance(raw_output, list) else []
                step = add(
                    record,
                    "agent",
                    "\n\n".join(
                        "\n".join(p.get("text") or p.get("refusal") or "" for p in o.get("content", []))
                        for o in output
                        if o.get("type") == "message"
                    ),
                    model_name=record.get("response_model") or record.get("model") or model_name,
                    extra={
                        "record_id": record["id"],
                        "request_id": record.get("request_id"),
                        "status": record.get("status"),
                        "accepted": record.get("accepted"),
                        "request": request,
                    },
                )
                if step["message"].strip() and (record.get("status") != "completed" or record.get("accepted") is False):
                    step["message"] = "未接受的模型文本(诊断)\n" + step["message"]
                timing = _timing(request, record)
                step["extra"]["timing"] = timing
                reasoning = "\n\n".join(
                    _text(o.get("summary")) or _text(o.get("content")) for o in output if o.get("type") == "reasoning"
                )
                if reasoning:
                    step["reasoning_content"] = reasoning
                usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
                metrics = {
                    "prompt_tokens": usage.get("input_tokens"),
                    "completion_tokens": usage.get("output_tokens"),
                    "cached_tokens": (
                        usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
                    ).get("cached_tokens"),
                    "cost_usd": usage.get("cost"),
                }
                if any(value is not None for value in metrics.values()):
                    step["metrics"] = {key: value for key, value in metrics.items() if value is not None}
                if record.get("status") != "completed" or record.get("accepted") is False:
                    step["extra"]["unaccepted_output"] = json.dumps(raw_output, ensure_ascii=False)
                    step["extra"]["error"] = record.get("error")
                    step["extra"]["incomplete_details"] = record.get("incomplete_details")
                    continue
                tool_calls = [o for o in output if o.get("type") == "function_call"]
                if tool_calls:
                    step["tool_calls"] = [
                        {
                            "tool_call_id": o["call_id"],
                            "function_name": o["name"],
                            "arguments": _arguments(o.get("arguments")),
                        }
                        for o in tool_calls
                    ]
                    for call in tool_calls:
                        calls[call["call_id"]] = (step, call["call_id"])
            elif kind == "cell_start":
                target = calls.get(record.get("call_id"))
                if target is None and record.get("parent_cell_id") in cells:
                    target = cells[record["parent_cell_id"]][0]
                if target is None:
                    target = (
                        add(
                            record,
                            "system",
                            record.get("code") or "",
                            extra={
                                "record_id": record["id"],
                                "parent_cell_id": record.get("parent_cell_id"),
                                "code": record.get("code") or "",
                            },
                        ),
                        None,
                    )
                target[0].setdefault("extra", {}).setdefault("cells", []).append(
                    {
                        "cell_id": record["id"],
                        "call_id": record.get("call_id"),
                        "parent_cell_id": record.get("parent_cell_id"),
                    }
                )
                detail = target[0]["extra"]["cells"][-1]
                detail.update(
                    code=record.get("code"), timeout=record.get("timeout"), status="unfinished", timing=_timing(record)
                )
                cell_details[record["id"]] = (record, detail)
                cells[record["id"]] = (target, record.get("call_id"))
            elif kind == "turn_end":
                add(record, "agent", record["message"], extra={"record": record, "explicit_turn_end": True})
            elif kind == "display":
                cell_id = record.get("cell_id")
                owner = cells.get(cell_id)
                if owner is not None:
                    step = owner[0][0]
                    step.setdefault("extra", {}).setdefault("display", []).append(record)
                    step["message"] = "\n\n".join(
                        part for part in [step["message"], f"display · cell {cell_id}\n{record['text']}"] if part
                    )
                else:
                    add(
                        record,
                        "system",
                        "display\n" + record["text"],
                        extra={"record_id": record["id"], "cell_id": cell_id, "audience": "user"},
                    )
            elif kind == "cell_end":
                cell = cells.pop(record.get("cell_id"), None)
                if cell is not None:
                    target, call_id = cell
                    start, detail = cell_details[record["cell_id"]]
                    detail.update(
                        status=record.get("status", "unknown"),
                        timing=_timing(start, record),
                        end_record_id=record["id"],
                        output=record.get("output", ""),
                        error=record.get("error"),
                    )
                    content = _cell_result(record)
                    if start.get("parent_cell_id") is not None and not start.get("call_id"):
                        content = f"嵌套 cell {record['cell_id']} · {detail['status']}\n\n源码:\n{start.get('code', '')}\n\n输出:\n{content}"
                    observe(target, content)
                    calls.pop(call_id, None)
                else:
                    add(record, "system", "Unmatched cell result: " + json.dumps(record, ensure_ascii=False))
            elif kind == "tool_error":
                target = calls.pop(record.get("call_id"), None)
                if target is not None:
                    observe(target, _cell_result(record))
                else:
                    add(record, "system", "Unmatched tool error: " + json.dumps(record, ensure_ascii=False))
            elif kind in {
                "model_request_error",
                "mode",
                "model_retry",
                "context_reset",
                "recovery_error",
                "model_stop",
            }:
                if kind == "mode" and record.get("name") == "normal" and not record.get("recovery"):
                    continue
                labels = {
                    "model_request_error": "模型请求失败",
                    "mode": "恢复模式切换",
                    "model_retry": "重试模型请求",
                    "context_reset": "重置上下文",
                    "recovery_error": "恢复失败",
                    "model_stop": "模型停止",
                }
                detail = (
                    record.get("error")
                    or record.get("reason")
                    or record.get("handoff")
                    or record.get("recovery_error")
                    or record.get("status")
                    or ""
                )
                step = add(
                    record, "system", labels[kind] + (": " + str(detail) if detail else ""), extra={"record": record}
                )
                if kind == "model_request_error":
                    request = requests.pop(record.get("request_id"), {})
                    timing = _timing(request, record)
                    step["extra"] = {"request": request, "timing": timing}
            else:
                add(record, "system", kind + ": " + json.dumps(record, ensure_ascii=False))
    for target, call_id in cells.values():
        observe(target, "No result: the run ended during this cell; effects unknown.")
        calls.pop(call_id, None)
    for target in calls.values():
        observe(target, "No result: no cell execution or dispatch result was recorded.")
    for record in requests.values():
        add(record, "system", "Model request has no recorded outcome; provider usage is unknown.")
    if not steps:
        return None
    return Trajectory.model_validate(
        {
            "session_id": session_id,
            "agent": {"name": "repl-agent", "version": "unknown", "model_name": model_name},
            "steps": steps,
            "extra": {"native_session_records": records, "unreadable_records": unreadable},
        }
    )
