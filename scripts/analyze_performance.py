from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


AGGREGATE_STAGES = {
    "request.total",
    "runner.total",
    "runner.react_execution",
    "react.runner.completed",
    "intent.route.completed",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _latest_log_file(log_dir: Path) -> Path:
    files = sorted(log_dir.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"没有性能日志：{log_dir}")
    return files[-1]


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError as exc:
                print(f"跳过损坏日志行 {line_number}: {exc}", file=sys.stderr)
                continue
            if isinstance(item, dict) and item.get("trace_id"):
                records.append(item)
    return records


def _group_traces(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["trace_id"])].append(record)
    for events in grouped.values():
        events.sort(key=lambda item: int(item.get("sequence") or 0))
    return dict(grouped)


def _trace_total(events: list[dict[str, Any]]) -> float:
    total = next(
        (item for item in reversed(events) if item.get("stage") == "request.total"),
        None,
    )
    if total is not None:
        return float(total.get("duration_ms") or total.get("elapsed_ms") or 0)
    return max((float(item.get("elapsed_ms") or 0) for item in events), default=0)


def _trace_query(events: list[dict[str, Any]]) -> str:
    start = next((item for item in events if item.get("stage") == "request.start"), {})
    return str(start.get("query") or "<未记录 query>")


def _trace_status(events: list[dict[str, Any]]) -> str:
    total = next(
        (item for item in reversed(events) if item.get("stage") == "request.total"),
        {},
    )
    return str(total.get("status") or events[-1].get("status") or "unknown")


def _details(record: dict[str, Any]) -> str:
    keys = (
        "operation",
        "model",
        "attempt",
        "max_attempts",
        "candidates",
        "decision",
        "selected_intent",
        "tool_name",
        "target_agent",
        "route_status",
        "response_kind",
        "will_retry",
        "backoff_seconds",
        "error",
    )
    parts = [
        f"{key}={record[key]}"
        for key in keys
        if record.get(key) not in (None, "", [], {})
    ]
    return " ".join(parts)


def _print_trace(trace_id: str, events: list[dict[str, Any]], slowest: int) -> None:
    total_ms = _trace_total(events)
    print(f"Trace ID : {trace_id}")
    print(f"Session  : {events[0].get('session_id', '')}")
    print(f"Status   : {_trace_status(events)}")
    print(f"Total    : {total_ms / 1000:.3f}s")
    print(f"Query    : {_trace_query(events)}")
    print("\n执行路径：")
    for record in events:
        sequence = int(record.get("sequence") or 0)
        elapsed = float(record.get("elapsed_ms") or 0)
        duration = record.get("duration_ms")
        duration_text = (
            f"耗时={float(duration):9.1f}ms" if duration is not None else " " * 18
        )
        slow = " [SLOW]" if record.get("slow") else ""
        detail = _details(record)
        print(
            f"  {sequence:03d} +{elapsed:10.1f}ms {duration_text} "
            f"{record.get('stage', '')} {record.get('status', '')}{slow} {detail}".rstrip(),
        )

    leaf_events = [
        item
        for item in events
        if item.get("duration_ms") is not None
        and item.get("stage") not in AGGREGATE_STAGES
    ]
    leaf_events.sort(key=lambda item: float(item.get("duration_ms") or 0), reverse=True)
    print(f"\n最慢的 {min(slowest, len(leaf_events))} 个非汇总阶段：")
    for index, record in enumerate(leaf_events[:slowest], start=1):
        detail = _details(record)
        print(
            f"  {index:02d}. {float(record['duration_ms']) / 1000:9.3f}s "
            f"{record.get('stage', '')} {detail}".rstrip(),
        )


def _print_trace_list(
    grouped: dict[str, list[dict[str, Any]]],
    count: int,
) -> None:
    traces = sorted(
        grouped.items(),
        key=lambda item: max(
            (str(event.get("timestamp") or "") for event in item[1]),
            default="",
        ),
        reverse=True,
    )[:count]
    print(f"最近 {len(traces)} 次请求：")
    for trace_id, events in traces:
        print(
            f"  {trace_id}  {_trace_total(events) / 1000:9.3f}s  "
            f"{_trace_status(events):7s}  {_trace_query(events)}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="分析意图路由 Agent 性能诊断日志")
    parser.add_argument("--file", type=Path, help="指定 diagnostics JSONL 文件")
    parser.add_argument("--trace-id", help="分析指定请求；默认分析最近一次请求")
    parser.add_argument("--list", type=int, default=0, help="列出最近 N 次请求")
    parser.add_argument("--slowest", type=int, default=10, help="显示最慢阶段数量")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    log_dir = _project_root() / "qwenpaw_runtime" / "diagnostics"
    path = args.file.resolve() if args.file else _latest_log_file(log_dir)
    grouped = _group_traces(_load_records(path))
    if not grouped:
        raise SystemExit(f"日志中没有有效请求：{path}")

    print(f"Log file : {path}\n")
    if args.list > 0:
        _print_trace_list(grouped, args.list)
        return 0

    trace_id = args.trace_id
    if trace_id:
        events = grouped.get(trace_id)
        if events is None:
            raise SystemExit(f"当前日志文件中找不到 trace_id：{trace_id}")
    else:
        trace_id, events = max(
            grouped.items(),
            key=lambda item: max(
                (str(event.get("timestamp") or "") for event in item[1]),
                default="",
            ),
        )
    _print_trace(trace_id, events, max(args.slowest, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
