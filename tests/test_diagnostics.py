from __future__ import annotations

import json
from zipfile import ZipFile

from deepseek_cli.diagnostics import DiagnosticRecorder, redact_diagnostic_text


def test_recorder_keeps_only_safe_operational_fields(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diagnostics", app_version="1.4.0")
    event = recorder.record(
        "text_chat",
        "model_completed",
        outcome="error",
        duration_ms=123.456,
        error_code="token=secret-value",
        provider="deepseek",
        model="deepseek-chat",
        request_kind="user",
        task_id=recorder.new_task_id("chat"),
        details={
            "history_turns": 12,
            "has_image": False,
            "prompt": "这段用户消息绝不能进入诊断",
        },
    )

    assert event["duration_ms"] == 123.5
    assert event["details"] == {"history_turns": 12, "has_image": False}
    serialized = recorder.path.read_text(encoding="utf-8")
    assert "这段用户消息绝不能进入诊断" not in serialized
    assert "secret-value" not in serialized
    assert "[redacted]" in serialized


def test_summary_reports_p50_p95_outcomes_and_errors(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    for duration in (10, 20, 30, 40, 100):
        recorder.record(
            "text_chat",
            "model_completed",
            duration_ms=duration,
        )
    recorder.record(
        "text_chat",
        "model_completed",
        outcome="error",
        error_code="timeout",
    )

    summary = recorder.summary()

    latency = summary["latency"]["text_chat.model_completed"]
    assert latency == {"samples": 5, "p50_ms": 30.0, "p95_ms": 100.0}
    assert summary["operations"] == {"text_chat": 6}
    assert summary["errors"] == {"timeout": 1}


def test_summary_reports_task_failure_and_cancellation_rates(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    outcomes = ("ok", "ok", "error", "cancelled")
    for index, outcome in enumerate(outcomes):
        recorder.record(
            "text_chat",
            "delivery_completed" if outcome == "ok" else "model_completed",
            outcome=outcome,
            task_id=f"chat-{index}",
        )

    assert recorder.summary()["rates"]["text_chat"] == {
        "completed_tasks": 4,
        "failure_rate": 0.25,
        "cancellation_rate": 0.25,
    }


def test_export_contains_no_secret_content_or_local_logs(tmp_path):
    recorder = DiagnosticRecorder(
        tmp_path / "diagnostics",
        app_version="1.4.0",
        platform_name="win32",
    )
    recorder.record(
        "image_generation",
        "completed",
        provider="siliconflow",
        model="sk-this-must-be-redacted",
        details={"output_characters": 42},
    )
    output = recorder.export(tmp_path / "BanVerse-diagnostics")

    assert output.suffix == ".zip"
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "events.jsonl",
            "manifest.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
    assert manifest["summary"]["event_count"] == 1
    assert b"this-must-be-redacted" not in combined
    assert "startup.log" not in archive.namelist()


def test_redaction_removes_credentials_email_and_private_paths():
    assert "secret" not in redact_diagnostic_text("Bearer secret")
    assert redact_diagnostic_text("developer@example.com") == "[redacted]"
    assert redact_diagnostic_text(r"C:\\Users\\Alice\\chat.db") == "[redacted-path]"


def test_session_reference_correlates_without_exposing_raw_identifier(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    reference = recorder.reference("turn-real-private-id", prefix="turn")
    event = recorder.record(
        "summary_role_state",
        "request_started",
        source_ref=reference,
    )

    assert reference.startswith("turn-")
    assert reference == recorder.reference("turn-real-private-id", prefix="turn")
    assert event["source_ref"] == reference
    assert "turn-real-private-id" not in recorder.path.read_text(encoding="utf-8")
