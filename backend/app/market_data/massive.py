import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

from .base import MarketDataSource
from .cache import PriceCache

logger = logging.getLogger(__name__)

# Polygon/Massive API base
API_BASE = "https://api.polygon.io"
SNAPSHOT_ENDPOINT = "/v3/snapshot"

# Free tier: 5 calls/min → poll every 15s to stay safely under
DEFAULT_POLL_INTERVAL = 15.0
# Override via env var for paid tiers:
# MASSIVE_POLL_INTERVAL=2 for aggressive polling on paid plans


class MassiveSource(MarketDataSource):
    """
    Fetches live stock prices from the Massive (Polygon.io) REST API.
    Polls the unified snapshot endpoint on a configurable interval.
    """

    def __init__(self, api_key: str, poll_interval: float | None = None) -> None:
        self._api_key = api_key
        self._poll_interval = poll_interval or float(
            os.environ.get("MASSIVE_POLL_INTERVAL", DEFAULT_POLL_INTERVAL)
        )
        self._tickers: set[str] = set()
        self._running = False
        self._task: asyncio.Task | None = None
        self._cache: PriceCache | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self, cache: PriceCache) -> None:
        self._cache = cache
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()

    def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker.upper())

    def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker.upper())
        if self._cache:
            asyncio.create_task(self._cache.remove(ticker.upper()))

    def get_tracked_tickers(self) -> list[str]:
        return list(self._tickers)

    async def _poll_loop(self) -> None:
        """Poll the Massive API at the configured interval."""
        while self._running:
            if self._tickers:
                try:
                    await self._fetch_and_update()
                except Exception:
                    logger.exception("Massive API poll failed")
            await asyncio.sleep(self._poll_interval)

    async def _fetch_and_update(self) -> None:
        """
        Fetch snapshots for all tracked tickers in one API call
        and update the price cache.
        """
        tickers_csv = ",".join(sorted(self._tickers))
        url = f"{API_BASE}{SNAPSHOT_ENDPOINT}"
        params = {
            "ticker.any_of": tickers_csv,
            "apiKey": self._api_key,
        }

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "OK":
            logger.warning("Massive API returned status: %s", data.get("status"))
            return

        now = datetime.now(timezone.utc)

        for result in data.get("results", []):
            ticker = result.get("ticker", "")
            if ticker not in self._tickers:
                continue

            # Extract price from last_trade or session
            price = self._extract_price(result)
            if price is None:
                logger.warning("No price found for %s in Massive response", ticker)
                continue

            await self._cache.update(ticker, price, now)

    @staticmethod
    def _extract_price(result: dict) -> float | None:
        """
        Extract the best available price from a Massive snapshot result.

        Priority:
        1. last_trade.price — most recent actual trade
        2. session.price — session-level price
        3. session.close — closing price (if market closed)
        """
        last_trade = result.get("last_trade", {})
        if last_trade and last_trade.get("price"):
            return float(last_trade["price"])

        session = result.get("session", {})
        if session.get("price"):
            return float(session["price"])
        if session.get("close"):
            return float(session["close"])

        return None
