#!/usr/bin/env python3
"""Launch the local Coffee Can Interactive Web Workbench (FastAPI + Vanilla Web UI)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn

from coffee_can.snapshot import export_static_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Coffee Can Interactive Web Workbench.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()

    # Keep static snapshot fresh on launch too
    export_static_snapshot(ROOT_DIR)
    uvicorn.run("coffee_can.web_app:app", host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
