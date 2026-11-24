"""Configuration, environment isolation (prod/sandbox), and path helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional when python-dotenv isn't installed yet
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

VALID_ENVS = {"prod", "sandbox"}


def get_project_root() -> Path:
    """Resolve repository root relative to this module."""
    return Path(__file__).resolve().parents[2]


def resolve_workspace_root(
    env: str | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, str]:
    """Resolve active workspace directory (`prod` -> repo root, `sandbox` -> `<repo>/sandbox`)."""
    base = (repo_root or get_project_root()).resolve()
    chosen = (env or os.getenv("COFFEE_CAN_ENV", "prod")).strip().lower()
    if chosen not in VALID_ENVS:
        raise ValueError(f"Unsupported COFFEE_CAN_ENV '{chosen}'. Expected one of {VALID_ENVS}.")
    if chosen == "sandbox":
        sb_root = base / "sandbox"
        if not (sb_root / "config" / "rules.yaml").exists():
            raise FileNotFoundError(
                f"Sandbox workspace not initialized at {sb_root}. "
                "Run: python scripts/sandbox_tool.py init"
            )
        return sb_root, "sandbox"
    return base, "prod"


@dataclass(frozen=True)
class ProjectPaths:
    """Canonical project directories and files (supports both prod and sandbox roots)."""

    root: Path
    config_dir: Path
    rules_file: Path
    data_sources_file: Path
    memory_dir: Path
    industries_dir: Path
    research_dir: Path
    near_miss_dir: Path
    coffee_can_dir: Path
    transcripts_dir: Path
    reports_dir: Path
    quarantine_dir: Path
    quarantine_anomalies_dir: Path
    quarantine_proposals_dir: Path
    quarantine_signals_dir: Path
    rejected_log_file: Path

    @classmethod
    def from_root(cls, root: Path | None = None) -> ProjectPaths:
        base = (root or get_project_root()).resolve()
        config_dir = base / "config"
        memory_dir = base / "memory"
        quarantine_dir = memory_dir / "quarantine"
        return cls(
            root=base,
            config_dir=config_dir,
            rules_file=config_dir / "rules.yaml",
            data_sources_file=config_dir / "data_sources.yaml",
            memory_dir=memory_dir,
            industries_dir=memory_dir / "industries",
            research_dir=memory_dir / "research",
            near_miss_dir=memory_dir / "near_miss_pool",
            coffee_can_dir=memory_dir / "coffee_can",
            transcripts_dir=memory_dir / "inputs" / "transcripts",
            reports_dir=memory_dir / "reports",
            quarantine_dir=quarantine_dir,
            quarantine_anomalies_dir=quarantine_dir / "anomalies",
            quarantine_proposals_dir=quarantine_dir / "proposals",
            quarantine_signals_dir=quarantine_dir / "signals",
            rejected_log_file=memory_dir / "rejected_log.json",
        )


def load_yaml_config(path: Path) -> dict[str, Any]:
    """Load a YAML configuration file and validate that it is a mapping."""
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}, got {type(data).__name__}")
    return data


def load_rules(root: Path | None = None) -> dict[str, Any]:
    """Load investment rules from config/rules.yaml."""
    paths = ProjectPaths.from_root(root)
    return load_yaml_config(paths.rules_file)


def load_data_sources(root: Path | None = None) -> dict[str, Any]:
    """Load data source settings from config/data_sources.yaml."""
    paths = ProjectPaths.from_root(root)
    return load_yaml_config(paths.data_sources_file)


def load_environment(root: Path | None = None) -> None:
    """Load local .env file if present without overriding existing environment variables."""
    base = (root or get_project_root()).resolve()
    env_file = base / ".env"
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)


def get_sec_user_agent(data_sources: dict[str, Any] | None = None) -> str:
    """Return SEC EDGAR User-Agent from environment variable configured in data_sources.yaml."""
    env_key = "SEC_USER_AGENT"
    if data_sources is not None:
        env_key = data_sources.get("sec_edgar", {}).get("user_agent_env", env_key)
    value = os.getenv(env_key, "").strip()
    if not value or "your_email@example.com" in value:
        raise OSError(
            f"Missing valid {env_key}. SEC EDGAR requires a real User-Agent "
            "(e.g. 'CoffeeCanResearch your_name@domain.com')."
        )
    return value
