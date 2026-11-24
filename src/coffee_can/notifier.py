"""Multi-channel notification dispatcher (console, Markdown report, GitHub Issue, webhook, TG)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

EXTERNAL_ALERT_SEVERITIES = {"TRIGGER", "YELLOW", "RED"}


@dataclass
class AlertMessage:
    """Structured alert emitted by radar, watchlist, earnings diff, or post-mortem scripts."""

    title: str
    severity: str  # "INFO" | "GREEN" | "YELLOW" | "RED" | "TRIGGER"
    body_markdown: str
    category: str  # "topic_radar" | "watchlist" | "earnings_diff" | "post_mortem"


class Notifier:
    """Routes alerts to configured output channels safely without breaking pipeline runs."""

    def __init__(self, config: dict[str, Any], reports_dir: Path) -> None:
        self.config = config.get("notifications", {})
        self.reports_dir = reports_dir

    def dispatch(self, alert: AlertMessage, dry_run: bool = False) -> Path | None:
        """Emit alert to enabled channels and optionally persist a Markdown report."""
        if self.config.get("console", {}).get("enabled", True):
            print(f"[{alert.severity}] {alert.title}\n{alert.body_markdown}\n")

        if dry_run:
            return None

        report_path: Path | None = None
        if self.config.get("markdown_report", {}).get("enabled", True):
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            report_path = self.reports_dir / f"{stamp}_{alert.category}.md"
            report_path.write_text(
                f"# [{alert.severity}] {alert.title}\n\n{alert.body_markdown}\n",
                encoding="utf-8",
            )

        if alert.severity.upper() in EXTERNAL_ALERT_SEVERITIES:
            self._send_github_issue(alert)
            self._send_webhook(alert)
            self._send_telegram(alert)

        return report_path

    def _send_github_issue(self, alert: AlertMessage) -> bool:
        gh_cfg = self.config.get("github_issue", {})
        if not gh_cfg.get("enabled", False):
            return False
        token = os.getenv("GITHUB_TOKEN", "").strip()
        repo = os.getenv("GITHUB_REPOSITORY", "").strip()
        if not token or not repo:
            return False
        labels = list(gh_cfg.get("labels", ["coffee-can-alert"]))
        url = f"https://api.github.com/repos/{repo}/issues"
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "title": f"[{alert.severity}] {alert.title}",
                    "body": alert.body_markdown,
                    "labels": labels,
                },
                timeout=15,
            )
            return resp.status_code in {200, 201}
        except requests.RequestException:
            return False

    def _send_webhook(self, alert: AlertMessage) -> bool:
        wh_cfg = self.config.get("webhook", {})
        if not wh_cfg.get("enabled", False):
            return False
        url_env = str(wh_cfg.get("url_env", "ALERT_WEBHOOK_URL"))
        url = os.getenv(url_env, "").strip()
        if not url:
            return False
        text = f"[{alert.severity}] {alert.title}\n\n{alert.body_markdown}"
        try:
            resp = requests.post(
                url,
                json={
                    "title": alert.title,
                    "severity": alert.severity,
                    "category": alert.category,
                    "text": text,
                    "msg_type": "text",
                    "content": {"text": text},
                },
                timeout=15,
            )
            return 200 <= resp.status_code < 300
        except requests.RequestException:
            return False

    def _send_telegram(self, alert: AlertMessage) -> bool:
        tg_cfg = self.config.get("telegram", {})
        if not tg_cfg.get("enabled", False):
            return False
        token = os.getenv(str(tg_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")), "").strip()
        chat_id = os.getenv(str(tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID")), "").strip()
        if not token or not chat_id:
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": f"[{alert.severity}] {alert.title}\n\n{alert.body_markdown}",
                },
                timeout=15,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False
