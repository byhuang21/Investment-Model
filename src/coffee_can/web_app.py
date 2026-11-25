"""Lightweight FastAPI backend for the Coffee Can Research Workbench.

Provides read/write access to Git-backed memory/ YAML & JSON records while strictly
enforcing AGENT_RULES.md guardrails (human subjective management score & >=10x odds
required before promoting any research dossier into memory/coffee_can/).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coffee_can.config import ProjectPaths, get_project_root, load_rules
from coffee_can.radar import build_industry_record_from_signal
from coffee_can.schemas import IndustryStatus
from coffee_can.snapshot import build_workbench_snapshot
from coffee_can.storage import read_yaml, write_yaml


class FalsificationLineInput(BaseModel):
    id: str
    description: str
    metric: str
    operator: str
    threshold: float
    consecutive_quarters: int = Field(default=1, ge=1, le=8)
    severity: str = "RED"


class HumanReviewPayload(BaseModel):
    valuation_horizon: str = "1-2y_forward"
    forward_1_2y_valuation_note: str = ""
    long_term_runway_intact: bool = True
    current_price_usd: float | None = None
    current_market_cap_usd_m: float | None = None
    forward_pe: float | None = None
    ev_to_sales: float | None = None
    conservative_10y_target_market_cap_usd_m: float | None = None
    implied_upside_multiple: float | None = None
    assumptions_summary: str = ""
    human_subjective_score: int | None = Field(default=None, ge=1, le=10)
    human_notes: str = ""
    three_sentence_memo: list[str] = Field(default_factory=lambda: ["", "", ""])
    falsification_lines: list[FalsificationLineInput] = Field(default_factory=list)


class IndustryStatusPayload(BaseModel):
    status: IndustryStatus
    note: str = "Manual toggle via Coffee Can Web Workbench"


class WatchlistTargetPayload(BaseModel):
    target_market_cap_usd_m: float = Field(..., gt=0)


class PromoteSignalPayload(BaseModel):
    status: IndustryStatus = IndustryStatus.DORMANT


def create_app(
    root: Path | None = None,
    web_dir: Path | None = None,
    env_name: str = "prod",
) -> FastAPI:
    """Create a configured FastAPI instance bound to `root` (defaults to repo root)."""
    repo_root = (root or get_project_root()).resolve()
    paths = ProjectPaths.from_root(repo_root)
    static_web_dir = (web_dir or (repo_root / "web")).resolve()

    app = FastAPI(
        title="Coffee Can Research Workbench API",
        version="0.2.0",
        description="Local read/write workbench over Git-backed Coffee Can memory/ files.",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "live-rw", "env": env_name}

    @app.get("/api/snapshot")
    def get_snapshot() -> dict[str, Any]:
        return build_workbench_snapshot(repo_root, env_name=env_name)

    @app.post("/api/research/{ticker}/human-review")
    def save_human_review(ticker: str, payload: HumanReviewPayload) -> dict[str, Any]:
        sym = ticker.strip().upper()
        sc_path = paths.research_dir / sym / "scorecard.yaml"
        if not sc_path.exists():
            raise HTTPException(status_code=404, detail=f"Research dossier not found for {sym}")

        rules = load_rules(repo_root)
        min_upside = float(rules["layer3_deep_dive"]["min_target_upside_multiple"])
        today_str = datetime.now(UTC).date().isoformat()
        sc = read_yaml(sc_path)

        odds = dict(sc.get("odds_check") or {})
        odds["valuation_horizon"] = payload.valuation_horizon
        odds["long_term_runway_intact"] = payload.long_term_runway_intact
        if payload.forward_1_2y_valuation_note:
            odds["forward_1_2y_valuation_note"] = payload.forward_1_2y_valuation_note.strip()
        if payload.current_price_usd is not None:
            odds["current_price_usd"] = round(payload.current_price_usd, 2)
        if payload.current_market_cap_usd_m is not None:
            odds["current_market_cap_usd_m"] = round(payload.current_market_cap_usd_m, 2)
        if payload.forward_pe is not None:
            odds["forward_pe"] = round(payload.forward_pe, 2)
        if payload.ev_to_sales is not None:
            odds["ev_to_sales"] = round(payload.ev_to_sales, 2)
        if payload.conservative_10y_target_market_cap_usd_m is not None:
            odds["conservative_10y_target_market_cap_usd_m"] = round(
                payload.conservative_10y_target_market_cap_usd_m, 2
            )
        if payload.implied_upside_multiple is not None:
            odds["implied_upside_multiple"] = round(payload.implied_upside_multiple, 2)
            odds["passed_10x_gate"] = payload.implied_upside_multiple >= min_upside
        if payload.assumptions_summary:
            odds["assumptions_summary"] = payload.assumptions_summary
        sc["odds_check"] = odds

        dims = dict(sc.get("dimensions") or {})
        mgmt = dict(dims.get("5_management") or {})
        mgmt["human_subjective_score"] = payload.human_subjective_score
        mgmt["human_notes"] = payload.human_notes
        if payload.human_subjective_score is not None:
            mgmt["reviewed_by_human"] = True
            mgmt["reviewed_at"] = today_str
        dims["5_management"] = mgmt
        sc["dimensions"] = dims

        memo = [m.strip() for m in payload.three_sentence_memo][:3]
        while len(memo) < 3:
            memo.append("")
        sc["decision"] = {
            "three_sentence_memo": memo,
            "falsification_lines": [
                {
                    "id": fl.id,
                    "description": fl.description,
                    "metric": fl.metric,
                    "operator": fl.operator,
                    "threshold": fl.threshold,
                    "consecutive_quarters": fl.consecutive_quarters,
                    "breach_streak": 0,
                    "severity": fl.severity.upper(),
                }
                for fl in payload.falsification_lines
            ],
        }
        if sc.get("status") == "DRAFTING_EVIDENCE":
            sc["status"] = "PENDING_HUMAN_REVIEW"
        sc["updated_at"] = today_str

        write_yaml(sc_path, sc)
        return {"saved": True, "ticker": sym, "scorecard": sc}

    @app.post("/api/research/{ticker}/promote")
    def promote_to_coffee_can(ticker: str) -> dict[str, Any]:
        """Promote a human-reviewed Layer 3 dossier into memory/coffee_can/<TICKER>.yaml."""
        sym = ticker.strip().upper()
        sc_path = paths.research_dir / sym / "scorecard.yaml"
        if not sc_path.exists():
            raise HTTPException(status_code=404, detail=f"Research dossier not found for {sym}")

        rules = load_rules(repo_root)
        l3_rules = rules["layer3_deep_dive"]
        min_upside = float(l3_rules["min_target_upside_multiple"])
        require_10y_veto = bool(l3_rules.get("require_10y_exact_multiple_veto", False))
        min_fl = int(l3_rules.get("required_falsification_lines_min", 1))
        sc = read_yaml(sc_path)

        mgmt = sc.get("dimensions", {}).get("5_management", {})
        human_score = mgmt.get("human_subjective_score")
        if human_score is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "AGENT_RULES violation: human_subjective_score is required before promotion."
                ),
            )

        odds = sc.get("odds_check") or {}
        upside = odds.get("implied_upside_multiple")
        fwd_note = str(odds.get("forward_1_2y_valuation_note") or "").strip()
        runway_intact = odds.get("long_term_runway_intact", True)
        if require_10y_veto:
            if upside is None or float(upside) < min_upside:
                raise HTTPException(
                    status_code=400,
                    detail=f"10x Odds Veto: implied_upside_multiple must be >= {min_upside:g}x.",
                )
        else:
            has_legacy_10x = upside is not None and float(upside) >= min_upside
            has_forward_check = bool(fwd_note) and runway_intact is not False
            if not (has_forward_check or has_legacy_10x):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Valuation Gate: provide a 1-2Y forward valuation assessment "
                        "(with long_term_runway_intact=true) before promotion."
                    ),
                )

        decision = sc.get("decision") or {}
        falsification_lines = list(decision.get("falsification_lines") or [])
        if len(falsification_lines) < min_fl:
            raise HTTPException(
                status_code=400,
                detail=f"At least {min_fl} quantifiable falsification line is required.",
            )

        memo = [m for m in (decision.get("three_sentence_memo") or []) if str(m).strip()]
        if not memo:
            raise HTTPException(
                status_code=400,
                detail="Please provide at least one sentence in three_sentence_memo.",
            )

        today_str = datetime.now(UTC).date().isoformat()
        pos_path = paths.coffee_can_dir / f"{sym}.yaml"
        entry_blk: dict[str, Any] = {
            "entered_at": today_str,
            "entry_price_usd": odds.get("current_price_usd"),
            "entry_market_cap_usd_m": odds.get("current_market_cap_usd_m"),
            "valuation_basis": odds.get("valuation_horizon", "1-2y_forward"),
            "forward_pe_at_entry": odds.get("forward_pe"),
            "ev_to_sales_at_entry": odds.get("ev_to_sales"),
            "forward_1_2y_valuation_note": fwd_note,
            "long_term_runway_intact": bool(runway_intact),
            "target_upside_multiple": float(upside) if upside is not None else None,
            "management_human_score": int(human_score),
        }
        position = {
            "ticker": sym,
            "company_name": sc.get("company_name", sym),
            "cik": sc.get("cik"),
            "industry_slug": sc.get("industry_slug"),
            "status": "IN_CAN",
            "entry": entry_blk,
            "three_sentence_memo": memo,
            "falsification_lines": falsification_lines,
            "earnings_check_history": {
                "last_checked_accession": None,
                "last_checked_period_end": None,
                "last_traffic_light": "GREEN",
                "checks": [],
            },
        }
        write_yaml(pos_path, position)

        sc["status"] = "APPROVED_FOR_CAN"
        sc["updated_at"] = today_str
        write_yaml(sc_path, sc)

        return {"promoted": True, "ticker": sym, "position": position}

    @app.patch("/api/industries/{slug}/status")
    def update_industry_status(slug: str, payload: IndustryStatusPayload) -> dict[str, Any]:
        ind_path = paths.industries_dir / f"{slug}.yaml"
        if not ind_path.exists():
            raise HTTPException(status_code=404, detail=f"Industry '{slug}' not found")
        rec = read_yaml(ind_path)
        prev = str(rec.get("status", "DORMANT"))
        new_st = payload.status.value
        today_str = datetime.now(UTC).date().isoformat()
        if prev != new_st:
            rec["status"] = new_st
            rec["last_state_change_at"] = today_str
            hist = list(rec.get("history") or [])
            hist.append(
                {
                    "date": today_str,
                    "event": "MANUAL_OVERRIDE",
                    "from_status": prev,
                    "to_status": new_st,
                    "evidence": payload.note,
                }
            )
            rec["history"] = hist
            write_yaml(ind_path, rec)
        return {"updated": True, "industry": rec}

    @app.patch("/api/near-miss/{ticker}/target")
    def update_near_miss_target(ticker: str, payload: WatchlistTargetPayload) -> dict[str, Any]:
        sym = ticker.strip().upper()
        nm_path = paths.near_miss_dir / f"{sym}.yaml"
        if not nm_path.exists():
            raise HTTPException(status_code=404, detail=f"Near-miss item '{sym}' not found")
        rec = read_yaml(nm_path)
        targets = dict(rec.get("targets") or {})
        targets["target_market_cap_usd_m"] = round(payload.target_market_cap_usd_m, 2)
        rec["targets"] = targets
        rec["updated_at"] = datetime.now(UTC).date().isoformat()
        write_yaml(nm_path, rec)
        return {"updated": True, "near_miss": rec}

    def _safe_signal_slug(raw_slug: str) -> str:
        slug = raw_slug.strip().lower()
        if not slug or not all(ch.isalnum() or ch in {"_", "-"} for ch in slug):
            raise HTTPException(status_code=400, detail="Invalid signal slug.")
        return slug

    @app.post("/api/quarantine/signals/{slug}/promote")
    def promote_discovered_signal(slug: str, payload: PromoteSignalPayload) -> dict[str, Any]:
        """Promote a blind-discovery signal from quarantine into memory/industries/<slug>.yaml."""
        clean_slug = _safe_signal_slug(slug)
        signals_path = paths.quarantine_signals_dir / "discovered_themes.yaml"
        if not signals_path.exists():
            raise HTTPException(status_code=404, detail="No discovered_themes.yaml in quarantine.")

        doc = read_yaml(signals_path)
        signals = list(doc.get("signals") or [])
        target = next((s for s in signals if str(s.get("slug", "")).lower() == clean_slug), None)
        if target is None:
            raise HTTPException(
                status_code=404, detail=f"Discovered signal '{clean_slug}' not found."
            )

        ind_path = paths.industries_dir / f"{clean_slug}.yaml"
        today_str = datetime.now(UTC).date().isoformat()
        rules = load_rules(repo_root)
        if ind_path.exists():
            industry_rec = read_yaml(ind_path)
        else:
            industry_rec = build_industry_record_from_signal(
                signal=target,
                initial_status=payload.status,
                today_str=today_str,
                rules=rules,
            )
            write_yaml(ind_path, industry_rec)

        target["status"] = "PROMOTED"
        target["promoted_at"] = today_str
        doc["signals"] = signals
        doc["new_signals_count"] = sum(
            1 for s in signals if str(s.get("status", "NEW")).upper() == "NEW"
        )
        write_yaml(signals_path, doc)
        return {"promoted": True, "industry": industry_rec}

    @app.post("/api/quarantine/signals/{slug}/dismiss")
    def dismiss_discovered_signal(slug: str) -> dict[str, Any]:
        """Dismiss a blind-discovery signal so future weekly scans do not re-flag it."""
        clean_slug = _safe_signal_slug(slug)
        signals_path = paths.quarantine_signals_dir / "discovered_themes.yaml"
        if not signals_path.exists():
            raise HTTPException(status_code=404, detail="No discovered_themes.yaml in quarantine.")

        doc = read_yaml(signals_path)
        signals = list(doc.get("signals") or [])
        target = next((s for s in signals if str(s.get("slug", "")).lower() == clean_slug), None)
        if target is None:
            raise HTTPException(
                status_code=404, detail=f"Discovered signal '{clean_slug}' not found."
            )

        target["status"] = "DISMISSED"
        target["dismissed_at"] = datetime.now(UTC).date().isoformat()
        doc["signals"] = signals
        doc["new_signals_count"] = sum(
            1 for s in signals if str(s.get("status", "NEW")).upper() == "NEW"
        )
        write_yaml(signals_path, doc)
        return {"dismissed": True, "slug": clean_slug}

    if static_web_dir.exists():
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(static_web_dir / "index.html")

        app.mount("/", StaticFiles(directory=str(static_web_dir), html=True), name="web")

    return app


app = create_app()
