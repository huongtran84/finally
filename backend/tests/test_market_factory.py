"""Unit tests for the market data source factory."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.factory import DEFAULT_TICKERS, create_market_data_source
from app.market.simulator import SimulatorDataSource
from app.market.vndirect import VNDirectDataSource


class TestCreateMarketDataSource:
    def _make_cache(self) -> PriceCache:
        return PriceCache()

    def test_returns_simulator_when_no_api_key(self):
        cache = self._make_cache()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VNSTOCK_API_KEY", None)
            source = create_market_data_source(cache)
        assert isinstance(source, SimulatorDataSource)

    def test_returns_simulator_when_api_key_empty_string(self):
        cache = self._make_cache()
        with patch.dict(os.environ, {"VNSTOCK_API_KEY": ""}, clear=False):
            source = create_market_data_source(cache)
        assert isinstance(source, SimulatorDataSource)

    def test_returns_simulator_when_api_key_whitespace(self):
        cache = self._make_cache()
        with patch.dict(os.environ, {"VNSTOCK_API_KEY": "   "}, clear=False):
            source = create_market_data_source(cache)
        assert isinstance(source, SimulatorDataSource)

    def test_returns_vndirect_when_api_key_set(self):
        cache = self._make_cache()
        with patch.dict(os.environ, {"VNSTOCK_API_KEY": "any-non-empty-value"}, clear=False):
            source = create_market_data_source(cache)
        assert isinstance(source, VNDirectDataSource)

    def test_uses_default_tickers_when_none_provided(self):
        cache = self._make_cache()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VNSTOCK_API_KEY", None)
            source = create_market_data_source(cache, initial_tickers=None)
        assert isinstance(source, SimulatorDataSource)
        assert source._initial_tickers == DEFAULT_TICKERS

    def test_uses_provided_tickers(self):
        cache = self._make_cache()
        custom = ["VNM", "FPT"]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VNSTOCK_API_KEY", None)
            source = create_market_data_source(cache, initial_tickers=custom)
        assert source._initial_tickers == custom

    def test_default_tickers_list(self):
        expected = ["VNM", "VCB", "VIC", "HPG", "FPT", "MWG", "TCB", "VHM", "GAS", "MSN"]
        assert DEFAULT_TICKERS == expected
