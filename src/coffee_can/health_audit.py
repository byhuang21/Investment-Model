"""Periodic System Health Audit Engine.

Audits the entire Coffee Can operational stack:
1. Local repository integrity via `run_doctor` (config/, memory/, AGENT_RULES.md)
2. Static web snapshot validity & staleness (`web/data/snapshot.json`)
3. Live GitHub Pages reachability (`index.html` + `data/snapshot.json` HTTP 200)
4. Recent GitHub Actions workflow conclusions (catches any silent cron failures)
5. Open bot PR backlog & quarantine backlog visibility
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import requests

from coffee_can.doctor import run_doctor


@dataclass
class AuditItem:
    name: str
    status: str  # "OK" | "WARN" | "FAIL"
    detail: str


@dataclass
class HealthAuditResult:
    checked_at: str
    items: list[AuditItem] = field(default_factory=list)

    @property
    def failures(self) -> list[AuditItem]:
        return [i for i in self.items if i.status == "FAIL"]

    @property
    def warnings(self) -> list[AuditItem]:
        return [i for i in self.items if i.status == "WARN"]

    @property
    def ok(self) -> bool:
        return len(self.failures) == 0

    def to_markdown(self) -> str:
        verdict = "ALL SYSTEMS GREEN" if self.ok else f"ISSUES DETECTED ({len(self.failures)} FAIL)"
        lines = [
            f"- **巡检时间 (UTC)**：`{self.checked_at}`",
            f"- **总判定**：`{verdict}`"
            f"（FAIL: `{len(self.failures)}` | WARN: `{len(self.warnings)}`）",
            "",
            "| 检查项 | 状态 | 详情 |",
            "|---|---|---|",
        ]
        for item in self.items:
            icon = "✅" if item.status == "OK" else "⚠️" if item.status == "WARN" else "❌"
            lines.append(f"| `{item.name}` | {icon} {item.status} | {item.detail} |")
        return "\n".join(lines)


def check_local_doctor(root: Path) -> AuditItem:
    rep = run_doctor(root)
    if not rep.ok:
        err_str = "; ".join(f"{e.scope}: {e.message}" for e in rep.errors[:5])
        return AuditItem("repo.doctor", "FAIL", err_str)
    if rep.warnings:
        warn_str = "; ".join(f"{w.scope}: {w.message}" for w in rep.warnings[:3])
        return AuditItem("repo.doctor", "WARN", f"{rep.checks_passed} checks passed ({warn_str})")
    return AuditItem("repo.doctor", "OK", f"{rep.checks_passed} checks passed, 0 warnings")


def check_static_snapshot(root: Path, max_age_days: int = 10) -> AuditItem:
    snap_path = root / "web" / "data" / "snapshot.json"
    if not snap_path.exists():
        return AuditItem("web.snapshot_file", "FAIL", "Missing web/data/snapshot.json")
    try:
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        gen_raw = str(data.get("generated_at", ""))
        gen_dt = datetime.fromisoformat(gen_raw.replace("Z", "+00:00"))
        age_days = (datetime.now(UTC) - gen_dt).total_seconds() / 86400.0
    except Exception as exc:
        return AuditItem("web.snapshot_file", "FAIL", f"Invalid snapshot JSON: {exc}")

    if age_days > max_age_days:
        return AuditItem(
            "web.snapshot_file",
            "WARN",
            f"Snapshot is {age_days:.1f}d old (> {max_age_days}d threshold)",
        )
    return AuditItem(
        "web.snapshot_file",
        "OK",
        f"Valid JSON, age {age_days:.1f}d, "
        f"industries={len(data.get('industries', []))}, "
        f"research={len(data.get('research', []))}",
    )


def check_pages_endpoint(pages_url: str, timeout: int = 15) -> AuditItem:
    base = pages_url.rstrip("/")
    try:
        r_home = requests.get(f"{base}/", timeout=timeout)
        if r_home.status_code != 200:
            return AuditItem(
                "pages.live_http", "FAIL", f"GET {base}/ returned {r_home.status_code}"
            )
        r_snap = requests.get(f"{base}/data/snapshot.json", timeout=timeout)
        if r_snap.status_code != 200:
            return AuditItem(
                "pages.live_http",
                "FAIL",
                f"GET {base}/data/snapshot.json returned {r_snap.status_code}",
            )
        payload = r_snap.json()
        if "industries" not in payload or "research" not in payload:
            return AuditItem("pages.live_http", "FAIL", "Live snapshot.json missing expected keys")
        return AuditItem(
            "pages.live_http",
            "OK",
            f"{base}/ -> HTTP 200, live snapshot v{payload.get('rules_version')}",
        )
    except Exception as exc:
        return AuditItem("pages.live_http", "FAIL", f"Reachability check error: {exc}")


def check_github_workflows(
    repo: str,
    token: str,
    ignore_workflows: set[str] | None = None,
    timeout: int = 15,
) -> list[AuditItem]:
    """Check latest completed run of each active GitHub Actions workflow."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    ignored = ignore_workflows or {"Periodic Health Audit"}
    items: list[AuditItem] = []

    try:
        wf_resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows",
            headers=headers,
            timeout=timeout,
        )
        wf_resp.raise_for_status()
        workflows = wf_resp.json().get("workflows", [])
    except Exception as exc:
        return [AuditItem("github.workflows", "FAIL", f"Failed to list workflows: {exc}")]

    for wf in workflows:
        name = str(wf.get("name", ""))
        if wf.get("state") != "active" or name in ignored:
            continue
        wf_id = wf["id"]
        try:
            runs_resp = requests.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{wf_id}/runs?per_page=5",
                headers=headers,
                timeout=timeout,
            )
            runs_resp.raise_for_status()
            runs = runs_resp.json().get("workflow_runs", [])
        except Exception as exc:
            items.append(AuditItem(f"wf.{name}", "FAIL", f"Failed to read runs: {exc}"))
            continue

        completed = [r for r in runs if r.get("status") == "completed"]
        if not completed:
            items.append(AuditItem(f"wf.{name}", "WARN", "No completed runs yet"))
            continue

        latest = completed[0]
        concl = str(latest.get("conclusion", "unknown"))
        run_url = latest.get("html_url", "")
        created = str(latest.get("created_at", ""))[:10]
        if concl == "success":
            items.append(AuditItem(f"wf.{name}", "OK", f"Latest run ({created}) succeeded"))
        else:
            items.append(
                AuditItem(
                    f"wf.{name}",
                    "FAIL",
                    f"Latest run ({created}) concluded '{concl}': {run_url}",
                )
            )
    return items


def check_open_bot_prs(repo: str, token: str, timeout: int = 15) -> AuditItem:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=20",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        prs = resp.json()
        bot_prs = [p for p in prs if str(p.get("head", {}).get("ref", "")).startswith("bot/")]
        if bot_prs:
            nums = ", ".join(f"#{p['number']}" for p in bot_prs[:5])
            return AuditItem(
                "github.bot_prs",
                "WARN",
                f"{len(bot_prs)} open bot sync PR(s) awaiting merge: {nums}",
            )
        return AuditItem("github.bot_prs", "OK", "0 pending bot sync PRs")
    except Exception as exc:
        return AuditItem("github.bot_prs", "WARN", f"Unable to check open PRs: {exc}")


def run_full_health_audit(
    root: Path,
    pages_url: str | None = None,
    repo: str | None = None,
    github_token: str | None = None,
) -> HealthAuditResult:
    """Run local + optional cloud (GitHub Actions & Pages) health audit."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    items: list[AuditItem] = [
        check_local_doctor(root),
        check_static_snapshot(root),
    ]
    if pages_url:
        items.append(check_pages_endpoint(pages_url))

    gh_repo = (repo or os.getenv("GITHUB_REPOSITORY", "")).strip()
    gh_tok = (github_token or os.getenv("GITHUB_TOKEN", "")).strip()
    if gh_repo and gh_tok:
        items.extend(check_github_workflows(gh_repo, gh_tok))
        items.append(check_open_bot_prs(gh_repo, gh_tok))

    return HealthAuditResult(checked_at=now_str, items=items)
