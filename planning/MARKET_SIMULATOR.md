# Market Simulator Design

## Overview

The simulator generates realistic stock price movements for Vietnamese equities using Geometric Brownian Motion (GBM) with sector correlation and price limit enforcement. It runs as an async background task, updating the shared PriceCache every ~500ms.

## Methodology: Geometric Brownian Motion

### Core Formula

Each price update follows:

```
S(t+dt) = S(t) * exp((mu - 0.5 * sigma^2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `S(t)` = current price
- `mu` = annualized drift (expected return)
- `sigma` = annualized volatility
- `dt` = time step in years (0.5s / (252 days * 6.5 hours * 3600 seconds))
- `Z` = standard normal random variable

### Why GBM

GBM is the standard model for stock price simulation because:
- Prices are always positive (log-normal distribution)
- Returns are normally distributed
- The model captures both trend (drift) and randomness (volatility)
- Simple to implement and computationally cheap

## Stock Configuration

Each tracked stock has parameters calibrated to Vietnamese market characteristics:

```python
from dataclasses import dataclass


@dataclass
class StockConfig:
    ticker: str
    name: str
    exchange: str          # "HOSE", "HNX", "UPCOM"
    sector: str            # For correlation grouping
    initial_price: float   # VND
    mu: float              # Annualized drift
    sigma: float           # Annualized volatility


DEFAULT_STOCKS = [
    StockConfig("VNM", "Vinamilk", "HOSE", "consumer", 75_000, 0.05, 0.25),
    StockConfig("VCB", "Vietcombank", "HOSE", "banking", 92_000, 0.08, 0.30),
    StockConfig("VIC", "Vingroup", "HOSE", "real_estate", 42_000, 0.03, 0.35),
    StockConfig("HPG", "Hoa Phat", "HOSE", "materials", 28_000, 0.06, 0.40),
    StockConfig("FPT", "FPT Corp", "HOSE", "technology", 130_000, 0.10, 0.30),
    StockConfig("MWG", "The Gioi Di Dong", "HOSE", "retail", 45_000, 0.04, 0.35),
    StockConfig("TCB", "Techcombank", "HOSE", "banking", 25_000, 0.07, 0.30),
    StockConfig("VHM", "Vinhomes", "HOSE", "real_estate", 38_000, 0.04, 0.35),
    StockConfig("GAS", "PV Gas", "HOSE", "energy", 85_000, 0.05, 0.25),
    StockConfig("MSN", "Masan", "HOSE", "consumer", 68_000, 0.06, 0.30),
]
```

## Sector Correlation

Stocks in the same sector share a common random factor so they move together, reflecting real market behavior.

```python
import numpy as np

SECTOR_CORRELATION = 0.6  # 60% of movement is sector-driven

def generate_correlated_returns(
    stocks: list[StockConfig], dt: float
) -> dict[str, float]:
    """Generate correlated random returns for all stocks."""
    sectors = set(s.sector for s in stocks)
    sector_shocks = {sector: np.random.normal() for sector in sectors}

    returns = {}
    for stock in stocks:
        sector_z = sector_shocks[stock.sector]
        idio_z = np.random.normal()
        # Combine sector and idiosyncratic components
        z = (
            SECTOR_CORRELATION * sector_z
            + np.sqrt(1 - SECTOR_CORRELATION**2) * idio_z
        )
        drift = (stock.mu - 0.5 * stock.sigma**2) * dt
        diffusion = stock.sigma * np.sqrt(dt) * z
        returns[stock.ticker] = drift + diffusion
    return returns
```

## Price Limit Enforcement

Vietnamese exchanges enforce daily price limits. The simulator enforces these per update cycle.

```python
PRICE_LIMITS = {
    "HOSE": 0.07,    # +/- 7%
    "HNX": 0.10,     # +/- 10%
    "UPCOM": 0.15,   # +/- 15%
}

def clamp_price(
    new_price: float,
    basic_price: float,
    exchange: str,
) -> float:
    """Clamp price within exchange-specific daily limits."""
    limit = PRICE_LIMITS.get(exchange, 0.07)
    ceiling = basic_price * (1 + limit)
    floor = basic_price * (1 - limit)
    return max(floor, min(ceiling, new_price))
```

## Random Events (Jumps)

To increase realism, the simulator occasionally injects larger price movements:

```python
import random

JUMP_PROBABILITY = 0.002     # ~0.2% chance per update per stock
JUMP_MAGNITUDE_MIN = 0.02    # 2% minimum jump
JUMP_MAGNITUDE_MAX = 0.05    # 5% maximum jump

def maybe_apply_jump(price: float) -> float:
    """Randomly apply a price jump."""
    if random.random() < JUMP_PROBABILITY:
        magnitude = random.uniform(JUMP_MAGNITUDE_MIN, JUMP_MAGNITUDE_MAX)
        direction = random.choice([-1, 1])
        return price * (1 + direction * magnitude)
    return price
```

## Simulator State

```python
from dataclasses import dataclass, field


@dataclass
class TickerState:
    config: StockConfig
    current_price: float
    previous_price: float
    session_open: float     # Price at session start (for session_change_pct)
    basic_price: float      # Reference price (= session_open for simulator)
    day_open: float
    day_high: float
    day_low: float
    day_volume: int = 0
```

## Full Simulator Implementation

```python
import asyncio
import math
import random
from datetime import datetime

import numpy as np


class SimulatorDataSource:
    """Simulates Vietnamese stock prices using GBM with sector correlation."""

    UPDATE_INTERVAL = 0.5  # seconds
    DT = UPDATE_INTERVAL / (252 * 6.5 * 3600)  # Convert to annualized time step

    def __init__(self, cache: "PriceCache", tickers: list[str]):
        self._cache = cache
        self._running = False
        self._task: asyncio.Task | None = None
        self._states: dict[str, TickerState] = {}
        self._configs: dict[str, StockConfig] = {
            s.ticker: s for s in DEFAULT_STOCKS
        }
        self._initial_tickers = tickers

    async def start(self) -> None:
        self._running = True
        for ticker in self._initial_tickers:
            self._init_ticker(ticker)
        self._task = asyncio.create_task(self._simulate_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    def _init_ticker(self, ticker: str) -> None:
        config = self._configs.get(ticker)
        if not config:
            # Unknown ticker: generate plausible defaults
            config = StockConfig(
                ticker=ticker,
                name=ticker,
                exchange="HOSE",
                sector="other",
                initial_price=random.uniform(20_000, 150_000),
                mu=0.05,
                sigma=0.30,
            )
            self._configs[ticker] = config

        # Add small random perturbation to initial price (+/- 2%)
        jitter = random.uniform(-0.02, 0.02)
        price = config.initial_price * (1 + jitter)
        price = round(price / 100) * 100  # Round to nearest 100 VND

        state = TickerState(
            config=config,
            current_price=price,
            previous_price=price,
            session_open=price,
            basic_price=price,
            day_open=price,
            day_high=price,
            day_low=price,
            day_volume=0,
        )
        self._states[ticker] = state
        self._write_to_cache(state)

    async def _simulate_loop(self) -> None:
        while self._running:
            self._tick()
            await asyncio.sleep(self.UPDATE_INTERVAL)

    def _tick(self) -> None:
        active_stocks = [
            self._configs[t] for t in self._states
            if t in self._configs
        ]
        if not active_stocks:
            return

        returns = generate_correlated_returns(active_stocks, self.DT)

        for ticker, log_return in returns.items():
            state = self._states.get(ticker)
            if not state:
                continue

            # Apply GBM return
            new_price = state.current_price * math.exp(log_return)

            # Apply random jump
            new_price = maybe_apply_jump(new_price)

            # Enforce price limits
            new_price = clamp_price(
                new_price, state.basic_price, state.config.exchange
            )

            # Round to nearest 100 VND (standard for VN stocks)
            new_price = round(new_price / 100) * 100

            # Generate random volume increment
            vol_increment = random.randint(100, 5000) * 100

            # Update state
            state.previous_price = state.current_price
            state.current_price = new_price
            state.day_high = max(state.day_high, new_price)
            state.day_low = min(state.day_low, new_price)
            state.day_volume += vol_increment

            self._write_to_cache(state)

    def _write_to_cache(self, state: TickerState) -> None:
        limit = PRICE_LIMITS.get(state.config.exchange, 0.07)
        update = PriceUpdate(
            ticker=state.config.ticker,
            price=state.current_price,
            previous_price=state.previous_price,
            open=state.day_open,
            high=state.day_high,
            low=state.day_low,
            volume=state.day_volume,
            timestamp=datetime.now().isoformat(),
            basic_price=state.basic_price,
            ceiling_price=round(state.basic_price * (1 + limit) / 100) * 100,
            floor_price=round(state.basic_price * (1 - limit) / 100) * 100,
            exchange=state.config.exchange,
        )
        self._cache.update(update)

    def get_price(self, ticker: str) -> "PriceUpdate | None":
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, "PriceUpdate"]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list["PriceUpdate"]:
        return self._cache.get_history(ticker, limit)

    def add_ticker(self, ticker: str) -> None:
        if ticker not in self._states:
            self._init_ticker(ticker)

    def remove_ticker(self, ticker: str) -> None:
        self._states.pop(ticker, None)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._states
```

## Calibration Notes

### Drift (mu) values

| Sector | mu Range | Rationale |
|--------|----------|-----------|
| Banking | 0.07-0.08 | Moderate growth, VN banking sector expanding |
| Technology | 0.10 | FPT is a growth stock in Vietnam |
| Consumer | 0.04-0.06 | Stable, defensive sector |
| Real estate | 0.03-0.04 | Lower drift, higher vol (VN property sector volatility) |
| Materials | 0.06 | Cyclical, tied to construction/infrastructure |
| Energy | 0.05 | State-owned, moderate growth |

### Volatility (sigma) values

| Sector | sigma Range | Rationale |
|--------|-------------|-----------|
| Banking | 0.30 | Moderate for VN market |
| Technology | 0.30 | Growth stock volatility |
| Consumer | 0.25-0.30 | Lower volatility, defensive |
| Real estate | 0.35 | Higher volatility for VN property |
| Materials | 0.40 | Cyclical, high sensitivity to steel/commodity prices |
| Energy | 0.25 | State influence dampens volatility |

### VN Market Context

- Vietnamese stocks trade in VND with no fractional pricing below 100 VND
- HOSE stocks typically range from 10,000 to 200,000+ VND per share
- Average daily volatility on VNINDEX is ~1.5-2.0%
- Sector correlations are higher than developed markets due to retail-dominated trading

## Price Rounding

Vietnamese stock prices are quoted in multiples of 100 VND (for HOSE stocks above 50,000 VND) or 10 VND (for lower-priced stocks). For simplicity, the simulator rounds all prices to the nearest 100 VND:

```python
price = round(price / 100) * 100
```

## Session Reset

The simulator runs continuously (no trading hours). The `basic_price` and `session_open` are set once at startup and remain fixed until the server restarts. This means:
- `session_change_pct` reflects change since server start
- Price limits are enforced relative to the initial reference price
- A server restart resets all prices to their configured initial values (with small jitter)

## Testing

Key properties to verify:
1. Prices are always positive
2. Prices stay within ceiling/floor limits
3. Prices are always multiples of 100 VND
4. Sector correlation: banking stocks (VCB, TCB) move together more than VCB+HPG
5. Volume is always a positive multiple of 100
6. Adding a new ticker seeds it immediately with a valid price
7. Removing a ticker clears it from the cache
