"""Unit tests for SEC EDGAR XBRL TTM, balance-sheet staleness, and dilution extraction."""

from __future__ import annotations

from coffee_can.clients.sec_edgar import extract_fundamentals_from_company_facts
from coffee_can.config import load_data_sources


def _make_sample_company_facts() -> dict:
    """Synthetic SEC CompanyFacts with FY2024, FY2025 10-K + Q2 2025/2026 10-Q YTD."""
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            # FY2024 annual
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 800_000_000,
                                "filed": "2025-02-20",
                                "form": "10-K",
                                "accn": "0001-25-000001",
                            },
                            # FY2025 annual
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 1_000_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-000001",
                            },
                            # 6M 2024 YTD (for prior TTM computation)
                            {
                                "start": "2024-01-01",
                                "end": "2024-06-30",
                                "val": 380_000_000,
                                "filed": "2025-08-05",
                                "form": "10-Q",
                                "accn": "0001-25-000002",
                            },
                            # 6M 2025 YTD
                            {
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "val": 480_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                            # 6M 2026 YTD -> TTM = 1000 + 620 - 480 = 1140M
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 620_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                        ]
                    }
                },
                "GrossProfit": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 320_000_000,
                                "filed": "2025-02-20",
                                "form": "10-K",
                                "accn": "0001-25-000001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 450_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-000001",
                            },
                            {
                                "start": "2024-01-01",
                                "end": "2024-06-30",
                                "val": 150_000_000,
                                "filed": "2025-08-05",
                                "form": "10-Q",
                                "accn": "0001-25-000002",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "val": 210_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 290_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": -40_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-000001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "val": -20_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": -25_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 15_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-000001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-06-30",
                                "val": 7_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 7_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                        ]
                    }
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-06-30",
                                "val": 180_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            }
                        ]
                    }
                },
                # Stale ShortTermInvestments from 2021 should be ignored!
                "ShortTermInvestments": {
                    "units": {
                        "USD": [
                            {
                                "end": "2021-12-31",
                                "val": 999_000_000,
                                "filed": "2022-02-20",
                                "form": "10-K",
                                "accn": "0001-22-000001",
                            }
                        ]
                    }
                },
                "ShortTermBorrowings": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-06-30",
                                "val": 36_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            }
                        ]
                    }
                },
                "Goodwill": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-06-30",
                                "val": 100_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            }
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-06-30",
                                "val": 1_000_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            }
                        ]
                    }
                },
                "WeightedAverageNumberOfDilutedSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "start": "2025-04-01",
                                "end": "2025-06-30",
                                "val": 100_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                            {
                                "start": "2026-04-01",
                                "end": "2026-06-30",
                                "val": 103_000_000,
                                "filed": "2026-08-05",
                                "form": "10-Q",
                                "accn": "0001-26-000002",
                            },
                        ]
                    }
                },
            }
        }
    }


def test_xbrl_ttm_and_stale_balance_sheet_filtering() -> None:
    tags = load_data_sources()["sec_edgar"]["xbrl_tag_fallbacks"]
    result = extract_fundamentals_from_company_facts(_make_sample_company_facts(), tags)

    # TTM Revenue = 1000 + 620 - 480 = 1140M; Prior TTM = 800 + 480 - 380 = 900M
    assert result["ttm_revenue_usd_m"] == 1140.0
    assert result["revenue_yoy_growth"] == round((1140.0 / 900.0) - 1.0, 4)

    # TTM Gross Profit = 450 + 290 - 210 = 530M
    assert result["gross_margin"] == round(530.0 / 1140.0, 4)

    # TTM OCF = -40 + (-25) - (-20) = -45M; CapEx = 15M -> FCF = -60M
    assert result["ocf_usd_m"] == -45.0
    assert result["fcf_usd_m"] == -60.0

    # Stale 2021 ShortTermInvestments ($999M) MUST NOT be added to 2026 Cash ($180M)
    assert result["cash_and_st_inv_usd_m"] == 180.0
    # Runway = $180M / ($60M / 12) = 36.0 months
    assert result["cash_runway_months"] == 36.0
    # Short-term debt / cash = 36 / 180 = 0.20
    assert result["short_term_debt_to_cash"] == 0.2
    # Goodwill / Assets = 100 / 1000 = 0.10
    assert result["goodwill_to_total_assets"] == 0.1
    # Share dilution = 103M / 100M - 1 = 3.0%
    assert result["annual_share_dilution"] == 0.03
    assert result["filing_accession"] == "0001-26-000002"


def test_stale_gross_profit_falls_back_to_cor_and_dei_prevents_antidilutive_spike() -> None:
    tags = load_data_sources()["sec_edgar"]["xbrl_tag_fallbacks"]
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 1_000_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            }
                        ]
                    }
                },
                # Stale 2023 GrossProfit should be discarded in favor of 2025 CostOfRevenue
                "GrossProfit": {
                    "units": {
                        "USD": [
                            {
                                "start": "2023-01-01",
                                "end": "2023-12-31",
                                "val": 120_000_000,
                                "filed": "2024-02-20",
                                "form": "10-K",
                                "accn": "0002-24-000001",
                            }
                        ]
                    }
                },
                "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 750_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            }
                        ]
                    }
                },
                # Split-adjusted Basic shares in the same 10-K (+3% true dilution after 3-for-1)
                "WeightedAverageNumberOfSharesOutstandingBasic": {
                    "units": {
                        "shares": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 300_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 309_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            },
                        ]
                    }
                },
                # Simulated anti-dilutive spike (+10%) on Diluted
                "WeightedAverageNumberOfDilutedSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 300_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 330_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            },
                        ]
                    }
                },
            },
            # Unadjusted DEI cover-page shares showing +209% distortion from 3-for-1 split
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2025-02-15",
                                "val": 100_000_000,
                                "filed": "2025-02-20",
                                "form": "10-K",
                                "accn": "0002-25-000001",
                            },
                            {
                                "end": "2026-02-15",
                                "val": 309_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0002-26-000001",
                            },
                        ]
                    }
                }
            },
        }
    }
    res = extract_fundamentals_from_company_facts(facts, tags)
    assert res["gross_margin"] == 0.25
    assert res["annual_share_dilution"] == 0.03


def test_10q_erroneous_annual_duration_does_not_overwrite_10k() -> None:
    tags = load_data_sources()["sec_edgar"]["xbrl_tag_fallbacks"]
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 9_000_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0003-26-000001",
                            },
                            # Corrupted later 10-Q entry tagged as 364 days must be ignored
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 1_800_000_000,
                                "filed": "2026-05-01",
                                "form": "10-Q",
                                "accn": "0003-26-000002",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-03-31",
                                "val": 1_800_000_000,
                                "filed": "2026-05-01",
                                "form": "10-Q",
                                "accn": "0003-26-000002",
                            },
                            {
                                "start": "2026-01-01",
                                "end": "2026-03-31",
                                "val": 2_800_000_000,
                                "filed": "2026-05-01",
                                "form": "10-Q",
                                "accn": "0003-26-000002",
                            },
                        ]
                    }
                }
            }
        }
    }
    res = extract_fundamentals_from_company_facts(facts, tags)
    # TTM = 9,000 + 2,800 - 1,800 = 10,000M
    assert res["ttm_revenue_usd_m"] == 10000.0
