"""Unit tests for project governance: sandbox isolation, quarantine, and doctor invariants."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from coffee_can.config import ProjectPaths, resolve_workspace_root
from coffee_can.doctor import run_doctor
from coffee_can.storage import write_yaml


def _load_sandbox_tool():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "sandbox_tool.py"
    spec = importlib.util.spec_from_file_location("sandbox_tool", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sandbox_init_resolve_and_clean_isolated_from_prod(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copytree(repo_root / "config", tmp_path / "config")
    shutil.copytree(repo_root / "memory", tmp_path / "memory")

    # Before init, resolving sandbox must raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        resolve_workspace_root("sandbox", tmp_path)

    sb_tool = _load_sandbox_tool()
    sb_dir = sb_tool.init_sandbox(tmp_path, overwrite=False)
    assert (sb_dir / "config" / "rules.yaml").exists()
    assert (sb_dir / "memory" / "rejected_log.json").exists()

    ws_root, env_name = resolve_workspace_root("sandbox", tmp_path)
    assert env_name == "sandbox"
    assert ws_root == sb_dir

    # Modifying sandbox MUST NOT touch prod
    (sb_dir / "memory" / "test_marker.txt").write_text("sandbox only", encoding="utf-8")
    assert not (tmp_path / "memory" / "test_marker.txt").exists()

    assert sb_tool.clean_sandbox(tmp_path) is True
    assert not sb_dir.exists()


def test_doctor_passes_on_real_repo_and_catches_agent_rules_violation(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    # 1. Real repository must be healthy
    real_report = run_doctor(repo_root)
    assert real_report.ok is True, f"Unexpected doctor errors: {real_report.errors}"

    # 2. Illegal coffee_can entry missing human_score & <10x upside MUST fail doctor
    shutil.copytree(repo_root / "config", tmp_path / "config")
    shutil.copytree(repo_root / "memory", tmp_path / "memory")
    paths = ProjectPaths.from_root(tmp_path)
    write_yaml(
        paths.coffee_can_dir / "ILLEGAL.yaml",
        {
            "ticker": "ILLEGAL",
            "status": "IN_CAN",
            "entry": {
                "entered_at": "2026-11-24",
                "target_upside_multiple": 4.0,
                "management_human_score": None,
            },
            "falsification_lines": [],
        },
    )
    bad_report = run_doctor(tmp_path)
    assert bad_report.ok is False
    msgs = " ".join(f.message for f in bad_report.errors)
    assert "management_human_score" in msgs
    assert "Valuation gate violation" in msgs
    assert "falsification_lines" in msgs
