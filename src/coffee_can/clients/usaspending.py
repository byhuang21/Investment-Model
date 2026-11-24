"""USASpending.gov free federal contract award API client.

Supports:
- Trailing 12-month (TTM) federal contract spending & YoY growth via `/search/spending_over_time/`
- Top individual contract awards via `/search/spending_by_award/`
- Local disk JSON caching under `.cache/usaspending/`
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests


class UsaSpendingClient:
    """Client for discovering federal/defense/energy contract surges via USASpending.gov."""

    def __init__(
        self,
        config: dict[str, Any],
        project_root: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self.config = config
        self.use_cache = use_cache
        self.base_url = str(
            config.get("base_url", "https://api.usaspending.gov/api/v2")
        ).rstrip("/")
        self.timeout = int(config.get("timeout_seconds", 30))
        self.max_retries = int(config.get("max_retries", 3))
        self.defaults = config.get("default_filters", {})

        cache_rel = config.get("cache_dir", ".cache/usaspending")
        self.cache_dir = (project_root / cache_rel) if project_root else Path(cache_rel)
        self.cache_ttl_seconds = float(config.get("cache_ttl_hours", 24)) * 3600.0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "CoffeeCanResearch/0.1",
            }
        )

    def _cache_key(self, endpoint: str, payload: dict[str, Any]) -> str:
        raw = json.dumps({"endpoint": endpoint, "payload": payload}, sort_keys=True)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        safe_ep = endpoint.strip("/").replace("/", "_")
        return f"{safe_ep}_{digest}"

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        if not self.use_cache:
            return None
        path = self._cache_path(key)
        if not path.exists():
            return None
        if (time.time() - path.stat().st_mtime) > self.cache_ttl_seconds:
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, key: str, data: dict[str, Any]) -> None:
        if not self.use_cache:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(key)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        tmp.replace(path)

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._cache_key(endpoint, payload)
        cached = self._read_cache(key)
        if cached is not None:
            return cached

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        self._write_cache(key, data)
                        return data
                    raise ValueError(f"Unexpected JSON root type from {url}")
                if resp.status_code in {429, 500, 502, 503, 504}:
                    last_err = requests.HTTPError(f"HTTP {resp.status_code} for {url}")
                    time.sleep(min(2.0 * attempt, 6.0))
                    continue
                resp.raise_for_status()
            except (requests.RequestException, ValueError) as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(min(1.5 * attempt, 5.0))
        raise RuntimeError(
            f"USASpending API request failed after {self.max_retries} tries: {last_err}"
        )

    def _build_filters(
        self,
        keywords: list[str],
        start_date: date,
        end_date: date,
        min_amount_usd: float | None = None,
        naics_codes: list[str] | None = None,
        agencies: list[str] | None = None,
    ) -> dict[str, Any]:
        award_types = self.defaults.get("award_type_codes", ["A", "B", "C", "D"])
        filters: dict[str, Any] = {
            "award_type_codes": award_types,
            "time_period": [
                {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                }
            ],
        }
        cleaned_kw = [k.strip() for k in keywords if k and k.strip()]
        if cleaned_kw:
            filters["keywords"] = cleaned_kw
        if min_amount_usd is not None and min_amount_usd > 0:
            filters["award_amounts"] = [{"lower_bound": float(min_amount_usd)}]
        if naics_codes:
            filters["naics_codes"] = {"require": [str(c) for c in naics_codes]}
        if agencies:
            filters["agencies"] = [
                {"type": "awarding", "tier": "toptier", "name": name} for name in agencies
            ]
        return filters

    def search_awards(
        self,
        keywords: list[str],
        min_amount_usd: float | None = None,
        lookback_days: int | None = None,
        naics_codes: list[str] | None = None,
        agencies: list[str] | None = None,
        limit: int = 25,
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """Query top federal contract awards matching industry keywords and filters."""
        if not keywords and not naics_codes:
            return []
        end_d = as_of or datetime.now(UTC).date()
        days = int(lookback_days or self.defaults.get("lookback_days", 180))
        start_d = end_d - timedelta(days=days)
        min_amt = (
            min_amount_usd
            if min_amount_usd is not None
            else float(self.defaults.get("min_award_amount_usd", 10_000_000))
        )
        filters = self._build_filters(
            keywords=keywords,
            start_date=start_d,
            end_date=end_d,
            min_amount_usd=min_amt,
            naics_codes=naics_codes,
            agencies=agencies,
        )
        payload = {
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Awarding Agency",
                "Start Date",
                "Description",
            ],
            "filters": filters,
            "limit": limit,
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
        }
        data = self._post_json("search/spending_by_award/", payload)
        results: list[dict[str, Any]] = []
        for row in data.get("results", []):
            if not isinstance(row, dict):
                continue
            results.append(
                {
                    "award_id": row.get("Award ID"),
                    "recipient_name": row.get("Recipient Name"),
                    "amount_usd": float(row.get("Award Amount") or 0.0),
                    "agency": row.get("Awarding Agency"),
                    "start_date": row.get("Start Date"),
                    "description": row.get("Description"),
                }
            )
        return results

    def _sum_spending_window(
        self,
        keywords: list[str],
        start_date: date,
        end_date: date,
        naics_codes: list[str] | None = None,
        agencies: list[str] | None = None,
    ) -> float:
        filters = self._build_filters(
            keywords=keywords,
            start_date=start_date,
            end_date=end_date,
            min_amount_usd=None,
            naics_codes=naics_codes,
            agencies=agencies,
        )
        payload = {"group": "fiscal_year", "filters": filters}
        data = self._post_json("search/spending_over_time/", payload)
        total = 0.0
        for row in data.get("results", []):
            if isinstance(row, dict) and row.get("aggregated_amount") is not None:
                total += float(row["aggregated_amount"])
        return total

    def get_trailing_12m_spending(
        self,
        keywords: list[str],
        naics_codes: list[str] | None = None,
        agencies: list[str] | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Compute apples-to-apples TTM federal contract volume (USD M) and YoY growth."""
        if not keywords and not naics_codes:
            return {
                "ttm_award_usd_m": 0.0,
                "prior_ttm_award_usd_m": 0.0,
                "yoy_growth": None,
            }
        end_d = as_of or datetime.now(UTC).date()
        cur_start = end_d - timedelta(days=365)
        prior_end = cur_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=365)

        cur_usd = self._sum_spending_window(
            keywords=keywords,
            start_date=cur_start,
            end_date=end_d,
            naics_codes=naics_codes,
            agencies=agencies,
        )
        prior_usd = self._sum_spending_window(
            keywords=keywords,
            start_date=prior_start,
            end_date=prior_end,
            naics_codes=naics_codes,
            agencies=agencies,
        )
        yoy = ((cur_usd / prior_usd) - 1.0) if prior_usd > 0 else None
        return {
            "ttm_award_usd_m": round(cur_usd / 1_000_000.0, 2),
            "prior_ttm_award_usd_m": round(prior_usd / 1_000_000.0, 2),
            "yoy_growth": round(yoy, 4) if yoy is not None else None,
        }
