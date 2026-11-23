"""USASpending.gov free federal contract award API client skeleton."""

from __future__ import annotations

from typing import Any


class UsaSpendingClient:
    """Client for discovering large federal/defense/energy contract surges."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = config.get("base_url", "https://api.usaspending.gov/api/v2")

    def search_awards(
        self,
        keywords: list[str],
        min_amount_usd: float | None = None,
        lookback_days: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query federal contract awards matching industry keywords and size filters."""
        raise NotImplementedError("Phase 1: implement USASpending /search/spending_by_award/ query")
