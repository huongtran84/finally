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


@pytest.mark.parametrize("source_class,kwargs", [
    (SimulatorSource, {}),
    (MassiveSource, {"api_key": "fake-key"}),
])
def test_add_and_get_tracked_tickers(source_class, kwargs):
    """Both sources support add_ticker and get_tracked_tickers."""
    source = source_class(**kwargs)
    source.add_ticker("TEST")
    assert "TEST" in source.get_tracked_tickers()


@pytest.mark.parametrize("source_class,kwargs", [
    (SimulatorSource, {}),
    (MassiveSource, {"api_key": "fake-key"}),
])
def test_remove_ticker_removes_from_list(source_class, kwargs):
    """Both sources support remove_ticker."""
    source = source_class(**kwargs)
    source.add_ticker("TEST")
    source.remove_ticker("TEST")
    assert "TEST" not in source.get_tracked_tickers()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_class,kwargs", [
    (SimulatorSource, {}),
])
async def test_start_and_stop_lifecycle(source_class, kwargs):
    """Both sources support start/stop lifecycle (only sim for speed)."""
    cache = PriceCache()
    source = source_class(**kwargs)
    await source.start(cache)
    await source.stop()  # should not raise


def test_factory_returns_simulator_without_key(monkeypatch):
    """Factory returns SimulatorSource when no MASSIVE_API_KEY is set."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

    from app.market_data.factory import create_market_data_source
    source = create_market_data_source()
    assert isinstance(source, SimulatorSource)


def test_factory_returns_massive_with_key(monkeypatch):
    """Factory returns MassiveSource when MASSIVE_API_KEY is set."""
    monkeypatch.setenv("MASSIVE_API_KEY", "test-api-key-12345")

    from app.market_data.factory import create_market_data_source
    source = create_market_data_source()
    assert isinstance(source, MassiveSource)
