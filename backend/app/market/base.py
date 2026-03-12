"""Abstract base class for all market data sources."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.market.models import PriceUpdate


class MarketDataSource(ABC):
    """Unified interface for market data providers.

    Both the GBM simulator and the VNDirect REST poller implement this
    interface so that all downstream code (SSE streaming, trade execution,
    portfolio valuation) is decoupled from the specific data source.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start generating / polling prices."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop generating / polling prices and release resources."""

    @abstractmethod
    def get_price(self, ticker: str) -> PriceUpdate | None:
        """Return the latest price for *ticker*, or ``None`` if not tracked."""

    @abstractmethod
    def get_all_prices(self) -> dict[str, PriceUpdate]:
        """Return the latest prices for all currently-tracked tickers."""

    @abstractmethod
    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        """Return rolling price history for *ticker* (most-recent last)."""

    @abstractmethod
    def get_session_open(self, ticker: str) -> float:
        """Return the session-open price for *ticker* (used for session_change_pct)."""

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """Begin tracking *ticker* immediately."""

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """Stop tracking *ticker* and purge it from the price cache."""

    @abstractmethod
    def is_tracking(self, ticker: str) -> bool:
        """Return ``True`` if *ticker* is currently being tracked."""
