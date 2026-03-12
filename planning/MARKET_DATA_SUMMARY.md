# Market Data Backend — Summary

**Status**: Complete and tested (111/111 tests passing)

## What Was Built

The market data layer lives in `backend/app/market/` and consists of six modules:

| File | Responsibility |
|------|---------------|
| `models.py` | `PriceUpdate`, `StockConfig`, `TickerState`, `DEFAULT_STOCKS`, `PRICE_LIMITS` |
| `cache.py` | `PriceCache` — thread-safe in-memory store; rolling history; session-open tracking |
| `base.py` | `MarketDataSource` — abstract interface both implementations conform to |
| `simulator.py` | `SimulatorDataSource` — GBM-based price simulation with sector correlation |
| `vndirect.py` | `VNDirectDataSource` — VNDirect REST API polling (real market data) |
| `factory.py` | `create_market_data_source()` — selects implementation from env vars |

## Key Design Points

- **One interface, two implementations**: `SimulatorDataSource` and `VNDirectDataSource` both implement all eight methods on `MarketDataSource`. All downstream code (SSE, portfolio, API) is agnostic to the source.
- **Shared `PriceCache`**: a single background task writes prices; SSE and API layers read from the cache. Supports future multi-user scenarios without changes.
- **Session-open vs day-open**: `PriceUpdate.day_change_pct` is percentage change from the day's open price. The SSE "session change %" (change since backend started) is computed in `PriceUpdate.to_sse_dict(session_open)` using `PriceCache.get_session_open()`.
- **VND rounding**: `round_vnd()` uses `math.floor(price / 100 + 0.5) * 100` (round-half-up), not Python's default banker's rounding.
- **Price limits**: HOSE ±7%, HNX ±10%, UPCoM ±15%. Enforced every tick; ceiling shown in purple, floor in blue.
- **Dynamic ticker management**: `add_ticker()` / `remove_ticker()` add/drop tickers at runtime. Simulator seeds a price immediately; VNDirect picks it up on the next poll.

## Simulator Details

- **GBM** with per-stock annualised drift (`mu`) and volatility (`sigma`).
- **Sector correlation** (ρ = 0.6): stocks in the same sector share 60% of their return from a common sector shock.
- **Jump events**: 0.2% probability per tick; magnitude 2–5%; direction random.
- **Tick interval**: 500 ms.
- **10 default tickers** (all HOSE): VNM, VCB, VIC, HPG, FPT, MWG, TCB, VHM, GAS, MSN with realistic VNĐ starting prices.

## VNDirect Poller Details

- Polls `https://finfo-api.vndirect.com.vn/v4/stock_prices/` — no API key required.
- `VNSTOCK_API_KEY` env var acts as a feature flag only (non-empty → use real data).
- Poll interval: 15 seconds. First poll fires immediately on `start()` (no 15-second blank period at startup).
- Individual ticker failures are caught and logged; other tickers continue unaffected.

## `is_tracking()` Semantics

| Implementation | Returns True when… | `get_price()` guaranteed non-None? |
|---|---|---|
| `SimulatorDataSource` | Price has been seeded (synchronous on `add_ticker`) | Yes |
| `VNDirectDataSource` | Ticker is registered (before first poll completes) | No — guard against None |

## Test Coverage

Tests live in `backend/tests/test_market_*.py`:

- `test_market_cache.py` — 28 tests
- `test_market_factory.py` — 7 tests
- `test_market_models.py` — 17 tests
- `test_market_simulator.py` — 38 tests
- `test_market_vndirect.py` — 21 tests

Run with: `cd backend && python -m pytest tests/test_market_*.py -v`

## Environment Variables

```bash
VNSTOCK_API_KEY=   # Empty → simulator; any non-whitespace value → VNDirect
```
