"""Unit tests for the VNDirect market data source."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.market.cache import PriceCache
from app.market.models import PriceUpdate
from app.market.vndirect import VNDirectDataSource

# ---------------------------------------------------------------------------
# Sample VNDirect API response fixture
# ---------------------------------------------------------------------------

SAMPLE_RECORD = {
    "code": "VNM",
    "date": "2025-01-02",
    "time": "15:00:00",
    "floor": "HOSE",
    "type": "STOCK",
    "basicPrice": 75000,
    "ceilingPrice": 80250,
    "floorPrice": 69750,
    "open": 75500,
    "high": 76200,
    "low": 74800,
    "close": 75900,
    "average": 75600,
    "adOpen": 75500,
    "adHigh": 76200,
    "adLow": 74800,
    "adClose": 75900,
    "adAverage": 75600,
    "nmVolume": 1234567,
    "nmValue": 93380000000,
    "ptVolume": 50000,
    "ptValue": 3790000000,
    "change": 900,
    "adChange": 900,
    "pctChange": 1.2,
}

SAMPLE_RESPONSE = {
    "currentPage": 1,
    "size": 1,
    "totalElements": 1,
    "totalPages": 1,
    "data": [SAMPLE_RECORD],
}

EMPTY_RESPONSE = {
    "currentPage": 1,
    "size": 0,
    "totalElements": 0,
    "totalPages": 0,
    "data": [],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_source(
    tickers: list[str] | None = None,
    poll_interval: int = 15,
) -> tuple[PriceCache, VNDirectDataSource]:
    cache = PriceCache()
    source = VNDirectDataSource(
        cache,
        tickers or ["VNM"],
        poll_interval=poll_interval,
        api_url="https://finfo-api.vndirect.com.vn/v4/stock_prices/",
    )
    return cache, source


# ---------------------------------------------------------------------------
# _parse_record
# ---------------------------------------------------------------------------

class TestParseRecord:
    def test_parses_all_fields(self):
        cache, source = _make_source()
        update = source._parse_record(SAMPLE_RECORD, previous_price=75_000.0)
        assert update.ticker == "VNM"
        assert update.price == 75_900.0
        assert update.previous_price == 75_000.0
        assert update.open == 75_500.0
        assert update.high == 76_200.0
        assert update.low == 74_800.0
        assert update.volume == 1_234_567
        assert update.basic_price == 75_000.0
        assert update.ceiling_price == 80_250.0
        assert update.floor_price == 69_750.0
        assert update.exchange == "HOSE"

    def test_timestamp_is_iso_format(self):
        cache, source = _make_source()
        update = source._parse_record(SAMPLE_RECORD, previous_price=75_000.0)
        # Should be parseable as ISO 8601
        datetime.fromisoformat(update.timestamp)

    def test_uses_provided_previous_price(self):
        cache, source = _make_source()
        update = source._parse_record(SAMPLE_RECORD, previous_price=99_000.0)
        assert update.previous_price == 99_000.0


# ---------------------------------------------------------------------------
# add_ticker / remove_ticker / is_tracking
# ---------------------------------------------------------------------------

class TestVNDirectTracking:
    def test_initial_tickers_are_tracked(self):
        _, source = _make_source(["VNM", "VCB"])
        assert source.is_tracking("VNM")
        assert source.is_tracking("VCB")

    def test_add_ticker_starts_tracking(self):
        _, source = _make_source([])
        source.add_ticker("FPT")
        assert source.is_tracking("FPT")

    def test_remove_ticker_stops_tracking(self):
        cache, source = _make_source(["VNM"])
        source.remove_ticker("VNM")
        assert not source.is_tracking("VNM")

    def test_remove_ticker_purges_cache(self):
        cache, source = _make_source(["VNM"])
        # Manually insert a price into the cache
        cache.update(
            PriceUpdate(
                ticker="VNM",
                price=75_000.0,
                previous_price=74_500.0,
                open=74_000.0,
                high=76_000.0,
                low=73_000.0,
                volume=500_000,
                timestamp=datetime.now(timezone.utc).isoformat(),
                basic_price=74_000.0,
                ceiling_price=79_180.0,
                floor_price=68_820.0,
                exchange="HOSE",
            )
        )
        source.remove_ticker("VNM")
        assert cache.get("VNM") is None

    def test_remove_nonexistent_ticker_does_not_raise(self):
        _, source = _make_source([])
        source.remove_ticker("NONEXISTENT")  # Should not raise


# ---------------------------------------------------------------------------
# get_price / get_all_prices / get_history / get_session_open
# ---------------------------------------------------------------------------

class TestVNDirectRead:
    def test_get_price_returns_none_when_cache_empty(self):
        _, source = _make_source(["VNM"])
        assert source.get_price("VNM") is None

    def test_get_all_prices_returns_empty_dict_initially(self):
        _, source = _make_source(["VNM"])
        assert source.get_all_prices() == {}

    def test_get_history_returns_empty_list_initially(self):
        _, source = _make_source(["VNM"])
        assert source.get_history("VNM") == []

    def test_get_session_open_returns_zero_initially(self):
        _, source = _make_source(["VNM"])
        assert source.get_session_open("VNM") == 0.0


# ---------------------------------------------------------------------------
# _poll_one (via respx HTTP mocking)
# ---------------------------------------------------------------------------

class TestVNDirectPollOne:
    @pytest.mark.asyncio
    async def test_poll_one_updates_cache_on_success(self):
        cache, source = _make_source(["VNM"])
        with respx.mock(assert_all_called=False) as mock:
            mock.get(source.API_URL).mock(
                return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
            )
            async with httpx.AsyncClient() as client:
                await source._poll_one(client, "VNM", "2025-01-02")

        update = cache.get("VNM")
        assert update is not None
        assert update.price == 75_900.0
        assert update.ticker == "VNM"

    @pytest.mark.asyncio
    async def test_poll_one_no_op_when_empty_data(self):
        cache, source = _make_source(["VNM"])
        with respx.mock(assert_all_called=False) as mock:
            mock.get(source.API_URL).mock(
                return_value=httpx.Response(200, json=EMPTY_RESPONSE)
            )
            async with httpx.AsyncClient() as client:
                await source._poll_one(client, "VNM", "2025-01-02")

        # Cache should remain empty
        assert cache.get("VNM") is None

    @pytest.mark.asyncio
    async def test_poll_one_uses_cached_price_as_previous(self):
        cache, source = _make_source(["VNM"])

        # Pre-populate the cache with an older price
        cache.update(
            PriceUpdate(
                ticker="VNM",
                price=74_500.0,
                previous_price=74_000.0,
                open=74_000.0,
                high=75_000.0,
                low=73_500.0,
                volume=500_000,
                timestamp=datetime.now(timezone.utc).isoformat(),
                basic_price=74_000.0,
                ceiling_price=79_180.0,
                floor_price=68_820.0,
                exchange="HOSE",
            )
        )

        with respx.mock(assert_all_called=False) as mock:
            mock.get(source.API_URL).mock(
                return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
            )
            async with httpx.AsyncClient() as client:
                await source._poll_one(client, "VNM", "2025-01-02")

        update = cache.get("VNM")
        assert update.previous_price == 74_500.0

    @pytest.mark.asyncio
    async def test_poll_one_uses_basic_price_as_previous_when_no_cache(self):
        cache, source = _make_source(["VNM"])
        with respx.mock(assert_all_called=False) as mock:
            mock.get(source.API_URL).mock(
                return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
            )
            async with httpx.AsyncClient() as client:
                await source._poll_one(client, "VNM", "2025-01-02")

        update = cache.get("VNM")
        # basicPrice from SAMPLE_RECORD is 75000; no cache existed prior
        assert update.previous_price == 75_000.0


# ---------------------------------------------------------------------------
# _poll_all error resilience
# ---------------------------------------------------------------------------

class TestVNDirectPollAllResilience:
    @pytest.mark.asyncio
    async def test_error_on_one_ticker_does_not_prevent_others(self):
        """A network error for one ticker should not prevent others from being polled."""
        cache, source = _make_source(["VNM", "VCB"])
        source._tickers = {"VNM", "VCB"}

        call_count = 0

        async def mock_poll_one(client, ticker, date):
            nonlocal call_count
            call_count += 1
            if ticker == "VNM":
                raise httpx.ConnectError("Connection refused")
            # VCB succeeds → manually update cache
            cache.update(
                PriceUpdate(
                    ticker="VCB",
                    price=92_000.0,
                    previous_price=91_500.0,
                    open=91_000.0,
                    high=93_000.0,
                    low=90_500.0,
                    volume=800_000,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    basic_price=91_000.0,
                    ceiling_price=97_370.0,
                    floor_price=84_630.0,
                    exchange="HOSE",
                )
            )

        source._poll_one = mock_poll_one  # type: ignore[assignment]
        async with httpx.AsyncClient() as client:
            await source._poll_all(client)

        assert call_count == 2
        assert cache.get("VNM") is None   # Failed gracefully
        assert cache.get("VCB") is not None  # Succeeded


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------

class TestVNDirectLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        _, source = _make_source(["VNM"])
        # Override poll loop to avoid real HTTP calls
        source._poll_loop = AsyncMock()  # type: ignore[assignment]
        await source.start()
        assert source._task is not None
        await source.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        _, source = _make_source(["VNM"])

        async def _noop_loop():
            while True:
                await asyncio.sleep(1000)

        import asyncio as _asyncio
        source._poll_loop = _noop_loop  # type: ignore[assignment]
        await source.start()
        await source.stop()
        assert source._task is None or source._task.done()

    @pytest.mark.asyncio
    async def test_stop_when_not_started_does_not_raise(self):
        _, source = _make_source(["VNM"])
        await source.stop()  # Should not raise


import asyncio
