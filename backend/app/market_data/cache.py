import asyncio
from collections import deque
from datetime import datetime

from .models import PriceTick, PriceSnapshot, PriceHistory


class PriceCache:
    """
    Thread-safe in-memory cache for live price data.

    Stores:
    - Latest PriceTick per ticker (for SSE push and current-price lookups)
    - Session start price per ticker (for session_change_pct)
    - Rolling history of last 50 prices per ticker (for sparkline bootstrap)
    - A version counter that increments on any update (for SSE change detection)
    """

    HISTORY_SIZE = 50

    def __init__(self) -> None:
        self._prices: dict[str, PriceTick] = {}
        self._session_start: dict[str, float] = {}
        self._history: dict[str, deque[PriceSnapshot]] = {}
        self._version: int = 0
        self._lock = asyncio.Lock()

    async def update(self, ticker: str, price: float, timestamp: datetime) -> PriceTick:
        """
        Record a new price for a ticker. Returns the generated PriceTick.

        If this is the first price for the ticker, it becomes the session
        start price and previous_price is set equal to price (direction=flat).
        """
        async with self._lock:
            previous = self._prices.get(ticker)
            previous_price = previous.price if previous else price

            # Session start price: set once, never changes
            if ticker not in self._session_start:
                self._session_start[ticker] = price

            session_start = self._session_start[ticker]
            session_change_pct = ((price - session_start) / session_start) * 100

            tick = PriceTick(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=timestamp,
                direction=PriceTick.compute_direction(price, previous_price),
                session_change_pct=round(session_change_pct, 4),
            )

            self._prices[ticker] = tick

            # Append to rolling history
            if ticker not in self._history:
                self._history[ticker] = deque(maxlen=self.HISTORY_SIZE)
            self._history[ticker].append(
                PriceSnapshot(price=round(price, 2), timestamp=timestamp)
            )

            self._version += 1
            return tick

    async def get_latest(self, ticker: str) -> PriceTick | None:
        """Get the most recent PriceTick for a ticker, or None."""
        async with self._lock:
            return self._prices.get(ticker)

    async def get_all_latest(self) -> dict[str, PriceTick]:
        """Get a snapshot of all latest prices."""
        async with self._lock:
            return dict(self._prices)

    async def get_price(self, ticker: str) -> float | None:
        """Get just the current price for a ticker. Used by trade execution."""
        async with self._lock:
            tick = self._prices.get(ticker)
            return tick.price if tick else None

    async def get_history(self, ticker: str) -> PriceHistory | None:
        """Get rolling price history for one ticker."""
        async with self._lock:
            if ticker not in self._history:
                return None
            return PriceHistory(
                ticker=ticker,
                prices=list(self._history[ticker]),
                session_start_price=self._session_start.get(ticker, 0),
            )

    async def get_all_history(self) -> dict[str, PriceHistory]:
        """Get rolling price history for all tracked tickers."""
        async with self._lock:
            result = {}
            for ticker in self._history:
                result[ticker] = PriceHistory(
                    ticker=ticker,
                    prices=list(self._history[ticker]),
                    session_start_price=self._session_start.get(ticker, 0),
                )
            return result

    async def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache entirely."""
        async with self._lock:
            self._prices.pop(ticker, None)
            self._session_start.pop(ticker, None)
            self._history.pop(ticker, None)
            self._version += 1

    @property
    def version(self) -> int:
        """
        Monotonically increasing counter. SSE uses this to detect
        whether any prices have changed since last push.
        """
        return self._version
