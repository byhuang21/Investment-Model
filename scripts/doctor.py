#!/usr/bin/env python3
"""Integrity doctor: validate config/, memory/, AGENT_RULES invariants, and quarantine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.config import resolve_workspace_root
from coffee_can.doctor import run_doctor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Coffee Can config & memory integrity.")
    parser.add_argument(
        "--env",
        choices=["prod", "sandbox"],
        default=None,
        help="Target workspace environment (defaults to COFFEE_CAN_ENV or 'prod').",
    )
    args = parser.parse_args(argv)

    workspace_root, env_name = resolve_workspace_root(args.env, ROOT_DIR)
    report = run_doctor(workspace_root)

    print(f"=== Coffee Can Doctor [{env_name.upper()}] ===")
    print(f"Workspace     : {report.workspace_root}")
    print(f"Checks passed : {report.checks_passed}")
    print(f"Warnings      : {len(report.warnings)}")
    print(f"Errors        : {len(report.errors)}")

    for finding in report.findings:
        print(f"  [{finding.level}] {finding.scope}: {finding.message}")

    if report.ok:
        print("Result: HEALTHY")
        return 0
    print("Result: FAILED (fix ERROR findings before merging)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
