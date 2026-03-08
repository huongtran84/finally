# Market Data Backend — Detailed Design

This document specifies the complete market data subsystem for the FinAlly backend. It covers the abstract interface, the simulator implementation (GBM), the Massive API client, the shared price cache, SSE streaming, and the factory that selects the correct implementation at startup.

All code lives under `backend/app/market_data/`.

---

## 1. Module Structure

```
backend/app/market_data/
├── __init__.py           # Re-exports: get_market_data_source, PriceCache
├── models.py             # Pydantic models (PriceTick, PriceHistory, TickerConfig)
├── base.py               # Abstract base class: MarketDataSource
├── cache.py              # PriceCache — shared in-memory price store
├── simulator.py          # SimulatorSource — GBM price generator
├── massive.py            # MassiveSource — Polygon/Massive REST poller
├── factory.py            # create_market_data_source() — env-driven factory
└── sse.py                # SSE streaming endpoint logic
```

---

## 2. Data Models (`models.py`)

These Pydantic models are the shared contract between all market data components and the rest of the backend.

```python
from pydantic import BaseModel
from datetime import datetime


class PriceTick(BaseModel):
    """A single price update for one ticker."""
    ticker: str
    price: float
    previous_price: float
    timestamp: datetime
    direction: str  # "up", "down", or "flat"
    session_change_pct: float  # % change from session start price

    @staticmethod
    def compute_direction(price: float, previous_price: float) -> str:
        if price > previous_price:
            return "up"
        elif price < previous_price:
            return "down"
        return "flat"


class TickerConfig(BaseModel):
    """Configuration for a single simulated ticker."""
    ticker: str
    seed_price: float
    drift: float = 0.0     # annualized drift (mu)
    volatility: float = 0.3  # annualized volatility (sigma)
    sector: str = "tech"


class PriceSnapshot(BaseModel):
    """A point-in-time price for history."""
    price: float
    timestamp: datetime


class PriceHistory(BaseModel):
    """Rolling price history for one ticker."""
    ticker: str
    prices: list[PriceSnapshot]
    session_start_price: float
```

---

## 3. Abstract Base Class (`base.py`)

All market data sources implement this interface. Downstream code (SSE streaming, API routes, portfolio service) depends only on this abstraction.

```python
from abc import ABC, abstractmethod
import asyncio


class MarketDataSource(ABC):
    """
    Abstract interface for market data providers.

    Implementations must:
    1. Generate/fetch price ticks and write them to the shared PriceCache.
    2. Run as a long-lived background task via start().
    3. Support dynamic ticker addition/removal.
    """

    @abstractmethod
    async def start(self, cache: "PriceCache") -> None:
        """
        Begin producing price data. Runs indefinitely as a background task.
        Writes PriceTick objects into the provided PriceCache.

        Args:
            cache: The shared PriceCache to write updates into.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the data source."""
        ...

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """
        Start tracking a new ticker.

        For the simulator: seeds at a realistic price and begins generating.
        For Massive: adds to the next poll batch.
        """
        ...

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """
        Stop tracking a ticker.

        The source stops generating/polling for this ticker.
        The PriceCache entry is also cleared.
        """
        ...

    @abstractmethod
    def get_tracked_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
        ...
```

### Why an ABC?

- The SSE endpoint, the trade execution service (needs current price), and the watchlist endpoints all need market data. None of them should know or care whether prices come from GBM math or a REST API.
- Testing is trivial — inject a mock source that returns predetermined prices.
- Future data sources (WebSocket feeds, CSV replay) slot in with zero changes to consumers.

---

## 4. Shared Price Cache (`cache.py`)

The cache is the single source of truth for current prices. The market data source writes to it; SSE and REST endpoints read from it.

```python
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
```

### Design notes

- **`asyncio.Lock`** is used rather than `threading.Lock` because the entire backend is async (FastAPI + uvicorn). All callers are coroutines.
- **Version counter** lets the SSE loop efficiently detect changes without comparing full dicts. The SSE loop remembers the last version it broadcast and only sends data when `cache.version > last_sent_version`.
- **`deque(maxlen=50)`** automatically evicts old entries — no manual pruning needed.
- **Round to 2 decimal places** on write, so all consumers get clean prices.

---

## 5. Simulator (`simulator.py`)

### Geometric Brownian Motion

The GBM model generates realistic-looking stock price paths. The discrete-time formula for each step:

```
S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `S(t)` is the current price
- `mu` is the annualized drift (expected return)
- `sigma` is the annualized volatility
- `dt` is the time step in years (0.5 seconds ≈ 0.5 / (252 * 6.5 * 3600) years)
- `Z` is a standard normal random variable

### Sector Correlation

To make tech stocks move together (and differently from financials), we use a **Cholesky decomposition** of a correlation matrix. Instead of drawing independent random `Z` values per ticker, we draw correlated ones:

1. Define a correlation matrix based on sector membership.
2. Compute its Cholesky decomposition `L` (lower triangular).
3. Draw independent standard normals `Z_independent`.
4. Multiply: `Z_correlated = L @ Z_independent`.

### Random Events

Occasionally (configurable probability per tick), a ticker experiences a "shock" — a sudden move of 2-5% in either direction. This adds drama to the simulation and tests the frontend's ability to handle rapid price changes.

```python
import asyncio
import math
import random
from datetime import datetime, timezone

import numpy as np

from .base import MarketDataSource
from .cache import PriceCache
from .models import TickerConfig


# Default tickers with realistic seed prices and per-ticker volatility
DEFAULT_TICKERS: list[TickerConfig] = [
    TickerConfig(ticker="AAPL",  seed_price=192.0, volatility=0.25, sector="tech"),
    TickerConfig(ticker="GOOGL", seed_price=176.0, volatility=0.28, sector="tech"),
    TickerConfig(ticker="MSFT",  seed_price=420.0, volatility=0.22, sector="tech"),
    TickerConfig(ticker="AMZN",  seed_price=185.0, volatility=0.30, sector="tech"),
    TickerConfig(ticker="TSLA",  seed_price=245.0, volatility=0.55, sector="tech"),
    TickerConfig(ticker="NVDA",  seed_price=880.0, volatility=0.45, sector="tech"),
    TickerConfig(ticker="META",  seed_price=500.0, volatility=0.32, sector="tech"),
    TickerConfig(ticker="JPM",   seed_price=195.0, volatility=0.20, sector="finance"),
    TickerConfig(ticker="V",     seed_price=280.0, volatility=0.18, sector="finance"),
    TickerConfig(ticker="NFLX",  seed_price=620.0, volatility=0.35, sector="tech"),
]

# Sector correlation: same-sector tickers have 0.6 correlation, cross-sector 0.2
SECTOR_CORRELATION = {
    ("tech", "tech"): 0.6,
    ("finance", "finance"): 0.5,
    ("tech", "finance"): 0.2,
    ("finance", "tech"): 0.2,
}
DEFAULT_CROSS_CORRELATION = 0.1

# Simulation parameters
TICK_INTERVAL = 0.5  # seconds between price updates
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5.9M seconds
DT = TICK_INTERVAL / TRADING_SECONDS_PER_YEAR

# Random event parameters
EVENT_PROBABILITY = 0.003  # ~0.3% chance per tick per ticker (~1 event per ticker per 3 min)
EVENT_MIN_PCT = 0.02       # minimum event shock: 2%
EVENT_MAX_PCT = 0.05       # maximum event shock: 5%


class SimulatorSource(MarketDataSource):
    """
    Generates realistic stock prices using geometric Brownian motion
    with sector correlation and random shock events.
    """

    def __init__(self, tickers: list[TickerConfig] | None = None) -> None:
        self._configs: dict[str, TickerConfig] = {}
        self._current_prices: dict[str, float] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._cache: PriceCache | None = None

        for cfg in (tickers or DEFAULT_TICKERS):
            self._configs[cfg.ticker] = cfg
            self._current_prices[cfg.ticker] = cfg.seed_price

    async def start(self, cache: PriceCache) -> None:
        self._cache = cache
        self._running = True

        # Seed initial prices into the cache
        now = datetime.now(timezone.utc)
        for ticker, price in self._current_prices.items():
            await cache.update(ticker, price, now)

        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def add_ticker(self, ticker: str) -> None:
        """
        Dynamically add a ticker to the simulator.

        Seeds at a plausible price ($50-$500 range) with default volatility.
        """
        if ticker in self._configs:
            return

        seed_price = random.uniform(50.0, 500.0)
        config = TickerConfig(
            ticker=ticker,
            seed_price=seed_price,
            volatility=0.30,
            sector="tech",  # default; doesn't matter much for a single add
        )
        self._configs[ticker] = config
        self._current_prices[ticker] = seed_price

        # If already running, seed the cache immediately
        if self._cache:
            asyncio.create_task(
                self._cache.update(ticker, seed_price, datetime.now(timezone.utc))
            )

    def remove_ticker(self, ticker: str) -> None:
        self._configs.pop(ticker, None)
        self._current_prices.pop(ticker, None)
        if self._cache:
            asyncio.create_task(self._cache.remove(ticker))

    def get_tracked_tickers(self) -> list[str]:
        return list(self._configs.keys())

    async def _run_loop(self) -> None:
        """Main simulation loop — runs every TICK_INTERVAL seconds."""
        while self._running:
            await self._generate_tick()
            await asyncio.sleep(TICK_INTERVAL)

    async def _generate_tick(self) -> None:
        """
        Generate one round of correlated price moves for all tracked tickers.
        """
        tickers = list(self._configs.keys())
        n = len(tickers)
        if n == 0:
            return

        # Build correlation matrix
        corr_matrix = np.eye(n)
        sectors = [self._configs[t].sector for t in tickers]
        for i in range(n):
            for j in range(i + 1, n):
                key = (sectors[i], sectors[j])
                rho = SECTOR_CORRELATION.get(key, DEFAULT_CROSS_CORRELATION)
                corr_matrix[i, j] = rho
                corr_matrix[j, i] = rho

        # Cholesky decomposition for correlated normals
        try:
            L = np.linalg.cholesky(corr_matrix)
        except np.linalg.LinAlgError:
            # Fallback: if matrix isn't positive definite (shouldn't happen
            # with our correlations), use independent normals
            L = np.eye(n)

        z_independent = np.random.standard_normal(n)
        z_correlated = L @ z_independent

        now = datetime.now(timezone.utc)

        for i, ticker in enumerate(tickers):
            cfg = self._configs[ticker]
            S = self._current_prices[ticker]

            # GBM step
            mu = cfg.drift
            sigma = cfg.volatility
            z = z_correlated[i]
            new_price = S * math.exp((mu - 0.5 * sigma**2) * DT + sigma * math.sqrt(DT) * z)

            # Random event: sudden shock
            if random.random() < EVENT_PROBABILITY:
                shock_pct = random.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
                shock_direction = random.choice([-1, 1])
                new_price *= (1 + shock_direction * shock_pct)

            # Clamp to prevent negative/zero prices
            new_price = max(new_price, 0.01)

            self._current_prices[ticker] = new_price
            await self._cache.update(ticker, new_price, now)
```

### Key implementation details

1. **Cholesky is recomputed each tick** because tickers can be added/removed dynamically. With 10-20 tickers this is trivially fast (<1ms). If the ticker set were static, we'd cache `L`.

2. **`asyncio.create_task`** in `add_ticker`/`remove_ticker` is used because these methods are called synchronously from the watchlist endpoint but need to interact with the async cache. The tasks complete on the next event loop cycle.

3. **TSLA gets 0.55 volatility** while V gets 0.18 — this mirrors real-world behavior where meme stocks are dramatically more volatile than payment processors.

4. **Event probability of 0.003** means each ticker experiences roughly one shock every ~3 minutes (0.003 * 120 ticks/min ≈ 0.36 events/min). At 10 tickers, the user sees a shock somewhere about every 17 seconds — frequent enough to be interesting, rare enough to feel special.

---

## 6. Massive API Client (`massive.py`)

The Massive (formerly Polygon.io) client polls the REST API for real-time snapshot data. It uses the **v3 unified snapshot** endpoint to fetch all watched tickers in a single request.

### Endpoint

```
GET https://api.polygon.io/v3/snapshot?ticker.any_of=AAPL,MSFT,GOOGL&apiKey=<key>
```

### Response shape (relevant fields)

```json
{
  "results": [
    {
      "ticker": "AAPL",
      "session": {
        "price": 192.53,
        "close": 191.20,
        "open": 190.80,
        "high": 193.10,
        "low": 190.50,
        "change": 1.33,
        "change_percent": 0.6958,
        "previous_close": 191.20
      },
      "last_trade": {
        "price": 192.53,
        "timestamp": 1709900000000000000
      },
      "market_status": "open"
    }
  ],
  "status": "OK"
}
```

### Implementation

```python
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
# Override via env var for paid tiers
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
```

### Rate limiting strategy

| Massive Tier | Rate Limit | Recommended `MASSIVE_POLL_INTERVAL` |
|---|---|---|
| Free | 5 calls/min | `15` (default) |
| Starter | unlimited | `5` |
| Developer | unlimited | `2` |
| Advanced+ | unlimited | `2` |

The env var `MASSIVE_POLL_INTERVAL` overrides the default. With 10 tickers and the v3 unified snapshot, all tickers are fetched in a **single API call**, so rate limits apply per-poll, not per-ticker.

### Error handling

- **HTTP errors** (rate limit 429, server errors 5xx): logged, skipped, retried next interval. No crash.
- **Missing tickers** in response: silently skipped (ticker may be invalid or not yet trading).
- **Malformed JSON**: caught by the broad `except Exception` in the poll loop.
- **Network timeouts**: httpx is configured with a 10-second timeout. Failures are logged and retried next interval.

---

## 7. Factory (`factory.py`)

The factory reads `MASSIVE_API_KEY` from the environment and returns the appropriate `MarketDataSource` implementation.

```python
import os
import logging

from .base import MarketDataSource
from .simulator import SimulatorSource
from .massive import MassiveSource

logger = logging.getLogger(__name__)


def create_market_data_source() -> MarketDataSource:
    """
    Factory function that returns the appropriate market data source
    based on environment configuration.

    - If MASSIVE_API_KEY is set and non-empty → MassiveSource
    - Otherwise → SimulatorSource (default)
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        logger.info("Using Massive API for market data (poll interval: %s)",
                     os.environ.get("MASSIVE_POLL_INTERVAL", "15"))
        return MassiveSource(api_key=api_key)
    else:
        logger.info("Using market simulator for market data")
        return SimulatorSource()
```

---

## 8. SSE Streaming (`sse.py`)

The SSE endpoint reads from the `PriceCache` and pushes updates to connected clients only when prices have actually changed.

```python
import asyncio
import json
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import StreamingResponse

from .cache import PriceCache


SSE_CHECK_INTERVAL = 0.25  # seconds between checking for new data


async def price_stream(request: Request, cache: PriceCache):
    """
    SSE endpoint generator. Yields 'data: {...}\n\n' events whenever
    the price cache has new data.
    """

    async def event_generator():
        last_version = -1  # force initial send

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            current_version = cache.version
            if current_version > last_version:
                # Prices have changed — send all latest prices
                all_prices = await cache.get_all_latest()
                ticks = [tick.model_dump(mode="json") for tick in all_prices.values()]

                # SSE format: each event is "data: <json>\n\n"
                payload = json.dumps(ticks, default=str)
                yield f"data: {payload}\n\n"

                last_version = current_version

            await asyncio.sleep(SSE_CHECK_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

### SSE event format

Each SSE event is a JSON array of `PriceTick` objects:

```json
data: [
  {
    "ticker": "AAPL",
    "price": 192.45,
    "previous_price": 192.30,
    "timestamp": "2026-03-08T14:30:00.500Z",
    "direction": "up",
    "session_change_pct": 0.2344
  },
  {
    "ticker": "MSFT",
    "price": 419.80,
    "previous_price": 420.10,
    "timestamp": "2026-03-08T14:30:00.500Z",
    "direction": "down",
    "session_change_pct": -0.0714
  }
]
```

### Design choices

- **Push all tickers on each change**, not just the changed ones. With 10-20 tickers, the payload is tiny (~1KB). This simplifies the frontend — it always receives a complete snapshot and doesn't need to merge partial updates.
- **250ms check interval** is faster than the simulator's 500ms tick interval, ensuring no updates are missed. For the Massive source (15s polls), most checks find no change and yield nothing.
- **Version-based change detection** means the SSE loop does zero work when nothing has changed — no dict comparison, no serialization. Just an integer comparison.

---

## 9. FastAPI Integration

### App startup / shutdown

```python
# backend/app/main.py (relevant excerpt)

from contextlib import asynccontextmanager
from fastapi import FastAPI

from .market_data.factory import create_market_data_source
from .market_data.cache import PriceCache


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    cache = PriceCache()
    source = create_market_data_source()

    # Load initial tickers from the database watchlist
    initial_tickers = await get_watchlist_tickers()  # from DB module
    for ticker in initial_tickers:
        source.add_ticker(ticker)

    await source.start(cache)

    # Store on app.state for access in route handlers
    app.state.price_cache = cache
    app.state.market_data_source = source

    yield

    # Shutdown
    await source.stop()


app = FastAPI(lifespan=lifespan)
```

### Route registration

```python
# backend/app/routes/market_data.py

from fastapi import APIRouter, Request

from ..market_data.sse import price_stream

router = APIRouter(prefix="/api")


@router.get("/stream/prices")
async def stream_prices(request: Request):
    """SSE endpoint for live price updates."""
    cache = request.app.state.price_cache
    return await price_stream(request, cache)


@router.get("/prices/history")
async def get_price_history(request: Request):
    """
    Returns rolling price history (last 50 per ticker) for sparkline bootstrap.

    Response shape:
    {
      "AAPL": {
        "ticker": "AAPL",
        "prices": [{"price": 192.0, "timestamp": "..."}, ...],
        "session_start_price": 192.0
      },
      ...
    }
    """
    cache = request.app.state.price_cache
    all_history = await cache.get_all_history()
    return {
        ticker: history.model_dump(mode="json")
        for ticker, history in all_history.items()
    }
```

### Watchlist integration

When the watchlist endpoint adds or removes a ticker, it notifies the market data source:

```python
# backend/app/routes/watchlist.py (relevant excerpt)

@router.post("/api/watchlist")
async def add_to_watchlist(request: Request, body: AddTickerRequest):
    ticker = body.ticker.upper()

    # ... insert into database ...

    # Notify market data source to start tracking
    source = request.app.state.market_data_source
    source.add_ticker(ticker)

    return {"status": "ok", "ticker": ticker}


@router.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(request: Request, ticker: str):
    ticker = ticker.upper()

    # ... delete from database ...

    # Notify market data source to stop tracking
    source = request.app.state.market_data_source
    source.remove_ticker(ticker)

    return {"status": "ok", "ticker": ticker}
```

### Trade execution price lookup

The trade endpoint uses the cache to get the current price for instant-fill market orders:

```python
# backend/app/routes/portfolio.py (relevant excerpt)

@router.post("/api/portfolio/trade")
async def execute_trade(request: Request, body: TradeRequest):
    cache = request.app.state.price_cache
    current_price = await cache.get_price(body.ticker.upper())

    if current_price is None:
        return JSONResponse(
            status_code=400,
            content={"error": f"No price available for {body.ticker}"}
        )

    # Execute at current_price ...
```

---

## 10. Testing Strategy

### Unit tests for the simulator

```python
# backend/tests/test_simulator.py

import pytest
from app.market_data.simulator import SimulatorSource, DEFAULT_TICKERS
from app.market_data.cache import PriceCache


@pytest.mark.asyncio
async def test_simulator_seeds_initial_prices():
    """After start(), all default tickers should have prices in the cache."""
    cache = PriceCache()
    source = SimulatorSource()
    await source.start(cache)

    for cfg in DEFAULT_TICKERS:
        tick = await cache.get_latest(cfg.ticker)
        assert tick is not None
        assert tick.price == cfg.seed_price

    await source.stop()


@pytest.mark.asyncio
async def test_simulator_prices_change_over_time():
    """After running for a bit, prices should differ from seed."""
    import asyncio
    cache = PriceCache()
    source = SimulatorSource()
    await source.start(cache)

    await asyncio.sleep(1.5)  # ~3 ticks

    for cfg in DEFAULT_TICKERS:
        tick = await cache.get_latest(cfg.ticker)
        assert tick is not None
        # Price should have moved (extremely unlikely to be identical)
        # But we can't assert direction, so just check it's positive
        assert tick.price > 0

    await source.stop()


@pytest.mark.asyncio
async def test_add_ticker_dynamically():
    """Adding a ticker mid-simulation should seed it in the cache."""
    cache = PriceCache()
    source = SimulatorSource()
    await source.start(cache)

    source.add_ticker("PYPL")
    import asyncio
    await asyncio.sleep(0.1)  # let the create_task complete

    tick = await cache.get_latest("PYPL")
    assert tick is not None
    assert tick.price > 0

    await source.stop()


@pytest.mark.asyncio
async def test_remove_ticker():
    """Removing a ticker should clear it from the source and cache."""
    cache = PriceCache()
    source = SimulatorSource()
    await source.start(cache)

    source.remove_ticker("AAPL")
    import asyncio
    await asyncio.sleep(0.1)

    assert "AAPL" not in source.get_tracked_tickers()
    tick = await cache.get_latest("AAPL")
    assert tick is None

    await source.stop()
```

### Unit tests for the cache

```python
# backend/tests/test_cache.py

import pytest
from datetime import datetime, timezone
from app.market_data.cache import PriceCache


@pytest.mark.asyncio
async def test_first_update_sets_session_start():
    cache = PriceCache()
    tick = await cache.update("AAPL", 192.0, datetime.now(timezone.utc))
    assert tick.session_change_pct == 0.0
    assert tick.direction == "flat"
    assert tick.previous_price == 192.0


@pytest.mark.asyncio
async def test_second_update_computes_direction():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    await cache.update("AAPL", 192.0, now)
    tick = await cache.update("AAPL", 193.0, now)
    assert tick.direction == "up"
    assert tick.previous_price == 192.0
    assert tick.session_change_pct > 0


@pytest.mark.asyncio
async def test_history_limited_to_50():
    cache = PriceCache()
    now = datetime.now(timezone.utc)
    for i in range(60):
        await cache.update("AAPL", 100.0 + i, now)

    history = await cache.get_history("AAPL")
    assert len(history.prices) == 50


@pytest.mark.asyncio
async def test_version_increments():
    cache = PriceCache()
    v0 = cache.version
    await cache.update("AAPL", 100.0, datetime.now(timezone.utc))
    assert cache.version == v0 + 1


@pytest.mark.asyncio
async def test_remove_clears_everything():
    cache = PriceCache()
    await cache.update("AAPL", 100.0, datetime.now(timezone.utc))
    await cache.remove("AAPL")
    assert await cache.get_latest("AAPL") is None
    assert await cache.get_history("AAPL") is None
```

### Unit tests for the Massive client

```python
# backend/tests/test_massive.py

import pytest
from app.market_data.massive import MassiveSource


def test_extract_price_from_last_trade():
    result = {
        "ticker": "AAPL",
        "last_trade": {"price": 192.53},
        "session": {"price": 192.50, "close": 191.20},
    }
    assert MassiveSource._extract_price(result) == 192.53


def test_extract_price_fallback_to_session():
    result = {
        "ticker": "AAPL",
        "last_trade": {},
        "session": {"price": 192.50},
    }
    assert MassiveSource._extract_price(result) == 192.50


def test_extract_price_fallback_to_close():
    result = {
        "ticker": "AAPL",
        "session": {"close": 191.20},
    }
    assert MassiveSource._extract_price(result) == 191.20


def test_extract_price_returns_none_when_missing():
    result = {"ticker": "AAPL", "session": {}}
    assert MassiveSource._extract_price(result) is None
```

### Interface conformance test

This test ensures both implementations satisfy the abstract interface:

```python
# backend/tests/test_interface_conformance.py

import pytest
from app.market_data.base import MarketDataSource
from app.market_data.simulator import SimulatorSource
from app.market_data.massive import MassiveSource
from app.market_data.cache import PriceCache


@pytest.mark.parametrize("source_class,kwargs", [
    (SimulatorSource, {}),
    (MassiveSource, {"api_key": "fake-key"}),
])
def test_implements_interface(source_class, kwargs):
    """Both sources must be instances of MarketDataSource."""
    source = source_class(**kwargs)
    assert isinstance(source, MarketDataSource)
    assert hasattr(source, "start")
    assert hasattr(source, "stop")
    assert hasattr(source, "add_ticker")
    assert hasattr(source, "remove_ticker")
    assert hasattr(source, "get_tracked_tickers")
```

---

## 11. Dependency Summary

Add these to `backend/pyproject.toml`:

```toml
[project]
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.0",
    "httpx>=0.27",       # async HTTP client for Massive API
    "numpy>=1.26",       # Cholesky decomposition for correlated GBM
    "sse-starlette>=1.8", # optional — or use raw StreamingResponse as shown
]
```

`numpy` is the only non-obvious dependency. It's used solely for the Cholesky decomposition in the simulator. If avoiding numpy is preferred, the correlation matrix math can be done with pure Python for small matrices (10x10), but numpy makes it clean and fast.

`httpx` is used instead of `aiohttp` because it integrates better with the Starlette/FastAPI ecosystem and has a simpler API for our use case (straightforward GET requests).

---

## 12. Sequence Diagrams

### Startup flow

```
App startup
    │
    ├─ create_market_data_source()
    │   └─ reads MASSIVE_API_KEY env var
    │       ├─ key present → MassiveSource(api_key)
    │       └─ key absent  → SimulatorSource()
    │
    ├─ Load watchlist tickers from DB
    │   └─ for each ticker: source.add_ticker(ticker)
    │
    └─ source.start(cache)
        ├─ SimulatorSource: seeds prices, starts _run_loop task
        └─ MassiveSource: creates httpx client, starts _poll_loop task
```

### Price update flow (Simulator)

```
Every 500ms:
    SimulatorSource._generate_tick()
        │
        ├─ Build correlation matrix from sectors
        ├─ Cholesky decompose → L
        ├─ Draw correlated normals: Z = L @ N(0,1)
        │
        └─ For each ticker:
            ├─ GBM step: S_new = S * exp(...)
            ├─ Maybe apply random event shock
            └─ cache.update(ticker, S_new, now)
                ├─ Creates PriceTick with direction, session_change_pct
                ├─ Appends to rolling history deque
                └─ Increments version counter
```

### Price update flow (Massive)

```
Every 15s (or configured interval):
    MassiveSource._poll_loop()
        │
        └─ _fetch_and_update()
            ├─ GET /v3/snapshot?ticker.any_of=AAPL,MSFT,...&apiKey=...
            ├─ Parse JSON response
            └─ For each result:
                ├─ _extract_price(result) → float
                └─ cache.update(ticker, price, now)
```

### SSE push flow

```
Client connects: GET /api/stream/prices
    │
    └─ event_generator() loop (every 250ms):
        ├─ Check: cache.version > last_version?
        │   ├─ No  → sleep, continue
        │   └─ Yes → serialize all latest prices → yield SSE event
        │
        └─ Check: client disconnected?
            └─ Yes → break
```

### Dynamic ticker flow

```
POST /api/watchlist  {ticker: "PYPL"}
    │
    ├─ Insert into watchlist table
    └─ source.add_ticker("PYPL")
        ├─ SimulatorSource: seed at random price, add to configs
        └─ MassiveSource: add to _tickers set (included in next poll)

DELETE /api/watchlist/PYPL
    │
    ├─ Delete from watchlist table
    └─ source.remove_ticker("PYPL")
        ├─ Remove from configs/ticker set
        └─ cache.remove("PYPL") — clears all cached data
```
