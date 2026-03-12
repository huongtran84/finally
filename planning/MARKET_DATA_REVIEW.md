# Market Data Backend — Code Review

**Date**: 2026-03-12
**Reviewer**: Claude Code
**Scope**: `backend/app/market/` and `backend/tests/test_market_*.py`

---

## 1. Test Results

```
111 tests collected
110 passed
  1 FAILED
```

### Failing Test

**`tests/test_market_simulator.py::TestRoundVnd::test_rounds_up`**

```
assert round_vnd(75_050.0) == 75_100.0
AssertionError: assert 75000 == 75100.0
```

**Root cause**: Python's built-in `round()` uses "banker's rounding" (round half to even), not standard round-half-up. `round(75050 / 100)` evaluates as `round(750.5)` which rounds to `750` (the nearest even integer), producing `75000` instead of the expected `75100`.

This is a genuine bug. In the Vietnamese stock market context, midpoint prices should consistently round up (industry convention). The fix is to replace `round(price / 100) * 100` with `math.floor(price / 100 + 0.5) * 100` in `simulator.py:130`.

**Impact**: Low probability in practice — this only triggers when a raw GBM price lands exactly at a ×50 VND boundary. However, the ceiling/floor rounding in `_write_to_cache()` uses the same `round_vnd()` helper, so in theory a ceiling or floor could be off by 100 VND in that edge case.

---

## 2. Architecture Review

The implementation follows the design spec closely and the overall architecture is well-conceived.

### Module Structure

```
backend/app/market/
├── models.py      # PriceUpdate, StockConfig, TickerState, DEFAULT_STOCKS, PRICE_LIMITS
├── cache.py       # PriceCache (thread-safe, in-memory)
├── base.py        # MarketDataSource (abstract interface)
├── simulator.py   # SimulatorDataSource (GBM)
├── vndirect.py    # VNDirectDataSource (REST polling)
├── factory.py     # create_market_data_source() + DEFAULT_TICKERS
└── __init__.py    # Public re-exports
```

Clean separation of concerns. The abstract interface (`base.py`) correctly decouples all downstream code from the specific data source. The `PriceCache` acts as the proper shared intermediary between producer and consumer.

### Interface Conformance

Both `SimulatorDataSource` and `VNDirectDataSource` fully implement all eight methods on `MarketDataSource`. The additional `get_session_open()` method on the abstract class (not in the original `MARKET_INTERFACE.md` spec but added during implementation) is a good enhancement — it cleanly exposes session-open prices for the SSE layer without coupling it to internal simulator state.

---

## 3. Code Quality

### Strengths

- **`PriceCache` is well-designed**: Thread-safe with a single `threading.Lock` covering all mutations. Initialises history and session-open on first write. `get_all()` returns an independent copy to prevent external mutation. `get_history()` correctly slices to preserve most-recent entries.

- **`SimulatorDataSource` is faithful to the GBM spec**: The sector-correlation model (`SECTOR_CORRELATION = 0.6`) correctly blends `sqrt(1 - ρ²)` for the idiosyncratic component, matching the formula in `MARKET_SIMULATOR.md`. Price limits, random jumps, and VND rounding are all applied in the correct sequence.

- **`VNDirectDataSource` is robust**: Individual ticker failures are caught and logged without breaking the poll cycle. The `_parse_record()` helper is exposed for unit testing without making HTTP calls. `httpx.AsyncClient` is used correctly as a context manager inside `_poll_loop()`.

- **Graceful lifecycle**: Both sources handle `stop()` when not started (no crash), await the cancelled task to suppress `CancelledError` propagation, and correctly set `_running = False` before cancelling.

- **Factory is minimal and correct**: `VNSTOCK_API_KEY` whitespace stripping prevents accidental activation from an empty-but-whitespace env var. `initial_tickers=None` falls back cleanly to `DEFAULT_TICKERS`.

- **Timestamps use UTC**: Both sources use `datetime.now(timezone.utc).isoformat()` — correct for an international context, consistent between simulator and real-data modes.

### Issues Found

#### Bug (Priority: Medium)

**`round_vnd()` uses banker's rounding** (described above in section 1).
File: `backend/app/market/simulator.py:130`

#### Design Note: `session_change_pct` property vs. `to_sse_dict()` argument

`PriceUpdate.session_change_pct` (in `models.py:25`) computes percentage change from `self.open` (the day-open price). However, the PLAN.md specifies that session change % should be the change from the ticker's price **when the backend started** (`session_open`), not the day open.

The `to_sse_dict(session_open: float)` method (in `models.py:41`) is the correct approach — it accepts the backend-start price explicitly, and the SSE streaming layer is expected to supply this from `cache.get_session_open()`. The `session_change_pct` property on `PriceUpdate` is a misleadingly-named secondary helper that computes something slightly different (day change rather than session change). It should either be removed or renamed to `day_change_pct` to avoid confusion when the frontend or future developers read the model.

#### Minor: Orphaned `import asyncio` in test file

`backend/tests/test_market_vndirect.py:342` has a bare `import asyncio` at the bottom of the file, outside of any function or class. It is unused and appears to be a leftover. No functional impact.

#### Minor: VNDirect — 15-second gap before first prices

`VNDirectDataSource.start()` launches the poll loop but does not immediately trigger a poll. The first prices from the real API will not appear until `_POLL_INTERVAL` (15 seconds) has elapsed. The simulator seeds prices synchronously during `start()`, so users switching from simulator to real-data mode will notice a 15-second blank watchlist at startup.

#### Minor: `is_tracking()` semantic difference between implementations

- `SimulatorDataSource.is_tracking(ticker)` → returns `ticker in self._states` (True only after price is seeded)
- `VNDirectDataSource.is_tracking(ticker)` → returns `ticker in self._tickers` (True as soon as added, even before first poll)

This is a reasonable design difference given the two sources have different initialisation semantics, but it is worth documenting. Adding a ticker to VNDirect reports `is_tracking=True` immediately but `get_price()` returns `None` until the next poll cycle. Downstream callers should guard against a `None` return from `get_price()` regardless.

---

## 4. Test Coverage Assessment

The test suite is comprehensive. Coverage highlights:

| Area | Coverage |
|------|----------|
| `PriceCache` | Excellent — update, get, session_open, history, remove, is_tracking, tickers, thread safety |
| `SimulatorDataSource` | Excellent — lifecycle, price invariants (positive, multiples of 100, within limits), add/remove, unknown tickers, history, session open |
| Helper functions | Good — `round_vnd`, `clamp_price`, `maybe_apply_jump`, `generate_correlated_returns` |
| `VNDirectDataSource` | Good — parse_record, tracking, read methods, `_poll_one` with HTTP mocking, error resilience, lifecycle |
| `PriceUpdate` model | Good — `session_change_pct`, `is_ceiling`, `is_floor`, `to_sse_dict` |
| Factory | Good — all four selection paths covered |

### Coverage Gaps

- **No sector correlation test**: The design doc calls for verifying that "banking stocks (VCB, TCB) move together more than VCB+HPG". The current test confirms returns are generated and finite, but does not statistically verify intra-sector correlation is higher than cross-sector correlation. This is harder to unit test deterministically but could use a fixed seed.

- **No test for initial VNDirect poll delay**: The 15-second gap before first real prices is not tested or documented as expected behaviour.

- **No test for ceiling/floor rounding edge case**: The `round_vnd` banker's rounding bug was caught by the test, but there is no corresponding test for ceiling/floor prices computed in `_write_to_cache()` being affected by the same bug.

- **No concurrent `add_ticker` / `_tick()` test**: While asyncio is single-threaded (eliminating true data races), a test adding and removing tickers between ticks would increase confidence in state consistency.

---

## 5. Compliance with PLAN.md

| Requirement | Status |
|-------------|--------|
| GBM simulation with configurable mu/sigma per stock | ✅ Implemented |
| Sector correlation (60%) | ✅ Implemented |
| Random jump events (0.2%, 2–5%) | ✅ Implemented |
| Prices rounded to nearest 100 VND | ✅ Implemented (with banker's rounding bug at midpoint) |
| HOSE ±7% / HNX ±10% / UPCoM ±15% price limits | ✅ Implemented |
| Session change % (change since backend started) | ✅ via `to_sse_dict(session_open)` |
| Rolling 50-price history per ticker | ✅ Implemented |
| `add_ticker` / `remove_ticker` dynamic management | ✅ Implemented |
| VNDirect REST polling (15s interval) | ✅ Implemented |
| Factory env-var selection | ✅ Implemented |
| Correct initial prices (VNM 75k, VCB 92k, etc.) | ✅ All 10 defaults match spec |
| All 10 default HOSE tickers | ✅ VNM, VCB, VIC, HPG, FPT, MWG, TCB, VHM, GAS, MSN |

---

## 6. Missing Artefacts

`CLAUDE.md` references `planning/MARKET_DATA_SUMMARY.md` and a `planning/archive/` folder as documentation created by the Market Data agent. Neither exists in the repository. This is a documentation gap — the review agent found only the five design docs (`MARKET_INTERFACE.md`, `MARKET_DATA_DESIGN.md`, `MARKET_SIMULATOR.md`, `VNDIRECT_API.md`, `SSI_API.md`) plus the main `PLAN.md`. Future agents relying on a summary doc will find it missing.

---

## 7. Summary

The Market Data backend is a high-quality implementation. The architecture cleanly matches the design spec, code is well-documented and consistently structured, and the 111-test suite provides strong coverage. There is one genuine bug (`round_vnd()` banker's rounding) caught by the test suite, and three minor issues (misleadingly-named `session_change_pct` property, VNDirect startup latency, orphaned import). None of the issues block the use of this component by downstream engineers.

### Recommended Fixes Before Integration

1. **Fix `round_vnd()`** — replace with explicit round-half-up arithmetic to ensure the test passes and eliminate the edge case in ceiling/floor rounding.
2. **Consider renaming `PriceUpdate.session_change_pct`** to `day_change_pct` to prevent confusion with the plan's concept of "session change % = change since backend started" (which is computed in `to_sse_dict`).
3. **Remove orphaned `import asyncio`** at end of `test_market_vndirect.py`.

### Optional Improvements

4. Initial poll on `VNDirectDataSource.start()` to eliminate the 15-second blank period.
5. Document the `is_tracking()` semantic difference between simulator and VNDirect in docstrings.
6. Create `planning/MARKET_DATA_SUMMARY.md` to fulfil the CLAUDE.md reference.
