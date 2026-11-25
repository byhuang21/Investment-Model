#!/usr/bin/env python3
"""Periodic System Health Audit CLI.

Checks:
1. Local repository & AGENT_RULES.md integrity (`doctor.py`)
2. Static web snapshot validity & freshness (`web/data/snapshot.json`)
3. Live GitHub Pages HTTP 200 & JSON payload check (when --pages-url is given)
4. Latest GitHub Actions workflow run conclusions & open bot PR backlog
5. Optionally manages a deduplicated `[HEALTH AUDIT]` GitHub Issue on failures
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.config import load_environment
from coffee_can.health_audit import HealthAuditResult, run_full_health_audit

AUDIT_ISSUE_TITLE = "[HEALTH AUDIT] Coffee Can System Issues Detected"


def _sync_audit_issue(repo: str, token: str, result: HealthAuditResult) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/issues?state=open&per_page=30",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        open_issues = [
            i
            for i in resp.json()
            if "pull_request" not in i and i.get("title") == AUDIT_ISSUE_TITLE
        ]
    except Exception:
        return

    body = result.to_markdown()
    if not result.ok:
        if open_issues:
            num = open_issues[0]["number"]
            requests.post(
                f"https://api.github.com/repos/{repo}/issues/{num}/comments",
                headers=headers,
                json={"body": f"Periodic re-check still failing:\n\n{body}"},
                timeout=15,
            )
        else:
            requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers=headers,
                json={
                    "title": AUDIT_ISSUE_TITLE,
                    "body": body,
                    "labels": ["coffee-can-alert"],
                },
                timeout=15,
            )
    elif open_issues:
        # Auto-close resolved audit issue!
        for issue in open_issues:
            num = issue["number"]
            requests.post(
                f"https://api.github.com/repos/{repo}/issues/{num}/comments",
                headers=headers,
                json={"body": f"All checks recovered — auto-closing.\n\n{body}"},
                timeout=15,
            )
            requests.patch(
                f"https://api.github.com/repos/{repo}/issues/{num}",
                headers=headers,
                json={"state": "closed"},
                timeout=15,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Coffee Can periodic health audit.")
    parser.add_argument(
        "--pages-url",
        type=str,
        default=None,
        help="Optional GitHub Pages URL to probe (e.g. https://byhuang21.github.io/Investment-Model/).",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="GitHub owner/repo (defaults to GITHUB_REPOSITORY env var).",
    )
    parser.add_argument(
        "--sync-issue",
        action="store_true",
        help="Open/update/auto-close a deduplicated GitHub Issue on audit failure/recovery.",
    )
    args = parser.parse_args(argv)
    load_environment(ROOT_DIR)

    repo = (args.repo or os.getenv("GITHUB_REPOSITORY", "")).strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    pages_url = args.pages_url
    if not pages_url and repo and "/" in repo:
        owner, name = repo.split("/", 1)
        pages_url = f"https://{owner}.github.io/{name}/"

    result = run_full_health_audit(
        root=ROOT_DIR,
        pages_url=pages_url,
        repo=repo or None,
        github_token=token or None,
    )
    print(result.to_markdown())

    if args.sync_issue and repo and token:
        _sync_audit_issue(repo, token, result)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
