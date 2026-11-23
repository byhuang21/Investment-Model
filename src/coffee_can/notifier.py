"""Notification dispatcher skeleton (console, Markdown report, GitHub Issue, webhook)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class AlertMessage:
    """Structured alert emitted by radar, watchlist, earnings diff, or post-mortem scripts."""

    title: str
    severity: str  # "INFO" | "GREEN" | "YELLOW" | "RED" | "TRIGGER"
    body_markdown: str
    category: str  # "topic_radar" | "watchlist" | "earnings_diff" | "post_mortem"


class Notifier:
    """Routes alerts to configured output channels."""

    def __init__(self, config: dict[str, Any], reports_dir: Path) -> None:
        self.config = config.get("notifications", {})
        self.reports_dir = reports_dir

    def dispatch(self, alert: AlertMessage, dry_run: bool = False) -> Path | None:
        """Emit alert to enabled channels and optionally persist a Markdown report."""
        if self.config.get("console", {}).get("enabled", True):
            print(f"[{alert.severity}] {alert.title}\n{alert.body_markdown}\n")

        report_path: Path | None = None
        if not dry_run and self.config.get("markdown_report", {}).get("enabled", True):
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            report_path = self.reports_dir / f"{stamp}_{alert.category}.md"
            report_path.write_text(
                f"# [{alert.severity}] {alert.title}\n\n{alert.body_markdown}\n",
                encoding="utf-8",
            )

        # TODO(Phase 4): Implement GitHub Issue / Webhook / Telegram delivery when enabled.
        return report_path
