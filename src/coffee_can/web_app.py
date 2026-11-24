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


def create_app(root: Path | None = None) -> FastAPI:
    """Create a configured FastAPI instance bound to `root` (defaults to repo root)."""
    repo_root = (root or get_project_root()).resolve()
    paths = ProjectPaths.from_root(repo_root)
    web_dir = repo_root / "web"

    app = FastAPI(
        title="Coffee Can Research Workbench API",
        version="0.2.0",
        description="Local read/write workbench over Git-backed Coffee Can memory/ files.",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "live-rw"}

    @app.get("/api/snapshot")
    def get_snapshot() -> dict[str, Any]:
        return build_workbench_snapshot(repo_root)

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
        min_upside = float(rules["layer3_deep_dive"]["min_target_upside_multiple"])
        min_fl = int(rules["layer3_deep_dive"].get("required_falsification_lines_min", 1))
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
        if upside is None or float(upside) < min_upside:
            raise HTTPException(
                status_code=400,
                detail=f"10x Odds Veto: implied_upside_multiple must be >= {min_upside:g}x.",
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
        position = {
            "ticker": sym,
            "company_name": sc.get("company_name", sym),
            "cik": sc.get("cik"),
            "industry_slug": sc.get("industry_slug"),
            "status": "IN_CAN",
            "entry": {
                "entered_at": today_str,
                "entry_price_usd": None,
                "entry_market_cap_usd_m": odds.get("current_market_cap_usd_m"),
                "target_upside_multiple": float(upside),
                "management_human_score": int(human_score),
            },
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

    if web_dir.exists():
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
