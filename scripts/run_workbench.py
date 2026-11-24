#!/usr/bin/env python3
"""Launch the local Coffee Can Interactive Web Workbench (FastAPI + Vanilla Web UI)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn

from coffee_can.config import resolve_workspace_root
from coffee_can.snapshot import export_static_snapshot
from coffee_can.web_app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Coffee Can Interactive Web Workbench.")
    parser.add_argument(
        "--env",
        choices=["prod", "sandbox"],
        default=None,
        help="Target workspace environment (defaults to COFFEE_CAN_ENV or 'prod').",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()

    ws_root, env_name = resolve_workspace_root(args.env, ROOT_DIR)
    os.environ["COFFEE_CAN_ENV"] = env_name

    if env_name == "prod":
        export_static_snapshot(ROOT_DIR)

    print(f"Launching Coffee Can Workbench [{env_name.upper()}] -> workspace: {ws_root}")
    app = create_app(root=ws_root, web_dir=ROOT_DIR / "web", env_name=env_name)
    uvicorn.run(app, host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
