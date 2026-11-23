"""Persistence helpers for reading and writing YAML/JSON records in memory/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from coffee_can.schemas import RejectedEntry


def list_active_yaml_files(directory: Path) -> list[Path]:
    """List non-template YAML files in a directory (ignoring `_`-prefixed files)."""
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix in {".yaml", ".yml"}
        and not path.name.startswith("_")
    )


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping from disk."""
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a YAML mapping atomically with UTF-8 encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp_path.replace(path)


def read_rejected_log(path: Path) -> dict[str, Any]:
    """Load memory/rejected_log.json, initializing structure if missing."""
    if not path.exists():
        return {"schema_version": "0.1.0", "entries": []}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError(f"Invalid rejected_log format in {path}")
    return data


def append_rejected_entry(path: Path, entry: RejectedEntry) -> None:
    """Append a rejected stock record atomically, deduplicating same-day re-runs."""
    log = read_rejected_log(path)
    entry_dict = entry.to_dict()
    entries: list[dict[str, Any]] = log["entries"]
    replaced = False
    for idx, existing in enumerate(entries):
        if (
            existing.get("ticker") == entry.ticker
            and existing.get("rejected_at") == entry.rejected_at
            and existing.get("stage") == entry.stage
        ):
            entries[idx] = entry_dict
            replaced = True
            break
    if not replaced:
        entries.append(entry_dict)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(log, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp_path.replace(path)
