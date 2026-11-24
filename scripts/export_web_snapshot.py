#!/usr/bin/env python3
"""Export Git-backed memory/ and config/ into web/data/snapshot.json for static hosting."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.snapshot import export_static_snapshot


def main() -> int:
    out_path = export_static_snapshot(ROOT_DIR)
    print(f"Exported static workbench snapshot -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
