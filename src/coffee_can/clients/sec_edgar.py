"""SEC EDGAR free API client & US-GAAP XBRL fundamental extractor.

Implements SEC fair-access policy:
- Mandatory custom User-Agent header (`SEC_USER_AGENT`)
- Client-side rate limiting (default <= 8 req/s, safely below SEC's 10 req/s ceiling)
- Local disk JSON caching under `.cache/sec_edgar/`
- Multi-tag XBRL stitching for accurate TTM flow metrics, point-in-time balance sheet
  metrics, and YoY share dilution.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

ALLOWED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
ANNUAL_MIN_DAYS = 330
ANNUAL_MAX_DAYS = 385
QUARTER_MIN_DAYS = 70
QUARTER_MAX_DAYS = 110


@dataclass(frozen=True)
class XbrlFact:
    """Single normalized XBRL numeric fact."""

    start: date | None
    end: date
    val: float
    filed: date
    form: str
    accn: str
    tag_priority: int


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


class SecEdgarClient:
    """Rate-limited SEC EDGAR client backed by official free JSON endpoints."""

    def __init__(
        self,
        user_agent: str,
        config: dict[str, Any],
        project_root: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.config = config
        self.use_cache = use_cache
        self.endpoints = config.get("endpoints", {})
        self.timeout = int(config.get("timeout_seconds", 20))
        self.max_retries = int(config.get("max_retries", 3))
        max_rps = float(config.get("max_requests_per_second", 8))
        self._min_interval = 1.0 / max(max_rps, 0.5)
        self._last_request_ts = 0.0

        cache_rel = config.get("cache_dir", ".cache/sec_edgar")
        self.cache_dir = (project_root / cache_rel) if project_root else Path(cache_rel)
        self.cache_ttl_seconds = float(config.get("cache_ttl_hours", 24)) * 3600.0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            }
        )
        self._ticker_map: dict[str, tuple[str, str]] | None = None

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
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
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl_seconds:
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

    def _get_json(self, url: str, cache_key: str) -> dict[str, Any]:
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        self._write_cache(cache_key, payload)
                        return payload
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
        raise RuntimeError(f"SEC EDGAR request failed after {self.max_retries} tries: {last_err}")

    def _load_ticker_map(self) -> dict[str, tuple[str, str]]:
        if self._ticker_map is not None:
            return self._ticker_map
        url = self.endpoints.get(
            "company_tickers", "https://www.sec.gov/files/company_tickers.json"
        )
        raw = self._get_json(url, "company_tickers")
        mapping: dict[str, tuple[str, str]] = {}
        reverse_map: dict[str, str] = {}
        for item in raw.values():
            if not isinstance(item, dict):
                continue
            ticker = _normalize_ticker(str(item.get("ticker", "")))
            cik_num = item.get("cik_str")
            title = str(item.get("title", "")).strip()
            if ticker and cik_num is not None:
                cik10 = f"{int(cik_num):010d}"
                mapping[ticker] = (cik10, title)
                # Keep primary/first ticker (company_tickers.json orders primary class first)
                reverse_map.setdefault(cik10, ticker)
        self._ticker_map = mapping
        self._cik_to_ticker_map = reverse_map
        return mapping

    def lookup_company(self, ticker: str) -> tuple[str, str]:
        """Resolve a US stock ticker to `(cik_10digit, company_title)`."""
        norm = _normalize_ticker(ticker)
        mapping = self._load_ticker_map()
        if norm in mapping:
            return mapping[norm]
        alt = norm.replace("-", "")
        if alt in mapping:
            return mapping[alt]
        raise KeyError(f"Ticker '{ticker}' not found in SEC EDGAR company_tickers.json")

    def lookup_cik(self, ticker: str) -> str:
        """Resolve a US stock ticker to its 10-digit zero-padded SEC CIK."""
        cik, _ = self.lookup_company(ticker)
        return cik

    def lookup_ticker_by_cik(self, cik: str) -> str | None:
        """Resolve a SEC CIK back to its primary US ticker symbol (if listed)."""
        self._load_ticker_map()
        try:
            cik10 = f"{int(str(cik).strip()):010d}"
        except ValueError:
            return None
        return getattr(self, "_cik_to_ticker_map", {}).get(cik10)

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Fetch XBRL CompanyFacts JSON for fundamental financial extraction."""
        cik10 = f"{int(cik):010d}"
        tpl = self.endpoints.get(
            "company_facts", "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        )
        return self._get_json(tpl.format(cik=cik10), f"company_facts_{cik10}")

    def get_submissions(self, cik: str) -> dict[str, Any]:
        """Fetch recent filings metadata (10-K, 10-Q, 8-K, S-1, Form 4)."""
        cik10 = f"{int(cik):010d}"
        tpl = self.endpoints.get(
            "submissions", "https://data.sec.gov/submissions/CIK{cik}.json"
        )
        return self._get_json(tpl.format(cik=cik10), f"submissions_{cik10}")

    def get_latest_periodic_filing(self, cik: str) -> dict[str, Any] | None:
        """Return newest 10-Q or 10-K metadata from SEC submissions JSON."""
        subs = self.get_submissions(cik)
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])

        for idx, form in enumerate(forms):
            form_u = str(form).upper()
            if form_u in ALLOWED_FORMS:
                return {
                    "form": form_u,
                    "accession": accessions[idx] if idx < len(accessions) else None,
                    "filing_date": filing_dates[idx] if idx < len(filing_dates) else None,
                    "report_date": report_dates[idx] if idx < len(report_dates) else None,
                    "primary_document": primary_docs[idx] if idx < len(primary_docs) else None,
                }
        return None

    def _get_text(self, url: str, cache_key: str) -> str:
        """Fetch raw text/XML from SEC EDGAR with rate limiting and local disk cache."""
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in cache_key)
        cache_path = self.cache_dir / f"{safe}.txt"
        if self.use_cache and cache_path.exists():
            if (time.time() - cache_path.stat().st_mtime) <= self.cache_ttl_seconds:
                try:
                    return cache_path.read_text(encoding="utf-8")
                except OSError:
                    pass

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    text = resp.text
                    if self.use_cache:
                        self.cache_dir.mkdir(parents=True, exist_ok=True)
                        tmp = cache_path.with_suffix(".txt.tmp")
                        tmp.write_text(text, encoding="utf-8")
                        tmp.replace(cache_path)
                    return text
                if resp.status_code in {429, 500, 502, 503, 504}:
                    last_err = requests.HTTPError(f"HTTP {resp.status_code} for {url}")
                    time.sleep(min(2.0 * attempt, 6.0))
                    continue
                resp.raise_for_status()
            except requests.RequestException as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(min(1.5 * attempt, 5.0))
        raise RuntimeError(
            f"SEC EDGAR text fetch failed after {self.max_retries} tries: {last_err}"
        )

    def get_recent_form4_filings(
        self,
        cik: str,
        lookback_days: int = 730,
        max_filings: int = 40,
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """List recent SEC Form 4 insider filings within `lookback_days`."""
        subs = self.get_submissions(cik)
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])

        end_d = as_of or datetime.now(UTC).date()
        cutoff = end_d - timedelta(days=lookback_days)
        results: list[dict[str, Any]] = []
        for idx, form in enumerate(forms):
            if str(form).upper() not in {"4", "4/A"}:
                continue
            f_date_str = filing_dates[idx] if idx < len(filing_dates) else ""
            f_date = _parse_iso_date(f_date_str)
            if f_date is None or f_date < cutoff:
                continue
            results.append(
                {
                    "form": str(form).upper(),
                    "filing_date": f_date_str,
                    "accession": accessions[idx] if idx < len(accessions) else "",
                    "primary_document": primary_docs[idx] if idx < len(primary_docs) else "",
                }
            )
            if len(results) >= max_filings:
                break
        return results

    def get_form4_xml(self, cik: str, accession: str, primary_document: str) -> str:
        """Fetch raw Form 4 XML by stripping any XSLT rendering folder prefix."""
        raw_doc = primary_document.split("/")[-1]
        accn_clean = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_clean}/{raw_doc}"
        return self._get_text(url, f"form4_{int(cik)}_{accn_clean}")

    def get_recent_narrative_filings(
        self,
        cik: str,
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Return recent 10-K/10-Q/8-K filings with direct SEC EDGAR archive URLs."""
        subs = self.get_submissions(cik)
        recent = subs.get("filings", {}).get("recent", {})
        all_forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])
        items_list = recent.get("items", [])

        allowed = {f.upper() for f in forms}
        out: list[dict[str, Any]] = []
        for idx, form in enumerate(all_forms):
            fu = str(form).upper()
            if fu not in allowed:
                continue
            accn = accessions[idx] if idx < len(accessions) else ""
            doc = primary_docs[idx] if idx < len(primary_docs) else ""
            accn_clean = accn.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_clean}/{doc}"
                if accn_clean and doc
                else ""
            )
            out.append(
                {
                    "form": fu,
                    "filing_date": filing_dates[idx] if idx < len(filing_dates) else "",
                    "report_date": report_dates[idx] if idx < len(report_dates) else "",
                    "accession": accn,
                    "items": items_list[idx] if idx < len(items_list) else [],
                    "url": url,
                }
            )
            if len(out) >= limit:
                break
        return out

    def search_efts(
        self,
        query: str,
        forms: list[str],
        lookback_days: int = 180,
        limit: int = 25,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Run full-text search across SEC filings via EFTS and resolve matched CIKs to tickers."""
        end_d = as_of or datetime.now(UTC).date()
        start_d = end_d - timedelta(days=lookback_days)
        base_url = self.endpoints.get("efts_search", "https://efts.sec.gov/LATEST/search-index")
        params = {
            "q": f'"{query.strip()}"' if " " in query.strip() else query.strip(),
            "forms": ",".join(forms),
            "dateRange": "custom",
            "startdt": start_d.isoformat(),
            "enddt": end_d.isoformat(),
            "from": 0,
            "size": limit,
        }
        url = f"{base_url}?{urlencode(params)}"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        raw = self._get_json(url, f"efts_{digest}")

        hits_block = raw.get("hits", {})
        total_raw = hits_block.get("total", 0)
        total_hits = (
            int(total_raw.get("value", 0)) if isinstance(total_raw, dict) else int(total_raw)
        )

        parsed_hits: list[dict[str, Any]] = []
        ticker_counts: dict[str, int] = {}
        for item in hits_block.get("hits", []):
            src = item.get("_source", {}) if isinstance(item, dict) else {}
            ciks = src.get("ciks") or []
            cik = str(ciks[0]) if ciks else None
            ticker = self.lookup_ticker_by_cik(cik) if cik else None
            if ticker:
                ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
            parsed_hits.append(
                {
                    "cik": cik,
                    "ticker": ticker,
                    "display_name": (src.get("display_names") or [""])[0],
                    "form": (src.get("root_forms") or [src.get("form", "")])[0],
                    "file_date": src.get("file_date"),
                    "adsh": src.get("adsh") or str(item.get("_id", "")).split(":")[0],
                }
            )

        discovered_tickers = [
            t for t, _ in sorted(ticker_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        return {
            "query": query,
            "total_hits": total_hits,
            "hits": parsed_hits,
            "discovered_tickers": discovered_tickers,
        }

    def discover_s1_clusters(
        self,
        lookback_days: int = 180,
        max_hits: int = 200,
        min_unique_filers: int = 2,
        top_n: int = 8,
        exclude_sic_prefixes: tuple[str, ...] = ("60", "61", "62", "63", "64", "65", "67"),
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """Sweep recent S-1 IPO filings via EFTS and cluster unique filers by 4-digit SIC."""
        end_d = as_of or datetime.now(UTC).date()
        start_d = end_d - timedelta(days=lookback_days)
        base_url = self.endpoints.get("efts_search", "https://efts.sec.gov/LATEST/search-index")

        raw_hits: list[dict[str, Any]] = []
        page_size = min(100, max(1, max_hits))
        offset = 0
        while offset < max_hits:
            params = {
                "forms": "S-1",
                "dateRange": "custom",
                "startdt": start_d.isoformat(),
                "enddt": end_d.isoformat(),
                "from": offset,
                "size": min(page_size, max_hits - offset),
            }
            url = f"{base_url}?{urlencode(params)}"
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            raw = self._get_json(url, f"efts_s1_blind_{digest}")
            hits_block = raw.get("hits", {})
            batch = hits_block.get("hits", [])
            if not batch:
                break
            raw_hits.extend(batch)
            total_raw = hits_block.get("total", 0)
            total_hits = (
                int(total_raw.get("value", 0)) if isinstance(total_raw, dict) else int(total_raw)
            )
            offset += len(batch)
            if offset >= total_hits:
                break

        return cluster_s1_hits_by_sic(
            raw_hits=raw_hits,
            ticker_resolver=self.lookup_ticker_by_cik,
            min_unique_filers=min_unique_filers,
            top_n=top_n,
            exclude_sic_prefixes=exclude_sic_prefixes,
        )


SIC_INDUSTRY_NAMES: dict[str, str] = {
    "1311": "Crude Petroleum & Natural Gas",
    "2834": "Pharmaceutical Preparations",
    "2835": "In Vitro & In Vivo Diagnostic Substances",
    "2836": "Biological Products (Biotech)",
    "3559": "Special Industry Machinery",
    "3571": "Electronic Computers & AI Servers",
    "3576": "Computer Communications Equipment",
    "3577": "Computer Peripheral Equipment",
    "3612": "Power & Distribution Transformers",
    "3613": "Switchgear & Switchboard Apparatus",
    "3663": "Radio & Satellite Communications Equipment",
    "3674": "Semiconductors & Related Devices",
    "3679": "Electronic Components",
    "3690": "Miscellaneous Electrical Machinery & Batteries",
    "3711": "Motor Vehicles & Autonomous Mobility",
    "3721": "Aircraft & Aerospace",
    "3728": "Aircraft Parts & Defense Auxiliary Equipment",
    "3760": "Guided Missiles & Space Vehicles",
    "3812": "Search, Detection, Navigation & Defense Systems",
    "3825": "Instruments for Measuring Electricity & Test",
    "3826": "Laboratory Analytical Instruments",
    "3841": "Surgical & Medical Instruments",
    "3842": "Orthopedic, Prosthetic & Surgical Appliances",
    "3845": "Electromedical & Electrotherapeutic Apparatus",
    "4512": "Air Transportation & Aviation",
    "4899": "Communications & Satellite Services",
    "4911": "Electric Services & Power Generation",
    "4991": "Cogeneration & Next-Gen Power",
    "5812": "Retail Eating Places & Fast-Casual",
    "7370": "Computer Programming & Data Processing",
    "7371": "Custom Computer Programming Services",
    "7372": "Prepackaged Software (Enterprise SaaS)",
    "7373": "Computer Integrated Systems Design",
    "7374": "Computer Processing & Cloud Infrastructure",
    "7389": "Business Services & Digital Marketplaces",
    "8071": "Medical Laboratories & Diagnostics",
    "8090": "Specialty Healthcare Services",
    "8711": "Engineering Services",
    "8731": "Commercial Physical & Biological Research",
}


def cluster_s1_hits_by_sic(
    raw_hits: list[dict[str, Any]],
    ticker_resolver: Any | None = None,
    min_unique_filers: int = 2,
    top_n: int = 8,
    exclude_sic_prefixes: tuple[str, ...] = ("60", "61", "62", "63", "64", "65", "67"),
) -> list[dict[str, Any]]:
    """Group EFTS S-1 hits by 4-digit SIC code, deduplicating multiple S-1/A amendments per CIK."""
    prefixes = tuple(str(p).strip() for p in exclude_sic_prefixes if str(p).strip())
    by_sic: dict[str, dict[str, dict[str, Any]]] = {}

    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        src = item.get("_source", {})
        if not isinstance(src, dict):
            continue
        ciks = src.get("ciks") or []
        sics = src.get("sics") or []
        if not ciks or not sics:
            continue
        cik = str(ciks[0]).zfill(10)
        sic = str(sics[0]).strip()
        if not sic or sic == "6770":
            continue
        if prefixes and any(sic.startswith(pfx) for pfx in prefixes):
            continue

        display_name = str((src.get("display_names") or [""])[0]).strip()
        file_date = str(src.get("file_date") or "")
        bucket = by_sic.setdefault(sic, {})
        if cik not in bucket or file_date > bucket[cik]["file_date"]:
            ticker = ticker_resolver(cik) if callable(ticker_resolver) else None
            bucket[cik] = {
                "cik": cik,
                "display_name": display_name,
                "ticker": ticker,
                "file_date": file_date,
            }

    clusters: list[dict[str, Any]] = []
    for sic, filers_map in by_sic.items():
        if len(filers_map) < min_unique_filers:
            continue
        filers = sorted(filers_map.values(), key=lambda f: f["file_date"], reverse=True)
        tickers = [f["ticker"] for f in filers if f.get("ticker")]
        clusters.append(
            {
                "sic_code": sic,
                "sic_name": SIC_INDUSTRY_NAMES.get(sic, f"SIC {sic}"),
                "unique_filers": len(filers),
                "filers": [f["display_name"] for f in filers[:6] if f.get("display_name")],
                "discovered_tickers": list(dict.fromkeys(tickers)),
            }
        )

    clusters.sort(key=lambda c: (-c["unique_filers"], c["sic_code"]))
    return clusters[:top_n]



# =============================================================================
# US-GAAP XBRL Fundamental Extraction Helpers (Pure & Unit-Testable)
# =============================================================================


def _collect_facts(
    company_facts: dict[str, Any],
    tags: list[str],
    unit_key: str = "USD",
    namespaces: tuple[str, ...] = ("us-gaap",),
    require_start: bool = True,
) -> list[XbrlFact]:
    """Collect and deduplicate facts across prioritized XBRL tags.

    When multiple filings or tags report the same `(start, end)` period, prefers:
    1. Most recent `filed` date (captures restatements/10-K over 10-Q)
    2. Higher-priority tag (lower `tag_priority` index)
    """
    facts_root = company_facts.get("facts", {})
    by_period: dict[tuple[date | None, date], XbrlFact] = {}

    for priority, tag in enumerate(tags):
        for ns in namespaces:
            tag_obj = facts_root.get(ns, {}).get(tag)
            if not isinstance(tag_obj, dict):
                continue
            unit_entries = tag_obj.get("units", {}).get(unit_key, [])
            for raw in unit_entries:
                form = str(raw.get("form", "")).upper()
                if form not in ALLOWED_FORMS:
                    continue
                val = raw.get("val")
                if val is None:
                    continue
                end_d = _parse_iso_date(raw.get("end"))
                filed_d = _parse_iso_date(raw.get("filed"))
                start_d = _parse_iso_date(raw.get("start"))
                if end_d is None or filed_d is None:
                    continue
                if require_start and start_d is None:
                    continue
                if not require_start and start_d is not None:
                    continue
                # Guard against 10-Q filings with erroneous full-year duration tags
                # overwriting real 10-K annual figures (e.g. FIX Q1 2026 10-Q quirk).
                if (
                    start_d is not None
                    and form in {"10-Q", "10-Q/A"}
                    and ANNUAL_MIN_DAYS <= (end_d - start_d).days <= ANNUAL_MAX_DAYS
                ):
                    continue

                fact = XbrlFact(
                    start=start_d,
                    end=end_d,
                    val=float(val),
                    filed=filed_d,
                    form=form,
                    accn=str(raw.get("accn", "")),
                    tag_priority=priority,
                )
                key = (start_d, end_d)
                prev = by_period.get(key)
                if prev is None or (fact.filed, -fact.tag_priority) > (
                    prev.filed,
                    -prev.tag_priority,
                ):
                    by_period[key] = fact

    return sorted(by_period.values(), key=lambda f: (f.end, f.filed))


def _duration_days(fact: XbrlFact) -> int:
    if fact.start is None:
        return 0
    return (fact.end - fact.start).days


def _find_matching_prior_period(
    facts: list[XbrlFact],
    target_end: date,
    target_days: int,
    tolerance_days: int = 22,
) -> XbrlFact | None:
    """Find a fact ending ~1 year before `target_end` with similar duration."""
    best: XbrlFact | None = None
    best_dist = 9999
    for fact in facts:
        end_gap = (target_end - fact.end).days
        if not (340 <= end_gap <= 390):
            continue
        dur_gap = abs(_duration_days(fact) - target_days)
        if dur_gap > tolerance_days:
            continue
        dist = abs(end_gap - 365) + dur_gap
        if dist < best_dist:
            best = fact
            best_dist = dist
    return best


def _sum_four_quarters(facts: list[XbrlFact], target_end: date) -> float | None:
    """Fallback TTM: chain 4 consecutive non-overlapping quarterly facts ending at `target_end`."""
    quarters = [
        f for f in facts if QUARTER_MIN_DAYS <= _duration_days(f) <= QUARTER_MAX_DAYS
    ]
    current_end = target_end
    picked: list[XbrlFact] = []
    for _ in range(4):
        matches = [f for f in quarters if abs((current_end - f.end).days) <= 7]
        if not matches:
            return None
        chosen = max(matches, key=lambda f: (f.filed, -f.tag_priority))
        picked.append(chosen)
        if chosen.start is None:
            return None
        current_end = chosen.start
    total_span = (picked[0].end - picked[-1].start).days if picked[-1].start else 0
    if not (ANNUAL_MIN_DAYS <= total_span <= ANNUAL_MAX_DAYS):
        return None
    return sum(f.val for f in picked)


def compute_ttm_series(
    facts: list[XbrlFact],
) -> tuple[float | None, float | None, date | None, str | None]:
    """Compute `(latest_ttm, prior_year_ttm, latest_end, latest_accn)` from duration facts."""
    if not facts:
        return None, None, None, None

    annuals = [
        f
        for f in facts
        if ANNUAL_MIN_DAYS <= _duration_days(f) <= ANNUAL_MAX_DAYS
        and f.form in {"10-K", "10-K/A"}
    ]
    latest_end = max(f.end for f in facts)
    latest_candidates = [f for f in facts if f.end == latest_end]
    # Prefer longest duration ending on latest_end (10-K annual > YTD 9M > YTD 6M > Q)
    latest_anchor = max(latest_candidates, key=lambda f: (_duration_days(f), f.filed))
    latest_accn = latest_anchor.accn

    def _ttm_at(anchor_end: date) -> float | None:
        # 1. Exact annual 10-K ending on anchor_end
        ann_exact = [f for f in annuals if abs((anchor_end - f.end).days) <= 7]
        if ann_exact:
            return max(ann_exact, key=lambda f: (f.filed, -f.tag_priority)).val

        # 2. Mid-year 10-Q: Annual(prior FY) + YTD(current) - YTD(prior year)
        prior_annuals = [f for f in annuals if 30 <= (anchor_end - f.end).days <= 330]
        if prior_annuals:
            base_annual = max(prior_annuals, key=lambda f: f.end)
            ytd_candidates = [
                f
                for f in facts
                if abs((anchor_end - f.end).days) <= 7
                and f.start is not None
                and abs((f.start - base_annual.end).days) <= 15
                and _duration_days(f) < ANNUAL_MIN_DAYS
            ]
            if ytd_candidates:
                cur_ytd = max(ytd_candidates, key=lambda f: (_duration_days(f), f.filed))
                prev_ytd = _find_matching_prior_period(
                    facts, cur_ytd.end, _duration_days(cur_ytd)
                )
                if prev_ytd is not None:
                    return base_annual.val + cur_ytd.val - prev_ytd.val

        # 3. Fallback: chain 4 consecutive standalone quarters
        return _sum_four_quarters(facts, anchor_end)

    latest_ttm = _ttm_at(latest_end)

    # Prior-year TTM (~365 days before latest_end)
    prior_end_candidates = [
        f.end for f in facts if 340 <= (latest_end - f.end).days <= 390
    ]
    prior_ttm: float | None = None
    if prior_end_candidates:
        prior_end = min(
            set(prior_end_candidates), key=lambda d: abs((latest_end - d).days - 365)
        )
        prior_ttm = _ttm_at(prior_end)

    return latest_ttm, prior_ttm, latest_end, latest_accn


def _latest_instant_fact(
    facts: list[XbrlFact],
    anchor_end: date | None = None,
    max_stale_days: int = 45,
) -> XbrlFact | None:
    """Return latest balance sheet fact, rejecting stale tags older than `max_stale_days`."""
    if not facts:
        return None
    latest = max(facts, key=lambda f: (f.end, f.filed, -f.tag_priority))
    if anchor_end is not None and (anchor_end - latest.end).days > max_stale_days:
        return None
    return latest


def compute_annual_share_dilution(
    company_facts: dict[str, Any],
    share_tags: list[str],
) -> float | None:
    """Estimate YoY share count growth using split-adjusted same-filing comparatives.

    Priority:
    1. `us-gaap:WeightedAverageNumberOfSharesOutstandingBasic` (retroactively split-adjusted
       under ASC 260 AND immune to GAAP loss-quarter anti-dilutive swings)
    2. `us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding`
    3. `dei:EntityCommonStockSharesOutstanding` (last-resort cover-page fallback)
    """
    ordered_gaap_tags = [
        t for t in share_tags if t != "EntityCommonStockSharesOutstanding"
    ]
    for tag in ordered_gaap_tags:
        gaap_facts = _collect_facts(
            company_facts,
            tags=[tag],
            unit_key="shares",
            namespaces=("us-gaap",),
            require_start=True,
        )
        if not gaap_facts:
            continue
        latest_end = max(f.end for f in gaap_facts)
        at_latest = [f for f in gaap_facts if f.end == latest_end]
        cur = min(
            at_latest,
            key=lambda f: (
                0
                if (
                    QUARTER_MIN_DAYS <= _duration_days(f) <= QUARTER_MAX_DAYS
                    or ANNUAL_MIN_DAYS <= _duration_days(f) <= ANNUAL_MAX_DAYS
                )
                else 1,
                -_duration_days(f),
            ),
        )
        # Prefer prior-year comparative reported in the SAME filing (guaranteed split-adjusted)
        same_filing_facts = [f for f in gaap_facts if f.accn == cur.accn]
        prev = _find_matching_prior_period(
            same_filing_facts, cur.end, _duration_days(cur)
        )
        if prev is None:
            prev = _find_matching_prior_period(gaap_facts, cur.end, _duration_days(cur))
        if prev is not None and prev.val > 0:
            return (cur.val / prev.val) - 1.0

    # Last-resort fallback: DEI cover-page shares (note: not retroactively split-adjusted)
    dei_facts = _collect_facts(
        company_facts,
        tags=["EntityCommonStockSharesOutstanding"],
        unit_key="shares",
        namespaces=("dei",),
        require_start=False,
    )
    if dei_facts:
        latest = max(dei_facts, key=lambda f: (f.end, f.filed))
        prior_candidates = [
            f for f in dei_facts if 300 <= (latest.end - f.end).days <= 430 and f.val > 0
        ]
        if prior_candidates:
            prior = min(
                prior_candidates, key=lambda f: abs((latest.end - f.end).days - 365)
            )
            days = (latest.end - prior.end).days
            raw_growth = (latest.val / prior.val) - 1.0
            return ((1.0 + raw_growth) ** (365.0 / days)) - 1.0

    return None


def extract_fundamentals_from_company_facts(
    company_facts: dict[str, Any],
    xbrl_tag_fallbacks: dict[str, list[str]],
) -> dict[str, Any]:
    """Extract normalized TTM & balance-sheet metrics (in USD millions & ratios) from XBRL JSON."""
    rev_facts = _collect_facts(
        company_facts, xbrl_tag_fallbacks.get("revenue", []), unit_key="USD", require_start=True
    )
    rev_ttm, rev_prior_ttm, rev_end, rev_accn = compute_ttm_series(rev_facts)

    gp_facts = _collect_facts(
        company_facts,
        xbrl_tag_fallbacks.get("gross_profit", []),
        unit_key="USD",
        require_start=True,
    )
    gp_ttm, gp_prior_ttm, gp_end, _ = compute_ttm_series(gp_facts)

    # Guard against stale GrossProfit tags when a company switched reporting structure
    if rev_end is not None and gp_end is not None and abs((rev_end - gp_end).days) > 45:
        gp_ttm = None
        gp_prior_ttm = None

    # Fallback: Gross Profit = Revenue - Cost of Revenue when GrossProfit tag is omitted or stale
    if gp_ttm is None and rev_ttm is not None:
        cor_facts = _collect_facts(
            company_facts,
            xbrl_tag_fallbacks.get("cost_of_revenue", []),
            unit_key="USD",
            require_start=True,
        )
        cor_ttm, cor_prior_ttm, cor_end, _ = compute_ttm_series(cor_facts)
        cor_fresh = (
            rev_end is None or cor_end is None or abs((rev_end - cor_end).days) <= 45
        )
        if cor_ttm is not None and cor_fresh:
            gp_ttm = rev_ttm - cor_ttm
        if (
            gp_prior_ttm is None
            and cor_fresh
            and rev_prior_ttm is not None
            and cor_prior_ttm is not None
        ):
            gp_prior_ttm = rev_prior_ttm - cor_prior_ttm

    ocf_facts = _collect_facts(
        company_facts,
        xbrl_tag_fallbacks.get("operating_cash_flow", []),
        unit_key="USD",
        require_start=True,
    )
    ocf_ttm, _, ocf_end, ocf_accn = compute_ttm_series(ocf_facts)

    capex_facts = _collect_facts(
        company_facts, xbrl_tag_fallbacks.get("capex", []), unit_key="USD", require_start=True
    )
    capex_ttm, _, _, _ = compute_ttm_series(capex_facts)

    fcf_ttm: float | None = None
    if ocf_ttm is not None:
        fcf_ttm = ocf_ttm - abs(capex_ttm) if capex_ttm is not None else ocf_ttm

    # Balance Sheet anchor date (latest Assets or Cash date)
    assets_facts = _collect_facts(
        company_facts,
        xbrl_tag_fallbacks.get("total_assets", []),
        unit_key="USD",
        require_start=False,
    )
    cash_facts = _collect_facts(
        company_facts,
        xbrl_tag_fallbacks.get("cash_and_equivalents", []),
        unit_key="USD",
        require_start=False,
    )
    latest_assets = _latest_instant_fact(assets_facts)
    latest_cash = _latest_instant_fact(cash_facts)
    bs_anchor = max(
        (d for d in (latest_assets.end if latest_assets else None,
                     latest_cash.end if latest_cash else None) if d is not None),
        default=None,
    )

    st_inv_fact = _latest_instant_fact(
        _collect_facts(
            company_facts,
            xbrl_tag_fallbacks.get("short_term_investments", []),
            unit_key="USD",
            require_start=False,
        ),
        anchor_end=bs_anchor,
    )
    st_debt_fact = _latest_instant_fact(
        _collect_facts(
            company_facts,
            xbrl_tag_fallbacks.get("short_term_debt", []),
            unit_key="USD",
            require_start=False,
        ),
        anchor_end=bs_anchor,
    )
    lt_debt_fact = _latest_instant_fact(
        _collect_facts(
            company_facts,
            xbrl_tag_fallbacks.get("long_term_debt", []),
            unit_key="USD",
            require_start=False,
        ),
        anchor_end=bs_anchor,
    )
    goodwill_fact = _latest_instant_fact(
        _collect_facts(
            company_facts,
            xbrl_tag_fallbacks.get("goodwill", []),
            unit_key="USD",
            require_start=False,
        ),
        anchor_end=bs_anchor,
    )

    cash_usd = (latest_cash.val if latest_cash else 0.0) + (
        st_inv_fact.val if st_inv_fact else 0.0
    )
    has_cash_data = latest_cash is not None or st_inv_fact is not None
    st_debt_usd = st_debt_fact.val if st_debt_fact else 0.0
    lt_debt_usd = lt_debt_fact.val if lt_debt_fact else 0.0
    total_debt_usd = st_debt_usd + lt_debt_usd

    # Ratios & derived metrics
    rev_yoy: float | None = None
    if rev_ttm is not None and rev_prior_ttm is not None and rev_prior_ttm > 0:
        rev_yoy = (rev_ttm / rev_prior_ttm) - 1.0

    gross_margin: float | None = None
    if gp_ttm is not None and rev_ttm is not None and rev_ttm > 0:
        gross_margin = gp_ttm / rev_ttm

    gross_margin_yoy_change: float | None = None
    if (
        gross_margin is not None
        and gp_prior_ttm is not None
        and rev_prior_ttm is not None
        and rev_prior_ttm > 0
    ):
        prior_gm = gp_prior_ttm / rev_prior_ttm
        gross_margin_yoy_change = gross_margin - prior_gm

    cash_runway_months: float | None = None
    if has_cash_data and fcf_ttm is not None and fcf_ttm < 0:
        monthly_burn = abs(fcf_ttm) / 12.0
        cash_runway_months = (cash_usd / monthly_burn) if monthly_burn > 0 else None

    short_term_debt_to_cash: float | None = None
    if has_cash_data:
        if st_debt_usd <= 0:
            short_term_debt_to_cash = 0.0
        elif cash_usd > 0:
            short_term_debt_to_cash = st_debt_usd / cash_usd
        else:
            short_term_debt_to_cash = float("inf")

    goodwill_to_total_assets: float | None = None
    if latest_assets is not None and latest_assets.val > 0:
        goodwill_to_total_assets = (
            goodwill_fact.val / latest_assets.val if goodwill_fact else 0.0
        )

    annual_share_dilution = compute_annual_share_dilution(
        company_facts, xbrl_tag_fallbacks.get("diluted_shares", [])
    )

    latest_period_end = max(
        (d for d in (rev_end, ocf_end, bs_anchor) if d is not None), default=None
    )
    filing_accession = (
        rev_accn
        or ocf_accn
        or (latest_assets.accn if latest_assets else None)
        or (latest_cash.accn if latest_cash else None)
    )

    to_m = lambda v: round(v / 1_000_000.0, 2) if v is not None else None  # noqa: E731

    return {
        "ttm_revenue_usd_m": to_m(rev_ttm),
        "revenue_yoy_growth": round(rev_yoy, 4) if rev_yoy is not None else None,
        "gross_margin": round(gross_margin, 4) if gross_margin is not None else None,
        "gross_margin_yoy_change": (
            round(gross_margin_yoy_change, 4)
            if gross_margin_yoy_change is not None
            else None
        ),
        "ocf_usd_m": to_m(ocf_ttm),
        "fcf_usd_m": to_m(fcf_ttm),
        "cash_and_st_inv_usd_m": to_m(cash_usd) if has_cash_data else None,
        "total_debt_usd_m": to_m(total_debt_usd) if (st_debt_fact or lt_debt_fact) else 0.0,
        "cash_runway_months": (
            round(cash_runway_months, 1) if cash_runway_months is not None else None
        ),
        "short_term_debt_to_cash": (
            round(short_term_debt_to_cash, 4)
            if short_term_debt_to_cash is not None and short_term_debt_to_cash != float("inf")
            else short_term_debt_to_cash
        ),
        "goodwill_to_total_assets": (
            round(goodwill_to_total_assets, 4)
            if goodwill_to_total_assets is not None
            else None
        ),
        "annual_share_dilution": (
            round(annual_share_dilution, 4) if annual_share_dilution is not None else None
        ),
        "latest_period_end": latest_period_end.isoformat() if latest_period_end else None,
        "filing_accession": filing_accession,
    }


def extract_capex_growth(
    company_facts: dict[str, Any],
    xbrl_tag_fallbacks: dict[str, list[str]],
) -> dict[str, Any]:
    """Extract TTM CapEx (USD M) and YoY growth for bellwether capital-spending radar."""
    capex_facts = _collect_facts(
        company_facts,
        xbrl_tag_fallbacks.get("capex", []),
        unit_key="USD",
        require_start=True,
    )
    ttm, prior, latest_end, accn = compute_ttm_series(capex_facts)
    ttm_abs = abs(ttm) if ttm is not None else None
    prior_abs = abs(prior) if prior is not None else None
    yoy = (
        ((ttm_abs / prior_abs) - 1.0)
        if (ttm_abs is not None and prior_abs and prior_abs > 0)
        else None
    )
    return {
        "ttm_capex_usd_m": round(ttm_abs / 1_000_000.0, 2) if ttm_abs is not None else None,
        "prior_ttm_capex_usd_m": (
            round(prior_abs / 1_000_000.0, 2) if prior_abs is not None else None
        ),
        "capex_yoy_growth": round(yoy, 4) if yoy is not None else None,
        "latest_period_end": latest_end.isoformat() if latest_end else None,
        "filing_accession": accn,
    }
