"""Unit tests for the Periodic Health Audit engine."""

from __future__ import annotations

from pathlib import Path

from coffee_can.health_audit import (
    AuditItem,
    HealthAuditResult,
    check_local_doctor,
    check_static_snapshot,
)


def test_local_doctor_and_static_snapshot_audit_pass() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    doc_item = check_local_doctor(repo_root)
    assert doc_item.status in {"OK", "WARN"}

    snap_item = check_static_snapshot(repo_root, max_age_days=30)
    assert snap_item.status == "OK"


def test_health_audit_markdown_summarizes_failures_and_passes() -> None:
    res = HealthAuditResult(
        checked_at="2026-11-25 01:00:00 UTC",
        items=[
            AuditItem("repo.doctor", "OK", "16 checks passed"),
            AuditItem("pages.live_http", "FAIL", "HTTP 404"),
        ],
    )
    assert res.ok is False
    assert len(res.failures) == 1
    md = res.to_markdown()
    assert "ISSUES DETECTED" in md
    assert "pages.live_http" in md
