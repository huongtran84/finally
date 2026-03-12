# Market Data Interface Design

## Overview

The FinAlly backend needs a unified Python interface for retrieving stock prices. The implementation is selected at startup based on environment variables:

- If `VNSTOCK_API_KEY` is set and non-empty: use the VNDirect REST API poller
- Otherwise: use the built-in price simulator

All downstream code (SSE streaming, price cache, trade execution, portfolio valuation) depends only on the abstract interface -- never on a specific implementation.

## Abstract Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PriceUpdate:
    """A single price update for one ticker."""
    ticker: str
    price: float
    previous_price: float
    open: float
    high: float
    low: float
    volume: int
    timestamp: str          # ISO 8601
    basic_price: float      # Reference price (gia tham chieu)
    ceiling_price: float    # Gia tran
    floor_price: float      # Gia san
    exchange: str           # "HOSE", "HNX", "UPCOM"


class MarketDataSource(ABC):
    """Abstract interface for market data providers."""

    @abstractmethod
    async def start(self) -> None:
        """Start the data source (begin generating/polling prices)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the data source and clean up resources."""

    @abstractmethod
    def get_price(self, ticker: str) -> PriceUpdate | None:
        """Get the latest price for a ticker. Returns None if not tracked."""

    @abstractmethod
    def get_all_prices(self) -> dict[str, PriceUpdate]:
        """Get latest prices for all tracked tickers."""

    @abstractmethod
    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        """Get rolling price history for a ticker (most recent last)."""

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """Start tracking a new ticker."""

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and remove from cache."""

    @abstractmethod
    def is_tracking(self, ticker: str) -> bool:
        """Check if a ticker is being tracked."""
```

## Price Cache (Shared)

Both implementations write to a shared in-memory price cache. The cache is the single source of truth that SSE streaming reads from.

```python
from collections import deque
import threading


class PriceCache:
    """Thread-safe in-memory price cache."""

    def __init__(self, history_size: int = 50):
        self._history_size = history_size
        self._prices: dict[str, PriceUpdate] = {}
        self._history: dict[str, deque[PriceUpdate]] = {}
        self._lock = threading.Lock()

    def update(self, update: PriceUpdate) -> None:
        """Write a new price update."""
        with self._lock:
            self._prices[update.ticker] = update
            if update.ticker not in self._history:
                self._history[update.ticker] = deque(maxlen=self._history_size)
            self._history[update.ticker].append(update)

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        with self._lock:
            history = self._history.get(ticker, deque())
            return list(history)[-limit:]

    def remove(self, ticker: str) -> None:
        with self._lock:
            self._prices.pop(ticker, None)
            self._history.pop(ticker, None)
```

## Implementation 1: VNDirect REST Poller

When `VNSTOCK_API_KEY` is set, use the VNDirect finfo-api to poll prices. Despite the env var name, the VNDirect REST API requires no authentication -- the env var acts as a feature flag to enable real data mode.

```python
import asyncio
import httpx
from datetime import datetime


class VNDirectDataSource(MarketDataSource):
    """Polls VNDirect finfo-api for real stock prices."""

    API_URL = "https://finfo-api.vndirect.com.vn/v4/stock_prices/"
    POLL_INTERVAL = 15  # seconds (conservative for free/unofficial API)

    def __init__(self, cache: PriceCache, tickers: list[str]):
        self._cache = cache
        self._tickers: set[str] = set(tickers)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while self._running:
                await self._poll_all(client)
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll_all(self, client: httpx.AsyncClient) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        for ticker in list(self._tickers):
            try:
                await self._poll_one(client, ticker, today)
            except Exception:
                pass  # Log in production; skip this cycle

    async def _poll_one(
        self, client: httpx.AsyncClient, ticker: str, date: str
    ) -> None:
        params = {
            "q": f"code:{ticker}~date:gte:{date}~date:lte:{date}",
            "sort": "date",
            "size": 1,
            "page": 1,
        }
        resp = await client.get(self.API_URL, params=params)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return

        record = data[0]
        prev = self._cache.get(ticker)
        previous_price = prev.price if prev else record["basicPrice"]

        update = PriceUpdate(
            ticker=record["code"],
            price=record["close"],
            previous_price=previous_price,
            open=record["open"],
            high=record["high"],
            low=record["low"],
            volume=record["nmVolume"],
            timestamp=datetime.now().isoformat(),
            basic_price=record["basicPrice"],
            ceiling_price=record["ceilingPrice"],
            floor_price=record["floorPrice"],
            exchange=record["floor"],
        )
        self._cache.update(update)

    def get_price(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        return self._cache.get_history(ticker, limit)

    def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker)

    def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._tickers
```

## Implementation 2: Simulator

When `VNSTOCK_API_KEY` is not set, the simulator generates prices using Geometric Brownian Motion (GBM). See `MARKET_SIMULATOR.md` for the full methodology.

```python
class SimulatorDataSource(MarketDataSource):
    """Simulates stock prices using GBM."""

    UPDATE_INTERVAL = 0.5  # seconds

    def __init__(self, cache: PriceCache, tickers: list[str]):
        self._cache = cache
        self._tickers: set[str] = set(tickers)
        self._running = False
        self._task: asyncio.Task | None = None
        # See MARKET_SIMULATOR.md for GBM internals

    async def start(self) -> None:
        self._running = True
        self._seed_initial_prices()
        self._task = asyncio.create_task(self._simulate_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    # ... (see MARKET_SIMULATOR.md for full implementation)
```

## Factory Function

```python
import os


def create_market_data_source(
    cache: PriceCache, initial_tickers: list[str]
) -> MarketDataSource:
    """Create the appropriate market data source based on environment."""
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if api_key:
        return VNDirectDataSource(cache, initial_tickers)
    return SimulatorDataSource(cache, initial_tickers)
```

## Integration with FastAPI

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache, DEFAULT_TICKERS)
    app.state.price_cache = cache
    app.state.market_data = source
    await source.start()
    yield
    await source.stop()

app = FastAPI(lifespan=lifespan)
```

## SSE Streaming Integration

The SSE endpoint reads from the price cache, not from the data source directly. This decouples the streaming layer from the data provider.

```python
from fastapi.responses import StreamingResponse
import asyncio
import json


@app.get("/api/stream/prices")
async def stream_prices(request: Request):
    cache: PriceCache = request.app.state.price_cache

    async def event_generator():
        last_seen: dict[str, float] = {}
        while True:
            prices = cache.get_all()
            for ticker, update in prices.items():
                if last_seen.get(ticker) != update.price:
                    last_seen[ticker] = update.price
                    data = json.dumps({
                        "ticker": update.ticker,
                        "price": update.price,
                        "previousPrice": update.previous_price,
                        "open": update.open,
                        "high": update.high,
                        "low": update.low,
                        "volume": update.volume,
                        "timestamp": update.timestamp,
                        "basicPrice": update.basic_price,
                        "ceilingPrice": update.ceiling_price,
                        "floorPrice": update.floor_price,
                        "exchange": update.exchange,
                    })
                    yield f"data: {data}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

## Watchlist Integration

When tickers are added/removed from the watchlist, the API handler notifies the market data source:

```python
@app.post("/api/watchlist")
async def add_to_watchlist(request: Request, body: WatchlistAdd):
    source: MarketDataSource = request.app.state.market_data
    source.add_ticker(body.ticker)
    # ... persist to database ...

@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(request: Request, ticker: str):
    source: MarketDataSource = request.app.state.market_data
    source.remove_ticker(ticker)
    # ... remove from database ...
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| VNDirect REST over WebSocket | REST polling is simpler to implement and debug; we only need price updates every few seconds, not sub-second |
| `VNSTOCK_API_KEY` as feature flag | Even though VNDirect API needs no auth, the env var clearly signals "use real data" vs. simulator |
| 15-second poll interval | Conservative for an unofficial API; avoids throttling; sufficient for a demo/educational app |
| PriceCache as intermediary | Decouples data source from consumers; same cache interface regardless of source |
| httpx over requests | Async-native HTTP client; works naturally with FastAPI's async event loop |
| No vnstock library dependency | Direct HTTP calls are simpler, more transparent, and avoid third-party version churn |
