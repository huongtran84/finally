"""Factory function for selecting the market data source at startup."""
from __future__ import annotations

import os

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource
from app.market.vndirect import VNDirectDataSource

#: Default watchlist — seeded on first database initialisation.
DEFAULT_TICKERS: list[str] = [
    "VNM", "VCB", "VIC", "HPG", "FPT",
    "MWG", "TCB", "VHM", "GAS", "MSN",
]


def create_market_data_source(
    cache: PriceCache,
    initial_tickers: list[str] | None = None,
) -> MarketDataSource:
    """Return the appropriate ``MarketDataSource`` based on environment.

    Selection logic:
    * ``VNSTOCK_API_KEY`` set and non-empty → :class:`VNDirectDataSource`
      (polls the VNDirect finfo-api for real prices)
    * Otherwise → :class:`SimulatorDataSource`
      (generates prices via GBM; works out-of-the-box with no credentials)

    Args:
        cache: Shared :class:`~app.market.cache.PriceCache` instance.
        initial_tickers: List of tickers to track from the start.  Falls back
            to :data:`DEFAULT_TICKERS` when ``None``.

    Returns:
        A concrete :class:`MarketDataSource` ready to be ``start()``ed.
    """
    tickers = initial_tickers if initial_tickers is not None else DEFAULT_TICKERS
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()

    if api_key:
        return VNDirectDataSource(cache, tickers)
    return SimulatorDataSource(cache, tickers)
