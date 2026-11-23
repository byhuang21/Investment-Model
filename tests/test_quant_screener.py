"""Unit tests for Layer 2 dual-track quant hard screening and 3-way routing."""

from __future__ import annotations

from coffee_can.config import load_rules
from coffee_can.schemas import FinancialSnapshot, ScreenOutcome
from coffee_can.screener import compute_near_miss_target_mcap, evaluate_snapshot


def _base_snapshot(**overrides: object) -> FinancialSnapshot:
    defaults: dict[str, object] = {
        "ticker": "TEST",
        "as_of_date": "2026-11-22",
        "cik": "0000000001",
        "company_name": "Test Compounder Inc.",
        "price_usd": 50.0,
        "market_cap_usd_m": 3000.0,
        "ev_to_sales": 5.0,
        "ttm_revenue_usd_m": 600.0,
        "revenue_yoy_growth": 0.22,
        "gross_margin": 0.45,
        "gross_margin_yoy_change": 0.02,
        "ocf_usd_m": 80.0,
        "fcf_usd_m": 60.0,
        "cash_and_st_inv_usd_m": 200.0,
        "cash_runway_months": None,
        "short_term_debt_to_cash": 0.10,
        "goodwill_to_total_assets": 0.12,
        "annual_share_dilution": 0.025,
        "filing_accession": "0001-26-000001",
    }
    defaults.update(overrides)
    return FinancialSnapshot(**defaults)  # type: ignore[arg-type]


def test_pass_track_a_profitable_compounder() -> None:
    rules = load_rules()
    decision = evaluate_snapshot(_base_snapshot(), rules, sector="Industrials")
    assert decision.outcome == ScreenOutcome.PASS
    assert decision.track_qualified == "profitable_compounder"
    assert decision.failed_rules == []


def test_pass_track_a2_operating_leverage_compounder_and_reject_weak_ocf() -> None:
    rules = load_rules()
    # Industrial / restaurant compounder: 25% GM, 27% YoY, 12% OCF margin -> PASS A2
    strong = _base_snapshot(
        ttm_revenue_usd_m=1500.0,
        revenue_yoy_growth=0.27,
        gross_margin=0.25,
        ocf_usd_m=180.0,
        fcf_usd_m=50.0,
    )
    dec_pass = evaluate_snapshot(strong, rules, sector="Industrials")
    assert dec_pass.outcome == ScreenOutcome.PASS
    assert dec_pass.track_qualified == "operating_leverage_compounder"

    # Low OCF margin (4.5%) at 22% GM -> REJECTED
    weak = _base_snapshot(
        ttm_revenue_usd_m=3600.0,
        revenue_yoy_growth=0.35,
        gross_margin=0.22,
        ocf_usd_m=162.0,
        fcf_usd_m=10.0,
    )
    dec_rej = evaluate_snapshot(weak, rules, sector="Industrials")
    assert dec_rej.outcome == ScreenOutcome.REJECTED
    assert any("tracks.failed_both" in r for r in dec_rej.failed_rules)


def test_pass_track_b_high_growth_reinvestment() -> None:
    rules = load_rules()
    snap = _base_snapshot(
        ocf_usd_m=-30.0,
        fcf_usd_m=-45.0,
        cash_runway_months=36.0,
        revenue_yoy_growth=0.35,
        gross_margin=0.52,
    )
    decision = evaluate_snapshot(snap, rules, sector="Technology")
    assert decision.outcome == ScreenOutcome.PASS
    assert decision.track_qualified == "high_growth_reinvestment"


def test_near_miss_when_market_cap_too_high_but_fundamentals_pass() -> None:
    rules = load_rules()
    snap = _base_snapshot(market_cap_usd_m=24000.0)
    decision = evaluate_snapshot(snap, rules, sector="Industrials")
    assert decision.outcome == ScreenOutcome.NEAR_MISS
    assert decision.failed_rules == ["MARKET_CAP_ABOVE_PASS_RANGE"]
    target = compute_near_miss_target_mcap(snap, rules, "MARKET_CAP_ABOVE_PASS_RANGE")
    assert target == 15000.0


def test_near_miss_when_ev_to_sales_too_high() -> None:
    rules = load_rules()
    snap = _base_snapshot(market_cap_usd_m=6000.0, ev_to_sales=22.5)
    decision = evaluate_snapshot(snap, rules, sector="Technology")
    assert decision.outcome == ScreenOutcome.NEAR_MISS
    assert decision.failed_rules == ["EV_TO_SALES_TOO_HIGH"]


def test_rejected_for_high_dilution_and_short_runway() -> None:
    rules = load_rules()
    snap = _base_snapshot(
        ocf_usd_m=-50.0,
        fcf_usd_m=-60.0,
        cash_runway_months=14.0,
        annual_share_dilution=0.09,
        revenue_yoy_growth=0.30,
        gross_margin=0.50,
    )
    decision = evaluate_snapshot(snap, rules, sector="Technology")
    assert decision.outcome == ScreenOutcome.REJECTED
    assert any("min_cash_runway_months" in r for r in decision.failed_rules)
    assert any("max_annual_share_dilution" in r for r in decision.failed_rules)


def test_rejected_excluded_sector() -> None:
    rules = load_rules()
    decision = evaluate_snapshot(_base_snapshot(), rules, sector="Financial Services")
    assert decision.outcome == ScreenOutcome.REJECTED
    assert "excluded_sector:Financial Services" in decision.failed_rules


def test_three_way_routing_persistence(tmp_path) -> None:
    import importlib.util
    from pathlib import Path

    from coffee_can.config import ProjectPaths
    from coffee_can.storage import read_rejected_log, read_yaml

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "2_quant_screener.py"
    spec = importlib.util.spec_from_file_location("quant_screener_cli", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    paths = ProjectPaths.from_root(tmp_path)
    rules = load_rules()

    # 1. PASS -> memory/research/<TICKER>/scorecard.yaml + evidence.md
    pass_dec = evaluate_snapshot(_base_snapshot(ticker="PASSCO"), rules, sector="Industrials")
    sc_path = mod.route_pass(pass_dec, paths, "grid_hv", "2026-11-22")
    assert sc_path.exists()
    assert (sc_path.parent / "evidence.md").exists()
    sc_data = read_yaml(sc_path)
    assert sc_data["dimensions"]["5_management"]["human_subjective_score"] is None
    assert sc_data["dimensions"]["5_management"]["reviewed_by_human"] is False

    # 2. NEAR_MISS -> memory/near_miss_pool/<TICKER>.yaml
    nm_dec = evaluate_snapshot(
        _base_snapshot(ticker="PRICEY", market_cap_usd_m=24000.0), rules, sector="Technology"
    )
    nm_path = mod.route_near_miss(nm_dec, paths, rules, "ai_infra", "2026-11-22")
    assert nm_path.exists()
    nm_data = read_yaml(nm_path)
    assert nm_data["targets"]["target_market_cap_usd_m"] == 15000.0

    # 3. REJECTED -> memory/rejected_log.json with SPY & QQQ closes
    rej_dec = evaluate_snapshot(
        _base_snapshot(ticker="DILUTE", annual_share_dilution=0.11), rules, sector="Technology"
    )
    rej_path = mod.route_rejected(rej_dec, paths, "ai_infra", "2026-11-22", 689.8, 595.2)
    log = read_rejected_log(rej_path)
    assert len(log["entries"]) == 1
    assert log["entries"][0]["ticker"] == "DILUTE"
    assert log["entries"][0]["spy_price_at_rejection"] == 689.8
    assert log["entries"][0]["qqq_price_at_rejection"] == 595.2
