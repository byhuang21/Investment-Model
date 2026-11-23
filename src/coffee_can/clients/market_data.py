"""Market data client backed by yfinance (prices, market cap, sector, SPY/QQQ history)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf


class MarketDataClient:
    """Provides latest quotes, market caps, and benchmark historical prices via yfinance."""

    def __init__(
        self,
        config: dict[str, Any],
        project_root: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self.config = config
        self.use_cache = use_cache
        self.delay_seconds = float(config.get("request_delay_seconds", 0.5))
        cache_rel = config.get("cache_dir", ".cache/yfinance")
        self.cache_dir = (project_root / cache_rel) if project_root else Path(cache_rel)
        self.cache_ttl_seconds = float(config.get("cache_ttl_hours", 4)) * 3600.0
        self._last_request_ts = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request_ts = time.monotonic()

    def _cache_path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
        return self.cache_dir / f"{safe}.json"

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

    def get_quote_snapshot(self, ticker: str) -> dict[str, Any]:
        """Fetch latest price, market cap (USD M), enterprise value (USD M), and sector."""
        symbol = ticker.strip().upper()
        cache_key = f"quote_{symbol}"
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        self._throttle()
        tk = yf.Ticker(symbol)

        price: float | None = None
        market_cap: float | None = None
        enterprise_value: float | None = None
        sector: str | None = None
        industry: str | None = None
        company_name: str | None = None
        currency: str | None = None

        try:
            fast = tk.fast_info
            raw_price = fast.get("lastPrice") or fast.get("previousClose")
            raw_mcap = fast.get("marketCap")
            if raw_price is not None:
                price = float(raw_price)
            if raw_mcap is not None:
                market_cap = float(raw_mcap)
            currency = fast.get("currency")
        except Exception:
            pass

        try:
            info = tk.info or {}
            if price is None and info.get("currentPrice") is not None:
                price = float(info["currentPrice"])
            if price is None and info.get("regularMarketPrice") is not None:
                price = float(info["regularMarketPrice"])
            if market_cap is None and info.get("marketCap") is not None:
                market_cap = float(info["marketCap"])
            if info.get("enterpriseValue") is not None:
                enterprise_value = float(info["enterpriseValue"])
            sector = info.get("sector")
            industry = info.get("industry")
            company_name = info.get("longName") or info.get("shortName")
            currency = currency or info.get("currency")
        except Exception:
            pass

        snapshot = {
            "ticker": symbol,
            "company_name": company_name,
            "sector": sector,
            "industry": industry,
            "currency": currency,
            "price_usd": round(price, 4) if price is not None else None,
            "market_cap_usd_m": (
                round(market_cap / 1_000_000.0, 2) if market_cap is not None else None
            ),
            "enterprise_value_usd_m": (
                round(enterprise_value / 1_000_000.0, 2)
                if enterprise_value is not None
                else None
            ),
        }
        self._write_cache(cache_key, snapshot)
        return snapshot

    def get_latest_price(self, ticker: str) -> float | None:
        """Fetch latest closing/trading price (used for SPY/QQQ benchmark snapshots)."""
        snap = self.get_quote_snapshot(ticker)
        return snap.get("price_usd")

    def get_price_on_or_after(self, ticker: str, date_str: str) -> float | None:
        """Fetch first available closing price on or after YYYY-MM-DD (within 10 calendar days)."""
        symbol = ticker.strip().upper()
        start_dt = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        end_dt = start_dt + timedelta(days=10)
        self._throttle()
        hist = yf.Ticker(symbol).history(start=start_dt.isoformat(), end=end_dt.isoformat())
        if hist is None or hist.empty:
            return None
        return round(float(hist["Close"].iloc[0]), 4)
