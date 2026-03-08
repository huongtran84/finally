import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.market_data.massive import MassiveSource
from app.market_data.cache import PriceCache


# --- _extract_price static method tests (no network needed) ---

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


def test_extract_price_returns_none_when_no_session():
    result = {"ticker": "AAPL"}
    assert MassiveSource._extract_price(result) is None


def test_extract_price_last_trade_none_price():
    """last_trade.price of None should fall through to session."""
    result = {
        "ticker": "AAPL",
        "last_trade": {"price": None},
        "session": {"price": 192.0},
    }
    assert MassiveSource._extract_price(result) == 192.0


def test_extract_price_returns_float():
    """Ensure price is cast to float."""
    result = {
        "ticker": "AAPL",
        "last_trade": {"price": "192.53"},
    }
    price = MassiveSource._extract_price(result)
    assert isinstance(price, float)
    assert price == 192.53


# --- Ticker management tests ---

def test_add_ticker_upcases():
    source = MassiveSource(api_key="test-key")
    source.add_ticker("aapl")
    assert "AAPL" in source.get_tracked_tickers()


def test_add_multiple_tickers():
    source = MassiveSource(api_key="test-key")
    source.add_ticker("AAPL")
    source.add_ticker("MSFT")
    source.add_ticker("GOOGL")
    tracked = source.get_tracked_tickers()
    assert set(tracked) == {"AAPL", "MSFT", "GOOGL"}


def test_remove_ticker():
    source = MassiveSource(api_key="test-key")
    source.add_ticker("AAPL")
    source.add_ticker("MSFT")
    source.remove_ticker("AAPL")
    assert "AAPL" not in source.get_tracked_tickers()
    assert "MSFT" in source.get_tracked_tickers()


def test_remove_nonexistent_ticker_is_safe():
    source = MassiveSource(api_key="test-key")
    source.remove_ticker("NONEXISTENT")  # should not raise


def test_initial_tracked_tickers_is_empty():
    source = MassiveSource(api_key="test-key")
    assert source.get_tracked_tickers() == []


# --- Integration-style tests with mocked HTTP ---

@pytest.mark.asyncio
async def test_fetch_and_update_processes_results():
    """Mock the HTTP client to test that _fetch_and_update correctly updates cache."""
    source = MassiveSource(api_key="test-key", poll_interval=60.0)
    source.add_ticker("AAPL")
    source.add_ticker("MSFT")

    cache = PriceCache()
    source._cache = cache

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "OK",
        "results": [
            {
                "ticker": "AAPL",
                "last_trade": {"price": 192.53},
                "session": {},
            },
            {
                "ticker": "MSFT",
                "last_trade": {"price": 420.10},
                "session": {},
            },
        ],
    }
    mock_response.raise_for_status = MagicMock()

    import httpx
    source._client = AsyncMock(spec=httpx.AsyncClient)
    source._client.get = AsyncMock(return_value=mock_response)

    await source._fetch_and_update()

    aapl_tick = await cache.get_latest("AAPL")
    msft_tick = await cache.get_latest("MSFT")

    assert aapl_tick is not None
    assert aapl_tick.price == 192.53

    assert msft_tick is not None
    assert msft_tick.price == 420.10


@pytest.mark.asyncio
async def test_fetch_skips_bad_status():
    """If API returns non-OK status, cache should not be updated."""
    source = MassiveSource(api_key="test-key")
    source.add_ticker("AAPL")

    cache = PriceCache()
    source._cache = cache

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "ERROR",
        "results": [{"ticker": "AAPL", "last_trade": {"price": 200.0}, "session": {}}],
    }
    mock_response.raise_for_status = MagicMock()

    import httpx
    source._client = AsyncMock(spec=httpx.AsyncClient)
    source._client.get = AsyncMock(return_value=mock_response)

    await source._fetch_and_update()

    # Cache should have no prices since status was not OK
    assert await cache.get_latest("AAPL") is None


@pytest.mark.asyncio
async def test_fetch_skips_untracked_tickers():
    """Results for tickers not in self._tickers should be ignored."""
    source = MassiveSource(api_key="test-key")
    source.add_ticker("AAPL")  # only tracking AAPL

    cache = PriceCache()
    source._cache = cache

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "OK",
        "results": [
            {"ticker": "AAPL", "last_trade": {"price": 192.0}, "session": {}},
            {"ticker": "MSFT", "last_trade": {"price": 420.0}, "session": {}},  # not tracked
        ],
    }
    mock_response.raise_for_status = MagicMock()

    import httpx
    source._client = AsyncMock(spec=httpx.AsyncClient)
    source._client.get = AsyncMock(return_value=mock_response)

    await source._fetch_and_update()

    assert await cache.get_latest("AAPL") is not None
    assert await cache.get_latest("MSFT") is None


@pytest.mark.asyncio
async def test_poll_loop_skips_when_no_tickers():
    """Poll loop should not call the API when no tickers are tracked."""
    source = MassiveSource(api_key="test-key", poll_interval=0.1)

    cache = PriceCache()
    source._cache = cache

    import httpx
    source._client = AsyncMock(spec=httpx.AsyncClient)

    # Start and immediately stop
    await source.start(cache)
    import asyncio
    await asyncio.sleep(0.15)
    await source.stop()

    # No tickers tracked, so get should never be called
    source._client.get.assert_not_called()
