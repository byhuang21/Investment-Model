"""API tests for the Coffee Can FastAPI Web Workbench (human gate & YAML persistence)."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from coffee_can.config import ProjectPaths
from coffee_can.storage import read_yaml
from coffee_can.web_app import create_app


def _copy_minimal_workspace(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    shutil.copytree(repo_root / "config", tmp_path / "config")
    shutil.copytree(repo_root / "memory", tmp_path / "memory")
    shutil.copytree(repo_root / "web", tmp_path / "web")
    return tmp_path


def test_snapshot_and_human_gate_enforcement(tmp_path: Path) -> None:
    ws = _copy_minimal_workspace(tmp_path)
    app = create_app(ws)
    client = TestClient(app)

    # 1. Health & snapshot load real data
    assert client.get("/api/health").json()["status"] == "ok"
    snap = client.get("/api/snapshot").json()
    assert len(snap["industries"]) >= 4
    assert len(snap["research"]) >= 1
    assert len(snap["near_miss"]) >= 1

    ticker = snap["research"][0]["ticker"]

    # 2. Promoting WITHOUT human score must be rejected (AGENT_RULES.md guardrail!)
    resp_block = client.post(f"/api/research/{ticker}/promote")
    assert resp_block.status_code == 400
    assert "human_subjective_score" in resp_block.json()["detail"]

    # 3. Save valid human review with >= 10x odds, score, memo, and falsification line
    review_resp = client.post(
        f"/api/research/{ticker}/human-review",
        json={
            "conservative_10y_target_market_cap_usd_m": 80000.0,
            "implied_upside_multiple": 12.5,
            "assumptions_summary": "Conservative 10y compounder case",
            "human_subjective_score": 8,
            "human_notes": "Disciplined capital allocator",
            "three_sentence_memo": ["Sentence 1", "Sentence 2", "Sentence 3"],
            "falsification_lines": [
                {
                    "id": "FL-1",
                    "description": "Revenue growth < 12% for 2Q",
                    "metric": "revenue_yoy_growth",
                    "operator": "<",
                    "threshold": 0.12,
                    "consecutive_quarters": 2,
                    "severity": "RED",
                }
            ],
        },
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["saved"] is True

    # 4. Promote succeeds and writes memory/coffee_can/<TICKER>.yaml
    prom_resp = client.post(f"/api/research/{ticker}/promote")
    assert prom_resp.status_code == 200
    paths = ProjectPaths.from_root(ws)
    pos_path = paths.coffee_can_dir / f"{ticker}.yaml"
    assert pos_path.exists()
    pos_data = read_yaml(pos_path)
    assert pos_data["status"] == "IN_CAN"
    assert pos_data["entry"]["management_human_score"] == 8

    # 5. Update near-miss target market cap
    nm_ticker = snap["near_miss"][0]["ticker"]
    nm_resp = client.patch(
        f"/api/near-miss/{nm_ticker}/target",
        json={"target_market_cap_usd_m": 12500.0},
    )
    assert nm_resp.status_code == 200
    assert nm_resp.json()["near_miss"]["targets"]["target_market_cap_usd_m"] == 12500.0

    # 6. Toggle industry status
    ind_slug = snap["industries"][0]["slug"]
    ind_resp = client.patch(
        f"/api/industries/{ind_slug}/status",
        json={"status": "DORMANT", "note": "Unit test toggle"},
    )
    assert ind_resp.status_code == 200
    assert ind_resp.json()["industry"]["status"] == "DORMANT"

    # 7. End-to-end closed loop: promoted Coffee Can holding passes doctor & earnings diff
    from coffee_can.doctor import run_doctor
    from coffee_can.earnings_diff import evaluate_earnings_diff
    from coffee_can.schemas import TrafficLight

    doc_report = run_doctor(ws)
    assert doc_report.ok is True, f"Doctor failed after promotion: {doc_report.errors}"

    chk = evaluate_earnings_diff(
        position=pos_data,
        fundamentals={
            "ttm_revenue_usd_m": 1500.0,
            "revenue_yoy_growth": 0.25,
            "gross_margin": 0.45,
            "gross_margin_yoy_change": 0.01,
            "annual_share_dilution": 0.015,
            "filing_accession": "0009-26-000001",
            "latest_period_end": "2026-09-30",
        },
        rules=snap["rules"],
        filing_meta={"form": "10-Q", "accession": "0009-26-000001", "report_date": "2026-09-30"},
    )
    assert chk.is_new_filing is True
    assert chk.traffic_light == TrafficLight.GREEN


def test_quarantine_signal_promote_and_dismiss(tmp_path: Path) -> None:
    from coffee_can.doctor import run_doctor
    from coffee_can.storage import write_yaml

    ws = _copy_minimal_workspace(tmp_path)
    paths = ProjectPaths.from_root(ws)
    write_yaml(
        paths.quarantine_signals_dir / "discovered_themes.yaml",
        {
            "last_scanned_at": "2026-11-24",
            "total_signals": 2,
            "new_signals_count": 2,
            "signals": [
                {
                    "slug": "commercial_space_launch_leo",
                    "name": "商业航天发射与低轨卫星",
                    "source": "CROSS_SECTOR_PROBE",
                    "status": "NEW",
                    "headline_metric": "18 条 SEC 180天公告",
                    "rationale": "Test probe signal",
                    "discovered_at": "2026-11-24",
                    "last_seen_at": "2026-11-24",
                    "sec_keywords": ["orbital launch"],
                    "usaspending_keywords": ["space launch"],
                    "naics_codes": [],
                    "sic_codes": [],
                    "discovered_tickers": ["RKLB", "ASTS"],
                },
                {
                    "slug": "naics_999999",
                    "name": "Noise Sector",
                    "source": "USASPENDING_NAICS",
                    "status": "NEW",
                    "headline_metric": "+30.0% YoY",
                    "rationale": "Noise",
                    "discovered_at": "2026-11-24",
                    "last_seen_at": "2026-11-24",
                    "naics_codes": ["999999"],
                },
            ],
        },
    )

    app = create_app(ws)
    client = TestClient(app)

    snap = client.get("/api/snapshot").json()
    assert snap["discovered_signals_meta"]["new_signals_count"] == 2

    # Promote first signal into memory/industries/ as DORMANT
    prom = client.post(
        "/api/quarantine/signals/commercial_space_launch_leo/promote",
        json={"status": "DORMANT"},
    )
    assert prom.status_code == 200
    assert prom.json()["promoted"] is True
    assert (paths.industries_dir / "commercial_space_launch_leo.yaml").exists()

    # Dismiss second signal
    dis = client.post("/api/quarantine/signals/naics_999999/dismiss")
    assert dis.status_code == 200
    assert dis.json()["dismissed"] is True

    snap2 = client.get("/api/snapshot").json()
    assert snap2["discovered_signals_meta"]["new_signals_count"] == 0
    assert run_doctor(ws).ok is True

