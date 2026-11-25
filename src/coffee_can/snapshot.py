"""Aggregates Git-backed memory/ and config/ into a unified JSON snapshot for the Web UI."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coffee_can.config import ProjectPaths, load_rules
from coffee_can.storage import list_active_yaml_files, read_rejected_log, read_yaml


def build_workbench_snapshot(
    root: Path | None = None,
    env_name: str = "prod",
) -> dict[str, Any]:
    """Read live state from config/ and memory/ without external network calls."""
    paths = ProjectPaths.from_root(root)
    rules = load_rules(paths.root)

    industries = [read_yaml(p) for p in list_active_yaml_files(paths.industries_dir)]
    near_miss = [read_yaml(p) for p in list_active_yaml_files(paths.near_miss_dir)]
    coffee_can = [read_yaml(p) for p in list_active_yaml_files(paths.coffee_can_dir)]
    rejected_log = read_rejected_log(paths.rejected_log_file)

    research: list[dict[str, Any]] = []
    if paths.research_dir.exists():
        for entry in sorted(paths.research_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            sc_path = entry / "scorecard.yaml"
            ev_path = entry / "evidence.md"
            if not sc_path.exists():
                continue
            scorecard = read_yaml(sc_path)
            evidence_md = ev_path.read_text(encoding="utf-8") if ev_path.exists() else ""
            research.append(
                {
                    "ticker": scorecard.get("ticker", entry.name),
                    "scorecard": scorecard,
                    "evidence_markdown": evidence_md,
                }
            )

    post_mortem: dict[str, Any] | None = None
    pm_json = paths.reports_dir / "latest_post_mortem.json"
    if pm_json.exists():
        try:
            post_mortem = json.loads(pm_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            post_mortem = None

    reports: list[dict[str, Any]] = []
    if paths.reports_dir.exists():
        md_files = sorted(
            (p for p in paths.reports_dir.iterdir() if p.is_file() and p.suffix == ".md"),
            reverse=True,
        )
        for p in md_files[:12]:
            reports.append(
                {
                    "filename": p.name,
                    "content": p.read_text(encoding="utf-8"),
                }
            )

    def _list_lane(lane_dir: Path) -> list[str]:
        if not lane_dir.exists():
            return []
        return sorted(
            p.name for p in lane_dir.iterdir() if p.is_file() and not p.name.startswith(".")
        )

    quarantine = {
        "anomalies": _list_lane(paths.quarantine_anomalies_dir),
        "proposals": _list_lane(paths.quarantine_proposals_dir),
        "signals": _list_lane(paths.quarantine_signals_dir),
    }

    discovered_signals: list[dict[str, Any]] = []
    discovered_meta: dict[str, Any] = {
        "last_scanned_at": None,
        "total_signals": 0,
        "new_signals_count": 0,
    }
    signals_yaml = paths.quarantine_signals_dir / "discovered_themes.yaml"
    if signals_yaml.exists():
        try:
            sig_doc = read_yaml(signals_yaml)
            raw_sigs = sig_doc.get("signals") or []
            if isinstance(raw_sigs, list):
                discovered_signals = [item for item in raw_sigs if isinstance(item, dict)]
            discovered_meta = {
                "last_scanned_at": sig_doc.get("last_scanned_at"),
                "total_signals": len(discovered_signals),
                "new_signals_count": sum(
                    1
                    for item in discovered_signals
                    if str(item.get("status", "NEW")).upper() == "NEW"
                ),
            }
        except Exception:
            discovered_signals = []

    return {
        "env": env_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "rules_version": rules.get("version", "0.2.0"),
        "rules": rules,
        "industries": industries,
        "discovered_signals": discovered_signals,
        "discovered_signals_meta": discovered_meta,
        "research": research,
        "near_miss": near_miss,
        "coffee_can": coffee_can,
        "rejected_log": rejected_log.get("entries", []),
        "post_mortem": post_mortem,
        "quarantine": quarantine,
        "reports": reports,
    }


def export_static_snapshot(root: Path | None = None) -> Path:
    """Write web/data/snapshot.json for zero-server static deployment (GitHub Pages/Vercel)."""
    paths = ProjectPaths.from_root(root)
    snapshot = build_workbench_snapshot(paths.root)
    out_path = paths.root / "web" / "data" / "snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return out_path
