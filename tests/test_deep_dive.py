"""Unit tests for Layer 3 SEC Form 4 XML parsing, 3Y capital allocation, and Human Gate guard."""

from __future__ import annotations

from coffee_can.config import load_data_sources
from coffee_can.deep_dive import (
    extract_multiyear_capital_allocation,
    parse_form4_xml,
    render_layer3_evidence_markdown,
    summarize_form4_activity,
    update_scorecard_with_evidence,
)

SAMPLE_FORM4_BUY_AND_TAX = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <aff10b5One>0</aff10b5One>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Founder</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-10</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>50.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>510000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-10</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>50.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

SAMPLE_FORM4_10B5_1_SELL = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <aff10b5One>1</aff10b5One>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>John CFO</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
      <officerTitle>CFO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-15</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>4000</value></transactionShares>
        <transactionPricePerShare><value>75.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_form4_parser_separates_open_market_buys_10b5_1_and_tax_withholding() -> None:
    tx1 = parse_form4_xml(
        SAMPLE_FORM4_BUY_AND_TAX, accession="0001-26-0001", filing_date="2026-08-11"
    )
    tx2 = parse_form4_xml(
        SAMPLE_FORM4_10B5_1_SELL, accession="0001-26-0002", filing_date="2026-09-16"
    )
    summary = summarize_form4_activity(tx1 + tx2, filings_parsed=2)

    assert summary.open_market_buy_count == 1
    assert summary.open_market_buy_usd == 500_000.0
    assert summary.unique_buyers == ["Jane Founder"]
    assert summary.discretionary_sell_count == 0
    assert summary.plan_10b5_1_sell_count == 1
    assert summary.plan_10b5_1_sell_usd == 300_000.0
    assert summary.routine_grant_tax_count == 1  # Code F excluded from open-market sales!
    assert summary.net_open_market_usd == 200_000.0


def test_capital_allocation_and_human_gate_immutability() -> None:
    tags = load_data_sources()["sec_edgar"]["xbrl_tag_fallbacks"]
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2024-01-01",
                                "end": "2024-12-31",
                                "val": 800_000_000,
                                "filed": "2025-02-20",
                                "form": "10-K",
                                "accn": "0001-25-01",
                            },
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 1_000_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-01",
                            },
                        ]
                    }
                },
                "GrossProfit": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 400_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-01",
                            }
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 250_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-01",
                            }
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 50_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-01",
                            }
                        ]
                    }
                },
                "PaymentsForRepurchaseOfCommonStock": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 30_000_000,
                                "filed": "2026-02-20",
                                "form": "10-K",
                                "accn": "0001-26-01",
                            }
                        ]
                    }
                },
            }
        }
    }
    rows = extract_multiyear_capital_allocation(facts, tags, years=3)
    assert len(rows) == 2
    fy25 = rows[-1]
    assert fy25["revenue_usd_m"] == 1000.0
    assert fy25["revenue_yoy_growth"] == 0.25
    assert fy25["fcf_usd_m"] == 200.0
    assert fy25["share_repurchases_usd_m"] == 30.0

    # Verify update_scorecard_with_evidence NEVER overwrites human_subjective_score
    summary = summarize_form4_activity([], filings_parsed=0)
    existing = {
        "ticker": "POWL",
        "company_name": "Powell Industries",
        "status": "DRAFTING_EVIDENCE",
        "dimensions": {
            "4_financial_floor": {"ttm_revenue_usd_m": 1000.0},
            "5_management": {
                "human_subjective_score": None,
                "reviewed_by_human": False,
            },
        },
    }
    updated = update_scorecard_with_evidence(existing, rows, summary, [], "2026-11-24")
    assert updated["status"] == "PENDING_HUMAN_REVIEW"
    assert updated["dimensions"]["5_management"]["human_subjective_score"] is None
    assert updated["dimensions"]["5_management"]["reviewed_by_human"] is False

    md = render_layer3_evidence_markdown(updated, rows, summary, [], "2026-11-24")
    assert "FY2025" in md
    assert "PENDING_HUMAN_REVIEW" in md
