"""Repository integrity checker enforcing AGENT_RULES.md & YAML/JSON schema invariants."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coffee_can.config import ProjectPaths, load_data_sources, load_rules
from coffee_can.schemas import IndustryStatus, ResearchStatus
from coffee_can.storage import list_active_yaml_files, read_rejected_log, read_yaml

VALID_FL_OPERATORS = {"<", "<=", ">", ">="}


@dataclass
class DoctorFinding:
    level: str  # "ERROR" | "WARN"
    scope: str
    message: str


@dataclass
class DoctorReport:
    workspace_root: str
    checks_passed: int = 0
    findings: list[DoctorFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[DoctorFinding]:
        return [f for f in self.findings if f.level == "ERROR"]

    @property
    def warnings(self) -> list[DoctorFinding]:
        return [f for f in self.findings if f.level == "WARN"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def run_doctor(root: Path) -> DoctorReport:
    """Validate config files, memory records, AGENT_RULES invariants, and quarantine backlog."""
    paths = ProjectPaths.from_root(root)
    report = DoctorReport(workspace_root=str(paths.root))

    # 1. Config integrity
    try:
        rules = load_rules(paths.root)
        mcap = rules["layer2_quant_screener"]["market_cap_usd_m"]
        if not (mcap["min_pass"] < mcap["max_pass"] <= mcap["max_near_miss"]):
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    "config/rules.yaml",
                    "Invalid market_cap_usd_m ordering: min_pass < max_pass <= max_near_miss",
                )
            )
        else:
            report.checks_passed += 1
        min_upside = float(rules["layer3_deep_dive"]["min_target_upside_multiple"])
    except Exception as exc:
        report.findings.append(DoctorFinding("ERROR", "config/rules.yaml", str(exc)))
        return report

    try:
        sources = load_data_sources(paths.root)
        rps = float(sources["sec_edgar"]["max_requests_per_second"])
        if rps > 10:
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    "config/data_sources.yaml",
                    f"SEC EDGAR max_requests_per_second ({rps}) exceeds SEC fair-access limit (10)",
                )
            )
        else:
            report.checks_passed += 1
    except Exception as exc:
        report.findings.append(DoctorFinding("ERROR", "config/data_sources.yaml", str(exc)))

    # 2. Industries state-machine records
    valid_ind_statuses = {s.value for s in IndustryStatus}
    for path in list_active_yaml_files(paths.industries_dir):
        rec = read_yaml(path)
        st = str(rec.get("status", "")).upper()
        if not rec.get("slug") or st not in valid_ind_statuses:
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    f"industries/{path.name}",
                    f"Missing slug or invalid status '{st}'",
                )
            )
        else:
            report.checks_passed += 1

    # 3. Research dossiers (AGENT_RULES: APPROVED_FOR_CAN requires human_subjective_score)
    valid_res_statuses = {s.value for s in ResearchStatus}
    if paths.research_dir.exists():
        for entry in sorted(paths.research_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            sc_path = entry / "scorecard.yaml"
            if not sc_path.exists():
                report.findings.append(
                    DoctorFinding("ERROR", f"research/{entry.name}", "Missing scorecard.yaml")
                )
                continue
            sc = read_yaml(sc_path)
            st = str(sc.get("status", "")).upper()
            if st not in valid_res_statuses:
                report.findings.append(
                    DoctorFinding(
                        "ERROR", f"research/{entry.name}", f"Invalid research status '{st}'"
                    )
                )
                continue
            mgmt = sc.get("dimensions", {}).get("5_management", {})
            if st == "APPROVED_FOR_CAN" and mgmt.get("human_subjective_score") is None:
                report.findings.append(
                    DoctorFinding(
                        "ERROR",
                        f"research/{entry.name}",
                        "AGENT_RULES violation: APPROVED_FOR_CAN without human_subjective_score",
                    )
                )
            else:
                report.checks_passed += 1

    # 4. Coffee Can holdings (strict AGENT_RULES enforcement)
    for path in list_active_yaml_files(paths.coffee_can_dir):
        pos = read_yaml(path)
        entry_blk = pos.get("entry") or {}
        if entry_blk.get("management_human_score") is None:
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    f"coffee_can/{path.name}",
                    "AGENT_RULES violation: missing entry.management_human_score",
                )
            )
        upside = entry_blk.get("target_upside_multiple")
        if upside is None or float(upside) < min_upside:
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    f"coffee_can/{path.name}",
                    f"10x Odds violation: target_upside_multiple ({upside}) < {min_upside:g}x",
                )
            )
        lines = list(pos.get("falsification_lines") or [])
        if not lines:
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    f"coffee_can/{path.name}",
                    "Missing machine-readable falsification_lines",
                )
            )
        for fl in lines:
            if fl.get("operator") not in VALID_FL_OPERATORS or not fl.get("metric"):
                report.findings.append(
                    DoctorFinding(
                        "ERROR",
                        f"coffee_can/{path.name}",
                        f"Invalid falsification line definition: {fl}",
                    )
                )
        report.checks_passed += 1

    # 5. Near-miss watchlist
    for path in list_active_yaml_files(paths.near_miss_dir):
        nm = read_yaml(path)
        tgt = nm.get("targets", {}).get("target_market_cap_usd_m")
        if tgt is None or float(tgt) <= 0:
            report.findings.append(
                DoctorFinding(
                    "ERROR",
                    f"near_miss_pool/{path.name}",
                    f"Invalid target_market_cap_usd_m: {tgt}",
                )
            )
        else:
            report.checks_passed += 1

    # 6. Rejected log JSON schema & duplicate check
    try:
        rej = read_rejected_log(paths.rejected_log_file)
        seen_keys: set[tuple[str, str, str]] = set()
        for item in rej.get("entries", []):
            key = (
                str(item.get("ticker", "")),
                str(item.get("rejected_at", "")),
                str(item.get("stage", "")),
            )
            if key in seen_keys:
                report.findings.append(
                    DoctorFinding(
                        "WARN", "memory/rejected_log.json", f"Duplicate same-day entry: {key}"
                    )
                )
            seen_keys.add(key)
        report.checks_passed += 1
    except Exception as exc:
        report.findings.append(DoctorFinding("ERROR", "memory/rejected_log.json", str(exc)))

    # 7. Quarantine backlog notice (informational WARN only)
    q_count = 0
    for q_dir in (
        paths.quarantine_anomalies_dir,
        paths.quarantine_proposals_dir,
        paths.quarantine_signals_dir,
    ):
        if q_dir.exists():
            q_count += sum(
                1 for p in q_dir.iterdir() if p.is_file() and not p.name.startswith(".")
            )
    if q_count > 0:
        report.findings.append(
            DoctorFinding(
                "WARN",
                "memory/quarantine/",
                f"{q_count} item(s) waiting in quarantine for human review/promotion.",
            )
        )

    return report
