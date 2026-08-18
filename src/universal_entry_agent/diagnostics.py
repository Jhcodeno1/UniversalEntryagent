from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Any


_CURRENT_TRACE: ContextVar["PerformanceTrace | None"] = ContextVar(
    "universal_agent_performance_trace",
    default=None,
)
_WRITE_LOCK = threading.Lock()
_SENSITIVE_FRAGMENTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def _redact_text(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(r"\1***", result)
        else:
            result = pattern.sub("sk-***", result)
    return result


def _truncate(value: str, limit: int) -> str:
    normalized = " ".join(_redact_text(value).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)] + "..."


def _safe_value(value: Any, limit: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(value, limit)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if any(fragment in name.lower() for fragment in _SENSITIVE_FRAGMENTS):
                result[name] = "***"
            else:
                result[name] = _safe_value(item, limit)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, limit) for item in list(value)[:20]]
    return _truncate(str(value), limit)


class PerformanceTrace:
    """Structured timing trace shared by routing and the ReAct runtime."""

    def __init__(
        self,
        *,
        project_root: Path,
        session_id: str,
        query: str,
        settings: dict[str, Any] | None = None,
    ) -> None:
        options = settings or {}
        self.enabled = bool(options.get("enabled", True))
        self.console_enabled = bool(options.get("console", True))
        self.jsonl_enabled = bool(options.get("jsonl", True))
        self.include_query = bool(options.get("include_query", True))
        self.max_text_length = max(int(options.get("max_text_length", 300)), 40)
        self.slow_stage_ms = max(float(options.get("slow_stage_ms", 1000)), 0.0)
        self.trace_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.session_id = session_id
        self.query = query
        self.project_root = project_root
        self.started_at = time.perf_counter()
        self.started_wall = datetime.now().astimezone()
        self.sequence = 0
        self.events: list[dict[str, Any]] = []
        self.total_ms = 0.0
        self.status = "running"

    def event(
        self,
        stage: str,
        *,
        status: str = "ok",
        duration_ms: float | None = None,
        **details: Any,
    ) -> None:
        if not self.enabled:
            return
        self.sequence += 1
        elapsed_ms = (time.perf_counter() - self.started_at) * 1000
        safe_details: dict[str, Any] = {}
        for key, value in details.items():
            if value is None:
                continue
            name = str(key)
            if any(fragment in name.lower() for fragment in _SENSITIVE_FRAGMENTS):
                safe_details[name] = "***"
            else:
                safe_details[name] = _safe_value(value, self.max_text_length)
        record: dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "session_id": self.session_id,
            "stage": stage,
            "status": status,
            "elapsed_ms": round(elapsed_ms, 3),
        }
        if duration_ms is not None:
            record["duration_ms"] = round(duration_ms, 3)
            record["slow"] = duration_ms >= self.slow_stage_ms
        record.update(safe_details)
        self.events.append(record)
        self._emit(record)

    def finish(
        self,
        *,
        status: str,
        reply_chars: int = 0,
        error: str | None = None,
    ) -> None:
        self.total_ms = (time.perf_counter() - self.started_at) * 1000
        self.status = status
        self.event(
            "request.total",
            status=status,
            duration_ms=self.total_ms,
            reply_chars=reply_chars,
            error=error,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "status": self.status,
            "elapsed_ms": round(self.total_ms, 3),
            "log_file": str(self.log_file),
        }

    @property
    def log_file(self) -> Path:
        day = self.started_wall.date().isoformat()
        return self.project_root / "qwenpaw_runtime" / "diagnostics" / f"{day}.jsonl"

    def _emit(self, record: dict[str, Any]) -> None:
        with _WRITE_LOCK:
            if self.console_enabled:
                try:
                    duration = record.get("duration_ms")
                    duration_text = (
                        f" duration={duration:.1f}ms" if duration is not None else ""
                    )
                    slow_text = " SLOW" if record.get("slow") else ""
                    detail_keys = (
                        "operation",
                        "attempt",
                        "candidates",
                        "selected_intent",
                        "tool_name",
                        "target_agent",
                        "response_kind",
                        "error",
                    )
                    detail_text = " ".join(
                        f"{key}={record[key]}"
                        for key in detail_keys
                        if key in record and record[key] not in (None, "")
                    )
                    line = (
                        f"[PERF] trace={self.trace_id} seq={record['sequence']} "
                        f"+{record['elapsed_ms']:.1f}ms stage={record['stage']} "
                        f"status={record['status']}{duration_text}{slow_text}"
                    )
                    if detail_text:
                        line += " " + detail_text
                    print(line, file=sys.__stderr__, flush=True)
                except Exception:
                    pass

            if self.jsonl_enabled:
                try:
                    self.log_file.parent.mkdir(parents=True, exist_ok=True)
                    with self.log_file.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                except Exception:
                    pass


def create_trace(
    *,
    project_root: Path,
    session_id: str,
    query: str,
    settings: dict[str, Any] | None = None,
) -> PerformanceTrace:
    trace = PerformanceTrace(
        project_root=project_root,
        session_id=session_id,
        query=query,
        settings=settings,
    )
    trace.event(
        "request.start",
        query=query if trace.include_query else None,
        query_chars=len(query),
    )
    return trace


def set_current_trace(trace: PerformanceTrace) -> Token:
    return _CURRENT_TRACE.set(trace)


def reset_current_trace(token: Token) -> None:
    _CURRENT_TRACE.reset(token)


def get_current_trace() -> PerformanceTrace | None:
    return _CURRENT_TRACE.get()


def elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
