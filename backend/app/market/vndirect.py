"""VNDirect REST API poller for real Vietnamese stock prices."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.models import PriceUpdate

logger = logging.getLogger(__name__)

#: VNDirect finfo-api endpoint for historical/EOD stock prices.
_API_URL = "https://finfo-api.vndirect.com.vn/v4/stock_prices/"

#: Seconds between full poll cycles (conservative for an unofficial API).
_POLL_INTERVAL = 15


class VNDirectDataSource(MarketDataSource):
    """Polls the VNDirect finfo-api for near-real-time stock prices.

    The VNDirect REST API is publicly accessible without authentication.
    The ``VNSTOCK_API_KEY`` environment variable acts as a feature flag —
    when set it signals "use real data"; the actual value is not sent to
    VNDirect.

    Polling strategy:
    * Every ``POLL_INTERVAL`` seconds, iterate over all tracked tickers and
      fetch the latest price record for today.
    * If a record is not available (e.g. market is closed), the previous
      cached price is retained unchanged.
    * Errors for individual tickers are swallowed so a single bad ticker
      does not break the poll cycle.
    """

    POLL_INTERVAL: int = _POLL_INTERVAL
    API_URL: str = _API_URL

    def __init__(
        self,
        cache: PriceCache,
        tickers: list[str],
        poll_interval: int = _POLL_INTERVAL,
        api_url: str = _API_URL,
    ) -> None:
        self._cache = cache
        self._tickers: set[str] = set(tickers)
        self._running = False
        self._task: asyncio.Task | None = None
        self._poll_interval = poll_interval
        self._api_url = api_url

    # ------------------------------------------------------------------
    # MarketDataSource interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Cancel the polling background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_price(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        return self._cache.get_history(ticker, limit)

    def get_session_open(self, ticker: str) -> float:
        return self._cache.get_session_open(ticker)

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to be polled on the next cycle."""
        self._tickers.add(ticker)

    def remove_ticker(self, ticker: str) -> None:
        """Stop polling a ticker and remove it from the cache."""
        self._tickers.discard(ticker)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._tickers

    # ------------------------------------------------------------------
    # Internal polling logic
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Continuously poll all tracked tickers."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            while self._running:
                await self._poll_all(client)
                await asyncio.sleep(self._poll_interval)

    async def _poll_all(self, client: httpx.AsyncClient) -> None:
        """Poll all currently-tracked tickers for today's price data."""
        today = datetime.now().strftime("%Y-%m-%d")
        for ticker in list(self._tickers):
            try:
                await self._poll_one(client, ticker, today)
            except Exception as exc:
                logger.warning("VNDirect poll failed for %s: %s", ticker, exc)

    async def _poll_one(
        self, client: httpx.AsyncClient, ticker: str, date: str
    ) -> None:
        """Fetch and cache the latest price record for one ticker on *date*.

        Args:
            client: Shared ``httpx.AsyncClient`` instance.
            ticker: Stock ticker symbol (e.g. ``"VNM"``).
            date: Date in ``YYYY-MM-DD`` format.
        """
        params = {
            "q": f"code:{ticker}~date:gte:{date}~date:lte:{date}",
            "sort": "date",
            "size": 1,
            "page": 1,
        }
        resp = await client.get(self._api_url, params=params)
        resp.raise_for_status()

        data = resp.json().get("data", [])
        if not data:
            # No data for today (e.g. market closed, holiday)
            return

        record = data[0]
        prev = self._cache.get(ticker)
        previous_price = prev.price if prev is not None else float(record["basicPrice"])

        update = PriceUpdate(
            ticker=record["code"],
            price=float(record["close"]),
            previous_price=previous_price,
            open=float(record["open"]),
            high=float(record["high"]),
            low=float(record["low"]),
            volume=int(record["nmVolume"]),
            timestamp=datetime.now(timezone.utc).isoformat(),
            basic_price=float(record["basicPrice"]),
            ceiling_price=float(record["ceilingPrice"]),
            floor_price=float(record["floorPrice"]),
            exchange=record["floor"],
        )
        self._cache.update(update)

    # ------------------------------------------------------------------
    # Helpers exposed for testing
    # ------------------------------------------------------------------

    def _parse_record(self, record: dict, previous_price: float) -> PriceUpdate:
        """Parse a single VNDirect API record into a ``PriceUpdate``.

        Exposed so unit tests can exercise parsing without making HTTP calls.
        """
        return PriceUpdate(
            ticker=record["code"],
            price=float(record["close"]),
            previous_price=previous_price,
            open=float(record["open"]),
            high=float(record["high"]),
            low=float(record["low"]),
            volume=int(record["nmVolume"]),
            timestamp=datetime.now(timezone.utc).isoformat(),
            basic_price=float(record["basicPrice"]),
            ceiling_price=float(record["ceilingPrice"]),
            floor_price=float(record["floorPrice"]),
            exchange=record["floor"],
        )
