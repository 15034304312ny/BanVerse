"""Privacy-preserving local diagnostics for BanVerse workflows.

The recorder deliberately accepts only operational labels, bounded numeric
measurements and a small allowlist of metadata.  Prompts, replies, media,
credentials, URLs and database identifiers have no field in the event schema.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

DIAGNOSTIC_SCHEMA_VERSION = 1
_EVENT_FILE_NAME = "events.jsonl"
_SAFE_DETAIL_KEYS = frozenset(
    {
        "call_count",
        "conflicts",
        "has_image",
        "history_turns",
        "input_characters",
        "output_characters",
        "pulled",
        "pushed",
        "queue_size",
        "segment_count",
        "updates_role_state",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:sk|ms)-[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|password|cookie|authorization)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:\b[A-Z]:[\\/]|/(?:home|users|data|storage|sdcard)/)"
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_TERMINAL_STAGES = frozenset(
    {
        "cycle_completed",
        "delivery_completed",
        "job_completed",
        "model_completed",
        "request_completed",
        "result_persisted",
    }
)


def redact_diagnostic_text(value: object, *, maximum: int = 160) -> str:
    """Return a bounded label with common secrets and private paths removed."""

    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = _EMAIL.sub("[redacted]", text)
    if _ABSOLUTE_PATH.search(text):
        return "[redacted-path]"
    return text[:maximum]


def _safe_number(value: object) -> int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(-1_000_000_000, min(value, 1_000_000_000))
    if isinstance(value, float) and math.isfinite(value):
        return round(max(-1_000_000_000.0, min(value, 1_000_000_000.0)), 3)
    return None


def _safe_details(details: Mapping[str, object] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (details or {}).items():
        normalized_key = redact_diagnostic_text(key, maximum=64)
        if normalized_key not in _SAFE_DETAIL_KEYS:
            continue
        safe_value = _safe_number(value)
        if safe_value is not None:
            result[normalized_key] = safe_value
    return result


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return round(ordered[min(index, len(ordered) - 1)], 1)


class DiagnosticRecorder:
    """Append bounded diagnostic events and export an explicitly safe package."""

    def __init__(
        self,
        root: str | Path,
        *,
        app_version: str = "",
        platform_name: str = "",
        max_bytes: int = 2_000_000,
        backups: int = 2,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / _EVENT_FILE_NAME
        self.app_version = redact_diagnostic_text(app_version, maximum=40)
        self.platform_name = redact_diagnostic_text(platform_name, maximum=40)
        self.max_bytes = max(32_768, int(max_bytes))
        self.backups = max(0, min(int(backups), 5))
        self.session_id = uuid4().hex[:12]
        self._lock = threading.RLock()

    def new_task_id(self, prefix: str = "task") -> str:
        safe_prefix = re.sub(
            r"[^a-z0-9_-]+", "-", str(prefix).strip().lower()
        ).strip("-")[:24]
        return f"{safe_prefix or 'task'}-{uuid4().hex[:12]}"

    def reference(self, value: object, *, prefix: str = "ref") -> str:
        """Create a session-scoped one-way reference without storing a raw ID."""

        source = str(value or "").strip()
        if not source:
            return ""
        digest = hashlib.sha256(
            f"{self.session_id}\0{source}".encode()
        ).hexdigest()[:16]
        safe_prefix = re.sub(
            r"[^a-z0-9_-]+", "-", str(prefix).strip().lower()
        ).strip("-")[:16]
        return f"{safe_prefix or 'ref'}-{digest}"

    def record(
        self,
        operation: str,
        stage: str,
        *,
        outcome: str = "ok",
        duration_ms: float | None = None,
        error_code: str = "",
        provider: str = "",
        model: str = "",
        request_kind: str = "",
        task_id: str = "",
        source_ref: str = "",
        details: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "schema": DIAGNOSTIC_SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "task_id": redact_diagnostic_text(task_id, maximum=48),
            "operation": redact_diagnostic_text(operation, maximum=64),
            "stage": redact_diagnostic_text(stage, maximum=64),
            "outcome": redact_diagnostic_text(outcome, maximum=24),
        }
        if source_ref:
            event["source_ref"] = redact_diagnostic_text(
                source_ref, maximum=40
            )
        for key, value, maximum in (
            ("error_code", error_code, 80),
            ("provider", provider, 80),
            ("model", model, 160),
            ("request_kind", request_kind, 40),
        ):
            safe_value = redact_diagnostic_text(value, maximum=maximum)
            if safe_value:
                event[key] = safe_value
        if duration_ms is not None and math.isfinite(float(duration_ms)):
            event["duration_ms"] = round(
                max(0.0, min(float(duration_ms), 86_400_000.0)), 1
            )
        safe_details = _safe_details(details)
        if safe_details:
            event["details"] = safe_details
        encoded = json.dumps(
            event, ensure_ascii=False, separators=(",", ":")
        ) + "\n"
        with self._lock:
            self._rotate_if_needed(len(encoded.encode("utf-8")))
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
        return event

    def span(
        self,
        operation: str,
        stage: str,
        **labels: object,
    ) -> DiagnosticSpan:
        return DiagnosticSpan(self, operation, stage, labels)

    def events(self, *, limit: int = 5_000) -> list[dict[str, Any]]:
        paths = [
            self.path.with_name(f"{self.path.name}.{index}")
            for index in range(self.backups, 0, -1)
        ]
        paths.append(self.path)
        collected: list[dict[str, Any]] = []
        with self._lock:
            for path in paths:
                if not path.is_file():
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line in lines:
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(event, dict) and event.get("schema") == 1:
                        collected.append(event)
        return collected[-max(1, min(int(limit), 20_000)) :]

    def summary(self) -> dict[str, Any]:
        events = self.events()
        operation_counts = Counter(
            str(event.get("operation", "unknown")) for event in events
        )
        outcome_counts = Counter(
            str(event.get("outcome", "unknown")) for event in events
        )
        error_counts = Counter(
            str(event.get("error_code", ""))
            for event in events
            if event.get("error_code")
        )
        durations: dict[str, list[float]] = defaultdict(list)
        for event in events:
            duration = event.get("duration_ms")
            if isinstance(duration, (int, float)):
                key = f"{event.get('operation', 'unknown')}.{event.get('stage', 'unknown')}"
                durations[key].append(float(duration))
        latency = {
            key: {
                "samples": len(values),
                "p50_ms": _percentile(values, 50),
                "p95_ms": _percentile(values, 95),
            }
            for key, values in sorted(durations.items())
        }
        terminal_tasks: dict[tuple[str, str], str] = {}
        for event in events:
            task_id = str(event.get("task_id", ""))
            stage = str(event.get("stage", ""))
            if task_id and stage in _TERMINAL_STAGES:
                terminal_tasks[(str(event.get("operation", "unknown")), task_id)] = str(
                    event.get("outcome", "unknown")
                )
        task_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
        for (operation, _task_id), outcome in terminal_tasks.items():
            task_outcomes[operation][outcome] += 1
        rates = {}
        for operation, counts in sorted(task_outcomes.items()):
            total = sum(counts.values())
            rates[operation] = {
                "completed_tasks": total,
                "failure_rate": round(counts["error"] / total, 4),
                "cancellation_rate": round(counts["cancelled"] / total, 4),
            }
        return {
            "schema": DIAGNOSTIC_SCHEMA_VERSION,
            "app_version": self.app_version,
            "platform": self.platform_name,
            "event_count": len(events),
            "first_event_at": events[0].get("timestamp", "") if events else "",
            "last_event_at": events[-1].get("timestamp", "") if events else "",
            "operations": dict(sorted(operation_counts.items())),
            "outcomes": dict(sorted(outcome_counts.items())),
            "errors": dict(sorted(error_counts.items())),
            "latency": latency,
            "rates": rates,
        }

    def export(self, destination: str | Path) -> Path:
        """Write a zip containing only the safe event stream and its summary."""

        target = Path(destination)
        if target.suffix.lower() != ".zip":
            target = target.with_suffix(".zip")
        target.parent.mkdir(parents=True, exist_ok=True)
        events = self.events()
        manifest = {
            "format": "BanVerse privacy-safe diagnostics",
            "schema": DIAGNOSTIC_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": self.summary(),
            "included": [
                "operation and stage labels",
                "provider/model identifiers",
                "bounded timings and counts",
                "sanitized error codes",
            ],
            "excluded": [
                "API keys, tokens, passwords and cookies",
                "prompts, replies, character cards and user profiles",
                "raw conversation/turn/account/device identifiers",
                "images, audio, database and startup logs",
            ],
        }
        event_text = "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in events
        )
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                archive.writestr("events.jsonl", event_text)
                archive.writestr(
                    "README.txt",
                    "此诊断包由用户主动导出。它不包含聊天正文、角色卡、媒体、数据库、"
                    "启动日志、账户标识或任何凭据。\n",
                )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_size = self.path.stat().st_size
        except OSError:
            current_size = 0
        if current_size + incoming_bytes <= self.max_bytes:
            return
        if self.backups <= 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        if self.path.exists():
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))


class DiagnosticSpan:
    """Small context/span helper that never suppresses workflow exceptions."""

    def __init__(
        self,
        recorder: DiagnosticRecorder,
        operation: str,
        stage: str,
        labels: Mapping[str, object],
    ) -> None:
        self._recorder = recorder
        self._operation = operation
        self._stage = stage
        self._labels = dict(labels)
        self._started = monotonic()
        self._finished = False

    def finish(
        self,
        *,
        outcome: str = "ok",
        error_code: str = "",
        details: Mapping[str, object] | None = None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        self._recorder.record(
            self._operation,
            self._stage,
            outcome=outcome,
            duration_ms=(monotonic() - self._started) * 1000,
            error_code=error_code,
            details=details,
            **self._labels,
        )

    def __enter__(self) -> DiagnosticSpan:
        return self

    def __exit__(self, exception_type, _exception, _traceback) -> bool:
        self.finish(
            outcome="error" if exception_type is not None else "ok",
            error_code="unhandled_exception" if exception_type is not None else "",
        )
        return False
