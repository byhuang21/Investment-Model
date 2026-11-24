#!/usr/bin/env python3
"""Manage the isolated sandbox/ workspace for zero-risk strategy & UI experiments.

Usage:
  python scripts/sandbox_tool.py init     # Clone prod config/ & memory/ into sandbox/
  python scripts/sandbox_tool.py reset    # Wipe sandbox/ and re-clone fresh from prod
  python scripts/sandbox_tool.py status   # Inspect sandbox status & file counts
  python scripts/sandbox_tool.py diff     # Show rules.yaml diff & new files in sandbox
  python scripts/sandbox_tool.py clean    # Remove sandbox/ completely
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def init_sandbox(repo_root: Path, overwrite: bool = False) -> Path:
    sb_root = repo_root / "sandbox"
    if sb_root.exists():
        if not overwrite:
            raise FileExistsError(
                f"Sandbox already exists at {sb_root}. Use 'reset' to refresh it."
            )
        shutil.rmtree(sb_root)
    sb_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo_root / "config", sb_root / "config")
    shutil.copytree(repo_root / "memory", sb_root / "memory")
    return sb_root


def clean_sandbox(repo_root: Path) -> bool:
    sb_root = repo_root / "sandbox"
    if not sb_root.exists():
        return False
    shutil.rmtree(sb_root)
    return True


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))


def show_status(repo_root: Path) -> None:
    sb_root = repo_root / "sandbox"
    if not sb_root.exists():
        print("Sandbox status: NOT INITIALIZED (run: python scripts/sandbox_tool.py init)")
        return
    print(f"Sandbox status: ACTIVE ({sb_root})")
    print(
        f"  config files : sandbox={_count_files(sb_root / 'config')} "
        f"| prod={_count_files(repo_root / 'config')}"
    )
    print(
        f"  memory files : sandbox={_count_files(sb_root / 'memory')} "
        f"| prod={_count_files(repo_root / 'memory')}"
    )


def show_diff(repo_root: Path) -> None:
    sb_root = repo_root / "sandbox"
    if not sb_root.exists():
        print("Sandbox not initialized.")
        return

    prod_rules = (repo_root / "config" / "rules.yaml").read_text(encoding="utf-8").splitlines()
    sb_rules = (sb_root / "config" / "rules.yaml").read_text(encoding="utf-8").splitlines()
    diff_lines = list(
        difflib.unified_diff(
            prod_rules,
            sb_rules,
            fromfile="prod/config/rules.yaml",
            tofile="sandbox/config/rules.yaml",
            lineterm="",
        )
    )
    if diff_lines:
        print("=== rules.yaml diff ===")
        for line in diff_lines:
            print(line)
    else:
        print("rules.yaml: identical to prod")

    prod_mem = {
        p.relative_to(repo_root / "memory")
        for p in (repo_root / "memory").rglob("*")
        if p.is_file()
    }
    sb_mem = {
        p.relative_to(sb_root / "memory")
        for p in (sb_root / "memory").rglob("*")
        if p.is_file()
    }
    added = sorted(sb_mem - prod_mem)
    removed = sorted(prod_mem - sb_mem)
    print(f"Sandbox memory added ({len(added)}): {[str(p) for p in added[:15]]}")
    print(f"Sandbox memory removed ({len(removed)}): {[str(p) for p in removed[:15]]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage isolated Coffee Can sandbox workspace.")
    parser.add_argument("action", choices=["init", "reset", "status", "diff", "clean"])
    args = parser.parse_args(argv)

    if args.action == "init":
        try:
            sb = init_sandbox(ROOT_DIR, overwrite=False)
            print(f"Initialized isolated sandbox at {sb}")
            print(
                "Tip: run any script or workbench with --env sandbox (or COFFEE_CAN_ENV=sandbox)."
            )
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.action == "reset":
        sb = init_sandbox(ROOT_DIR, overwrite=True)
        print(f"Reset isolated sandbox from prod -> {sb}")
    elif args.action == "status":
        show_status(ROOT_DIR)
    elif args.action == "diff":
        show_diff(ROOT_DIR)
    elif args.action == "clean":
        removed = clean_sandbox(ROOT_DIR)
        print("Removed sandbox/." if removed else "Sandbox did not exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
