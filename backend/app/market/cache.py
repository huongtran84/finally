"""Thread-safe in-memory price cache shared by all market data sources."""
from __future__ import annotations

import threading
from collections import deque

from app.market.models import PriceUpdate


class PriceCache:
    """
    Thread-safe in-memory price cache.

    - Holds the latest price for every tracked ticker.
    - Maintains a rolling history of the last `history_size` updates per ticker
      so sparklines can be bootstrapped on page load.
    - Tracks the session-open price per ticker for session_change_pct calculation.
    """

    def __init__(self, history_size: int = 50) -> None:
        self._history_size = history_size
        self._prices: dict[str, PriceUpdate] = {}
        self._history: dict[str, deque[PriceUpdate]] = {}
        self._session_open: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def update(self, update: PriceUpdate) -> None:
        """Store a new price update. Initialises history/session_open on first write."""
        with self._lock:
            ticker = update.ticker
            if ticker not in self._history:
                self._history[ticker] = deque(maxlen=self._history_size)
            if ticker not in self._session_open:
                self._session_open[ticker] = update.price
            self._prices[ticker] = update
            self._history[ticker].append(update)

    def remove(self, ticker: str) -> None:
        """Remove all data for a ticker."""
        with self._lock:
            self._prices.pop(ticker, None)
            self._history.pop(ticker, None)
            self._session_open.pop(ticker, None)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, ticker: str) -> PriceUpdate | None:
        """Return the latest price for a ticker, or None if not tracked."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Return a snapshot of all current prices."""
        with self._lock:
            return dict(self._prices)

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        """Return up to `limit` most-recent updates for a ticker (oldest first)."""
        with self._lock:
            history = self._history.get(ticker, deque())
            items = list(history)
            return items[-limit:] if limit < len(items) else items

    def get_session_open(self, ticker: str) -> float:
        """Return the session-open price for a ticker (0.0 if unknown)."""
        with self._lock:
            return self._session_open.get(ticker, 0.0)

    def get_all_session_opens(self) -> dict[str, float]:
        """Return a snapshot of all session-open prices."""
        with self._lock:
            return dict(self._session_open)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def is_tracking(self, ticker: str) -> bool:
        """Return True if the ticker has at least one price in the cache."""
        with self._lock:
            return ticker in self._prices

    def tickers(self) -> list[str]:
        """Return all currently tracked tickers."""
        with self._lock:
            return list(self._prices.keys())
