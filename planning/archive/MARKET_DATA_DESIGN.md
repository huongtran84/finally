# Thiết Kế Chi Tiết — Backend Dữ Liệu Thị Trường

Tài liệu này là hướng dẫn triển khai đầy đủ cho toàn bộ lớp dữ liệu thị trường của FinAlly. Nó tổng hợp, mở rộng và cung cấp code mẫu cho mọi thứ trong `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `VNDIRECT_API.md` và `SSI_API.md`. Đây là tài liệu tham chiếu duy nhất mà Backend Engineer cần để implement toàn bộ component này.

---

## Mục lục

1. [Tổng quan kiến trúc](#1-tổng-quan-kiến-trúc)
2. [Cấu trúc thư mục](#2-cấu-trúc-thư-mục)
3. [Data Models & Abstract Interface](#3-data-models--abstract-interface)
4. [Price Cache (Bộ đệm giá dùng chung)](#4-price-cache)
5. [Simulator — Triển khai GBM](#5-simulator--gbm)
6. [VNDirect REST Poller](#6-vndirect-rest-poller)
7. [SSI FastConnect Poller (Tùy chọn)](#7-ssi-fastconnect-poller)
8. [Factory Function & Dependency Injection](#8-factory-function--dependency-injection)
9. [Tích hợp FastAPI — Lifespan & SSE](#9-tích-hợp-fastapi)
10. [Tích hợp Watchlist](#10-tích-hợp-watchlist)
11. [Snapshot danh mục (background task)](#11-snapshot-danh-mục)
12. [Session Change %](#12-session-change-)
13. [pyproject.toml — Dependencies](#13-pyprojecttoml--dependencies)
14. [Testing Strategy](#14-testing-strategy)
15. [Quyết định thiết kế](#15-quyết-định-thiết-kế)

---

## 1. Tổng quan kiến trúc

```
┌─────────────────────────────────────────────────────────────┐
│  FastAPI Application                                         │
│                                                             │
│  ┌─────────────────┐   ┌──────────────────────────────┐    │
│  │  MarketDataSource│   │         PriceCache            │    │
│  │  (abstract)      │──▶│  (in-memory, thread-safe)    │    │
│  │                  │   │  prices: dict[str,PriceUpdate]│    │
│  │  SimulatorData   │   │  history: dict[str,deque]    │    │
│  │  VNDirectData    │   │  session_open: dict[str,float]│    │
│  │  SSIData         │   └──────────────┬───────────────┘    │
│  └─────────────────┘                  │                     │
│                                       ▼                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  SSE Endpoint /api/stream/prices                      │  │
│  │  - Polls cache mỗi 500ms                             │  │
│  │  - Chỉ emit khi giá thực sự thay đổi                │  │
│  │  - Kèm session_change_pct mỗi event                  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  REST Endpoints                                       │  │
│  │  GET /api/prices/history  → bootstrap sparklines      │  │
│  │  GET /api/watchlist       → giá hiện tại              │  │
│  │  POST/DELETE /api/watchlist → add_ticker/remove_ticker│  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Luồng dữ liệu tổng quát

```
[Simulator / VNDirect poll / SSI poll]
         │
         ▼  .update(PriceUpdate)
    [PriceCache]
         │
         ├──▶ SSE generator (push-on-change) ──▶ EventSource (browser)
         │
         ├──▶ GET /api/prices/history ──▶ sparkline bootstrap
         │
         └──▶ GET /api/watchlist ──▶ current prices
```

---

## 2. Cấu trúc thư mục

```
backend/
├── pyproject.toml
├── uv.lock
└── app/
    ├── main.py                  # FastAPI app + lifespan
    ├── config.py                # Settings từ env vars
    ├── database.py              # SQLite init + helpers
    ├── market/
    │   ├── __init__.py
    │   ├── models.py            # PriceUpdate, StockConfig, TickerState
    │   ├── cache.py             # PriceCache
    │   ├── base.py              # MarketDataSource (ABC)
    │   ├── simulator.py         # SimulatorDataSource
    │   ├── vndirect.py          # VNDirectDataSource
    │   ├── ssi.py               # SSIDataSource (optional)
    │   └── factory.py           # create_market_data_source()
    ├── routes/
    │   ├── __init__.py
    │   ├── stream.py            # SSE /api/stream/prices
    │   ├── prices.py            # /api/prices/history
    │   ├── watchlist.py         # /api/watchlist
    │   ├── portfolio.py         # /api/portfolio
    │   └── chat.py              # /api/chat
    └── tasks/
        ├── __init__.py
        └── snapshots.py         # Portfolio snapshot background task
```

---

## 3. Data Models & Abstract Interface

### `backend/app/market/models.py`

```python
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class PriceUpdate:
    """Một lần cập nhật giá cho một mã cổ phiếu."""
    ticker: str
    price: float              # Giá hiện tại (VNĐ)
    previous_price: float     # Giá lần cập nhật trước
    open: float               # Giá mở cửa phiên / ngày
    high: float               # Giá cao nhất
    low: float                # Giá thấp nhất
    volume: int               # Khối lượng giao dịch (cổ phiếu)
    timestamp: str            # ISO 8601 (UTC)
    basic_price: float        # Giá tham chiếu (gia tham chieu)
    ceiling_price: float      # Giá trần
    floor_price: float        # Giá sàn
    exchange: str             # "HOSE", "HNX", "UPCOM"

    @property
    def session_change_pct(self) -> float:
        """Thay đổi % so với giá mở cửa phiên."""
        if self.open == 0:
            return 0.0
        return round((self.price - self.open) / self.open * 100, 2)

    @property
    def is_ceiling(self) -> bool:
        return self.price >= self.ceiling_price

    @property
    def is_floor(self) -> bool:
        return self.price <= self.floor_price

    def to_sse_dict(self, session_open: float) -> dict:
        """Serialize ra dict cho SSE event."""
        session_change = 0.0
        if session_open > 0:
            session_change = round((self.price - session_open) / session_open * 100, 2)
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previousPrice": self.previous_price,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "basicPrice": self.basic_price,
            "ceilingPrice": self.ceiling_price,
            "floorPrice": self.floor_price,
            "exchange": self.exchange,
            "sessionChangePct": session_change,
            "isCeiling": self.is_ceiling,
            "isFloor": self.is_floor,
        }


@dataclass
class StockConfig:
    """Tham số cấu hình cho một mã cổ phiếu trong simulator."""
    ticker: str
    name: str
    exchange: str        # "HOSE", "HNX", "UPCOM"
    sector: str          # Dùng để tính tương quan ngành
    initial_price: float # Giá khởi điểm (VNĐ)
    mu: float            # Drift năm hóa
    sigma: float         # Volatility năm hóa


@dataclass
class TickerState:
    """Trạng thái nội bộ của simulator cho một mã."""
    config: StockConfig
    current_price: float
    previous_price: float
    session_open: float      # Giá lúc backend khởi động
    basic_price: float       # Giá tham chiếu (= session_open với simulator)
    day_open: float
    day_high: float
    day_low: float
    day_volume: int = 0


# ── Danh sách cổ phiếu mặc định ───────────────────────────────────────────────

DEFAULT_STOCKS: list[StockConfig] = [
    StockConfig("VNM", "Vinamilk",           "HOSE", "consumer",    75_000,  0.05, 0.25),
    StockConfig("VCB", "Vietcombank",         "HOSE", "banking",     92_000,  0.08, 0.30),
    StockConfig("VIC", "Vingroup",            "HOSE", "real_estate", 42_000,  0.03, 0.35),
    StockConfig("HPG", "Hoa Phat Group",      "HOSE", "materials",   28_000,  0.06, 0.40),
    StockConfig("FPT", "FPT Corporation",     "HOSE", "technology",  130_000, 0.10, 0.30),
    StockConfig("MWG", "The Gioi Di Dong",    "HOSE", "retail",      45_000,  0.04, 0.35),
    StockConfig("TCB", "Techcombank",         "HOSE", "banking",     25_000,  0.07, 0.30),
    StockConfig("VHM", "Vinhomes",            "HOSE", "real_estate", 38_000,  0.04, 0.35),
    StockConfig("GAS", "PetroVietnam Gas",    "HOSE", "energy",      85_000,  0.05, 0.25),
    StockConfig("MSN", "Masan Group",         "HOSE", "consumer",    68_000,  0.06, 0.30),
]

DEFAULT_STOCKS_MAP: dict[str, StockConfig] = {s.ticker: s for s in DEFAULT_STOCKS}

# ── Price limit rules ──────────────────────────────────────────────────────────

PRICE_LIMITS: dict[str, float] = {
    "HOSE":  0.07,   # ±7%
    "HNX":   0.10,   # ±10%
    "UPCOM": 0.15,   # ±15%
}
```

### `backend/app/market/base.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod

from app.market.models import PriceUpdate


class MarketDataSource(ABC):
    """Interface thống nhất cho mọi nguồn dữ liệu thị trường."""

    @abstractmethod
    async def start(self) -> None:
        """Bắt đầu tạo/polling giá."""

    @abstractmethod
    async def stop(self) -> None:
        """Dừng và giải phóng tài nguyên."""

    @abstractmethod
    def get_price(self, ticker: str) -> PriceUpdate | None:
        """Giá mới nhất của một mã. None nếu chưa track."""

    @abstractmethod
    def get_all_prices(self) -> dict[str, PriceUpdate]:
        """Tất cả giá mới nhất hiện đang track."""

    @abstractmethod
    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        """Lịch sử giá rolling (gần nhất cuối list)."""

    @abstractmethod
    def get_session_open(self, ticker: str) -> float:
        """Giá lúc phiên (session) bắt đầu — dùng tính session_change_pct."""

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """Bắt đầu track một mã mới."""

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """Dừng track và xóa khỏi cache."""

    @abstractmethod
    def is_tracking(self, ticker: str) -> bool:
        """Kiểm tra xem mã có đang được theo dõi không."""
```

---

## 4. Price Cache

### `backend/app/market/cache.py`

```python
from __future__ import annotations

import threading
from collections import deque

from app.market.models import PriceUpdate


class PriceCache:
    """
    Bộ đệm giá in-memory, thread-safe.

    - Lưu giá mới nhất của từng mã (prices)
    - Lưu lịch sử rolling 50 giá (history) để bootstrap sparklines
    - Lưu giá lúc phiên bắt đầu (session_open) để tính session_change_pct
    """

    def __init__(self, history_size: int = 50):
        self._history_size = history_size
        self._prices: dict[str, PriceUpdate] = {}
        self._history: dict[str, deque[PriceUpdate]] = {}
        self._session_open: dict[str, float] = {}
        self._lock = threading.Lock()

    # ── Write ──────────────────────────────────────────────────────────────────

    def update(self, update: PriceUpdate) -> None:
        """Ghi một bản cập nhật giá mới."""
        with self._lock:
            self._prices[update.ticker] = update
            if update.ticker not in self._history:
                self._history[update.ticker] = deque(maxlen=self._history_size)
            self._history[update.ticker].append(update)

    def set_session_open(self, ticker: str, price: float) -> None:
        """Đặt giá phiên (gọi một lần khi khởi tạo mỗi ticker)."""
        with self._lock:
            if ticker not in self._session_open:
                self._session_open[ticker] = price

    def remove(self, ticker: str) -> None:
        """Xóa hoàn toàn dữ liệu của một mã."""
        with self._lock:
            self._prices.pop(ticker, None)
            self._history.pop(ticker, None)
            self._session_open.pop(ticker, None)

    # ── Read ───────────────────────────────────────────────────────────────────

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        with self._lock:
            return dict(self._prices)

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        with self._lock:
            h = self._history.get(ticker, deque())
            return list(h)[-limit:]

    def get_session_open(self, ticker: str) -> float:
        with self._lock:
            return self._session_open.get(ticker, 0.0)

    def get_all_session_opens(self) -> dict[str, float]:
        with self._lock:
            return dict(self._session_open)

    def tracked_tickers(self) -> list[str]:
        with self._lock:
            return list(self._prices.keys())
```

---

## 5. Simulator — GBM

### `backend/app/market/simulator.py`

```python
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone

import numpy as np

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.models import (
    DEFAULT_STOCKS_MAP,
    PRICE_LIMITS,
    PriceUpdate,
    StockConfig,
    TickerState,
)

# ── Hằng số GBM ───────────────────────────────────────────────────────────────

# dt = 0.5 giây / (252 ngày giao dịch * 6.5 giờ * 3600 giây)
_TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
UPDATE_INTERVAL = 0.5                        # giây
DT = UPDATE_INTERVAL / _TRADING_SECONDS_PER_YEAR

SECTOR_CORRELATION = 0.6   # 60% chuyển động là do yếu tố ngành

JUMP_PROBABILITY   = 0.002   # ~0.2% mỗi lần cập nhật mỗi mã
JUMP_MIN           = 0.02    # 2%
JUMP_MAX           = 0.05    # 5%


# ── Helper functions ───────────────────────────────────────────────────────────

def _generate_correlated_returns(
    configs: list[StockConfig], dt: float
) -> dict[str, float]:
    """
    Sinh log-return cho toàn bộ mã với tương quan ngành.

    Mô hình: Z = rho * Z_sector + sqrt(1 - rho^2) * Z_idio
    - Z_sector: shock chung của ngành (giống nhau cho tất cả mã trong ngành)
    - Z_idio: shock riêng của từng mã
    """
    sectors = {c.sector for c in configs}
    sector_shocks: dict[str, float] = {s: float(np.random.normal()) for s in sectors}

    returns: dict[str, float] = {}
    rho = SECTOR_CORRELATION
    sqrt_1_minus_rho2 = math.sqrt(1.0 - rho ** 2)

    for c in configs:
        z = rho * sector_shocks[c.sector] + sqrt_1_minus_rho2 * float(np.random.normal())
        drift = (c.mu - 0.5 * c.sigma ** 2) * dt
        diffusion = c.sigma * math.sqrt(dt) * z
        returns[c.ticker] = drift + diffusion

    return returns


def _clamp_price(new_price: float, basic_price: float, exchange: str) -> float:
    """Kẹp giá trong biên độ dao động của sàn."""
    limit = PRICE_LIMITS.get(exchange, 0.07)
    ceiling = basic_price * (1 + limit)
    floor   = basic_price * (1 - limit)
    return max(floor, min(ceiling, new_price))


def _round_price(price: float) -> float:
    """Làm tròn về bội số 100 VNĐ (quy chuẩn HOSE/HNX)."""
    return round(price / 100) * 100


def _maybe_jump(price: float) -> float:
    """Áp dụng ngẫu nhiên một cú nhảy giá 2-5%."""
    if random.random() < JUMP_PROBABILITY:
        magnitude = random.uniform(JUMP_MIN, JUMP_MAX)
        direction = random.choice([-1, 1])
        return price * (1 + direction * magnitude)
    return price


# ── SimulatorDataSource ────────────────────────────────────────────────────────

class SimulatorDataSource(MarketDataSource):
    """
    Mô phỏng giá cổ phiếu Việt Nam dùng GBM có tương quan ngành.

    Chạy như asyncio background task, cập nhật cache mỗi 500ms.
    Biên độ dao động giá (±7% HOSE, ±10% HNX, ±15% UPCoM) được enforce mỗi tick.
    """

    def __init__(self, cache: PriceCache, tickers: list[str]):
        self._cache = cache
        self._states: dict[str, TickerState] = {}
        self._initial_tickers = list(tickers)
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        for ticker in self._initial_tickers:
            self._init_ticker(ticker)
        self._task = asyncio.create_task(self._simulate_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Initialization ─────────────────────────────────────────────────────────

    def _init_ticker(self, ticker: str) -> None:
        """Khởi tạo trạng thái ban đầu cho một mã."""
        config = DEFAULT_STOCKS_MAP.get(ticker)
        if config is None:
            # Mã không có trong danh sách mặc định — tạo config plausible
            config = StockConfig(
                ticker=ticker,
                name=ticker,
                exchange="HOSE",
                sector="other",
                initial_price=random.uniform(20_000, 150_000),
                mu=0.05,
                sigma=0.30,
            )

        # Jitter ±2% để các phiên không khởi đầu ở cùng giá
        jitter = random.uniform(-0.02, 0.02)
        price = _round_price(config.initial_price * (1 + jitter))

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
        self._cache.set_session_open(ticker, price)
        self._write_to_cache(state)

    # ── Simulation loop ────────────────────────────────────────────────────────

    async def _simulate_loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                pass  # Không để crash background task vì một lỗi nhỏ
            await asyncio.sleep(UPDATE_INTERVAL)

    def _tick(self) -> None:
        """Một bước mô phỏng — cập nhật tất cả mã đang track."""
        if not self._states:
            return

        configs = [
            s.config for s in self._states.values()
            if s.config is not None
        ]
        returns = _generate_correlated_returns(configs, DT)

        for ticker, log_return in returns.items():
            state = self._states.get(ticker)
            if state is None:
                continue

            # 1. Áp dụng GBM return
            new_price = state.current_price * math.exp(log_return)

            # 2. Random jump
            new_price = _maybe_jump(new_price)

            # 3. Enforce price limit
            new_price = _clamp_price(new_price, state.basic_price, state.config.exchange)

            # 4. Làm tròn 100 VNĐ
            new_price = _round_price(new_price)

            # 5. Cập nhật OHLV
            vol_increment = random.randint(1, 50) * 100  # lô 100 cp
            state.previous_price = state.current_price
            state.current_price  = new_price
            state.day_high        = max(state.day_high, new_price)
            state.day_low         = min(state.day_low, new_price)
            state.day_volume     += vol_increment

            self._write_to_cache(state)

    def _write_to_cache(self, state: TickerState) -> None:
        """Chuyển đổi TickerState → PriceUpdate và đẩy vào cache."""
        limit = PRICE_LIMITS.get(state.config.exchange, 0.07)
        update = PriceUpdate(
            ticker        = state.config.ticker,
            price         = state.current_price,
            previous_price= state.previous_price,
            open          = state.day_open,
            high          = state.day_high,
            low           = state.day_low,
            volume        = state.day_volume,
            timestamp     = datetime.now(timezone.utc).isoformat(),
            basic_price   = state.basic_price,
            ceiling_price = _round_price(state.basic_price * (1 + limit)),
            floor_price   = _round_price(state.basic_price * (1 - limit)),
            exchange      = state.config.exchange,
        )
        self._cache.update(update)

    # ── MarketDataSource interface ─────────────────────────────────────────────

    def get_price(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        return self._cache.get_history(ticker, limit)

    def get_session_open(self, ticker: str) -> float:
        return self._cache.get_session_open(ticker)

    def add_ticker(self, ticker: str) -> None:
        if ticker not in self._states:
            self._init_ticker(ticker)

    def remove_ticker(self, ticker: str) -> None:
        self._states.pop(ticker, None)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._states
```

---

## 6. VNDirect REST Poller

VNDirect finfo-api là API REST công khai (không cần auth), trả dữ liệu giá EOD và intraday.

### `backend/app/market/vndirect.py`

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.models import PRICE_LIMITS, PriceUpdate

VNDIRECT_URL = "https://finfo-api.vndirect.com.vn/v4/stock_prices/"
POLL_INTERVAL = 15.0  # giây — conservative cho unofficial API


class VNDirectDataSource(MarketDataSource):
    """
    Poll giá cổ phiếu từ VNDirect finfo-api (REST, không cần auth).

    Chiến lược:
    - Poll tuần tự từng mã mỗi 15 giây
    - Nếu API trả empty (ngoài giờ giao dịch) → giữ nguyên giá cuối
    - Retry với exponential backoff khi gặp lỗi mạng
    """

    def __init__(self, cache: PriceCache, tickers: list[str]):
        self._cache = cache
        self._tickers: set[str] = set(tickers)
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Poll loop ──────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Poll ngay lần đầu (không chờ 15 giây)
            await self._poll_all(client)
            while self._running:
                await asyncio.sleep(POLL_INTERVAL)
                if self._running:
                    await self._poll_all(client)

    async def _poll_all(self, client: httpx.AsyncClient) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        for ticker in list(self._tickers):
            try:
                await self._poll_one(client, ticker, today)
            except Exception:
                # Log lỗi trong production; skip ticker này cho chu kỳ hiện tại
                pass

    async def _poll_one(
        self, client: httpx.AsyncClient, ticker: str, date: str
    ) -> None:
        params = {
            "q":    f"code:{ticker}~date:gte:{date}~date:lte:{date}",
            "sort": "date",
            "size": 1,
            "page": 1,
        }
        resp = await client.get(VNDIRECT_URL, params=params)
        resp.raise_for_status()

        data = resp.json().get("data", [])
        if not data:
            # Ngoài giờ giao dịch hoặc không có dữ liệu ngày hôm nay
            # → không update cache, giữ giá cuối
            return

        record = data[0]

        # previous_price = giá hiện tại trong cache (trước khi update)
        prev = self._cache.get(ticker)
        previous_price = prev.price if prev else float(record.get("basicPrice", 0))

        # Lấy exchange từ field "floor" của VNDirect
        exchange = str(record.get("floor", "HOSE")).upper()
        # VNDirect dùng "UPCOM" không phải "UPCOM" — chuẩn hóa
        if exchange not in PRICE_LIMITS:
            exchange = "HOSE"

        update = PriceUpdate(
            ticker        = str(record["code"]),
            price         = float(record["close"]),
            previous_price= previous_price,
            open          = float(record["open"]),
            high          = float(record["high"]),
            low           = float(record["low"]),
            volume        = int(record.get("nmVolume", 0)),
            timestamp     = datetime.now(timezone.utc).isoformat(),
            basic_price   = float(record["basicPrice"]),
            ceiling_price = float(record["ceilingPrice"]),
            floor_price   = float(record["floorPrice"]),
            exchange      = exchange,
        )

        # Đặt session_open lần đầu (nếu chưa có)
        self._cache.set_session_open(ticker, update.price)
        self._cache.update(update)

    # ── MarketDataSource interface ─────────────────────────────────────────────

    def get_price(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        return self._cache.get_history(ticker, limit)

    def get_session_open(self, ticker: str) -> float:
        return self._cache.get_session_open(ticker)

    def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker)
        # Cache chưa có giá — sẽ được poll trong chu kỳ tiếp theo

    def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._tickers
```

### Ví dụ Response VNDirect

```json
{
  "data": [{
    "code": "VNM",
    "date": "2026-03-12",
    "time": "14:30:00",
    "floor": "HOSE",
    "type": "STOCK",
    "basicPrice": 75000,
    "ceilingPrice": 80200,
    "floorPrice": 69800,
    "open": 75500,
    "high": 76200,
    "low": 74800,
    "close": 75900,
    "nmVolume": 1234567,
    "change": 900,
    "pctChange": 1.2
  }]
}
```

---

## 7. SSI FastConnect Poller

SSI cung cấp API chính thức có auth. Dùng khi muốn độ tin cậy cao hơn VNDirect.

### `backend/app/market/ssi.py`

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.models import PRICE_LIMITS, PriceUpdate

SSI_BASE_URL  = "https://fc-data.ssi.com.vn"
TOKEN_ENDPOINT = f"{SSI_BASE_URL}/api/v2/Market/AccessToken"
PRICE_ENDPOINT = f"{SSI_BASE_URL}/api/v2/Market/DailyStockPrice"
POLL_INTERVAL  = 15.0


class SSIDataSource(MarketDataSource):
    """
    Poll giá cổ phiếu từ SSI FastConnect Data API (REST có Bearer auth).

    Cần VNSTOCK_API_KEY ở dạng "consumerID:consumerSecret" trong env.
    Ví dụ: VNSTOCK_API_KEY="c058f557...:144cac45..."
    """

    def __init__(self, cache: PriceCache, tickers: list[str], api_key: str):
        self._cache = cache
        self._tickers: set[str] = set(tickers)
        self._running = False
        self._task: asyncio.Task | None = None

        # Parse "consumerID:consumerSecret"
        parts = api_key.split(":", 1)
        self._consumer_id     = parts[0].strip()
        self._consumer_secret = parts[1].strip() if len(parts) > 1 else ""
        self._access_token: str | None = None

    # ── Auth ───────────────────────────────────────────────────────────────────

    async def _refresh_token(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(TOKEN_ENDPOINT, json={
            "consumerID":     self._consumer_id,
            "consumerSecret": self._consumer_secret,
        })
        resp.raise_for_status()
        self._access_token = resp.json()["data"]["accessToken"]

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            return {}
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Poll loop ──────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await self._refresh_token(client)
            await self._poll_all(client)
            while self._running:
                await asyncio.sleep(POLL_INTERVAL)
                if self._running:
                    await self._poll_all(client)

    async def _poll_all(self, client: httpx.AsyncClient) -> None:
        today_dd_mm_yyyy = datetime.now().strftime("%d/%m/%Y")
        for ticker in list(self._tickers):
            try:
                await self._poll_one(client, ticker, today_dd_mm_yyyy)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    # Token hết hạn → refresh và thử lại
                    await self._refresh_token(client)
                    await self._poll_one(client, ticker, today_dd_mm_yyyy)
            except Exception:
                pass

    async def _poll_one(
        self, client: httpx.AsyncClient, ticker: str, date: str
    ) -> None:
        params = {
            "Symbol":     ticker,
            "Market":     "HOSE",  # Có thể lookup từ metadata
            "FromDate":   date,
            "ToDate":     date,
            "PageIndex":  1,
            "PageSize":   1,
        }
        resp = await client.get(
            PRICE_ENDPOINT,
            params=params,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()

        payload = resp.json()
        data    = payload.get("data", [])
        if not data:
            return

        record         = data[0]
        prev           = self._cache.get(ticker)
        previous_price = prev.price if prev else float(record.get("BasicPrice", 0))

        update = PriceUpdate(
            ticker        = ticker,
            price         = float(record["Close"]),
            previous_price= previous_price,
            open          = float(record["Open"]),
            high          = float(record["High"]),
            low           = float(record["Low"]),
            volume        = int(record.get("TotalMatchVolume", 0)),
            timestamp     = datetime.now(timezone.utc).isoformat(),
            basic_price   = float(record["BasicPrice"]),
            ceiling_price = float(record["CeilingPrice"]),
            floor_price   = float(record["FloorPrice"]),
            exchange      = str(record.get("Market", "HOSE")).upper(),
        )
        self._cache.set_session_open(ticker, update.price)
        self._cache.update(update)

    # ── MarketDataSource interface ─────────────────────────────────────────────

    def get_price(self, ticker: str) -> PriceUpdate | None:
        return self._cache.get(ticker)

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return self._cache.get_all()

    def get_history(self, ticker: str, limit: int = 50) -> list[PriceUpdate]:
        return self._cache.get_history(ticker, limit)

    def get_session_open(self, ticker: str) -> float:
        return self._cache.get_session_open(ticker)

    def add_ticker(self, ticker: str) -> None:
        self._tickers.add(ticker)

    def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self._cache.remove(ticker)

    def is_tracking(self, ticker: str) -> bool:
        return ticker in self._tickers
```

---

## 8. Factory Function & Dependency Injection

### `backend/app/market/factory.py`

```python
from __future__ import annotations

import os

from app.market.base import MarketDataSource
from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource
from app.market.ssi import SSIDataSource
from app.market.vndirect import VNDirectDataSource


def create_market_data_source(
    cache: PriceCache,
    initial_tickers: list[str],
) -> MarketDataSource:
    """
    Tạo MarketDataSource phù hợp dựa trên biến môi trường.

    Logic lựa chọn:
    1. Nếu VNSTOCK_API_KEY chứa ":" → SSI FastConnect (consumerID:consumerSecret)
    2. Nếu VNSTOCK_API_KEY không rỗng → VNDirect REST (dùng key làm feature flag)
    3. Không có key → Simulator GBM

    Tại sao vậy?
    - VNDirect không cần key, nhưng env var được dùng như feature flag
      để signal "dùng dữ liệu thật".
    - Nếu key có dạng "id:secret" → giả định là SSI credentials.
    """
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()

    if api_key:
        if ":" in api_key:
            # SSI FastConnect: VNSTOCK_API_KEY="consumerID:consumerSecret"
            return SSIDataSource(cache, initial_tickers, api_key)
        else:
            # VNDirect: bất kỳ giá trị không rỗng nào → kích hoạt real data
            return VNDirectDataSource(cache, initial_tickers)

    return SimulatorDataSource(cache, initial_tickers)
```

### `backend/app/config.py`

```python
from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    vnstock_api_key: str  = os.getenv("VNSTOCK_API_KEY", "").strip()
    llm_model: str        = os.getenv("LLM_MODEL", "openrouter/openai/gpt-oss-120b")
    llm_mock: bool        = os.getenv("LLM_MOCK", "false").lower() == "true"
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    db_path: str          = os.getenv("DB_PATH", "/app/db/finally.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## 9. Tích hợp FastAPI

### `backend/app/main.py`

```python
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db, get_watchlist_tickers
from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.tasks.snapshots import start_snapshot_task
from app.routes import stream, prices, watchlist, portfolio, chat

STATIC_DIR = "/app/static"   # Next.js export sau khi build Docker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Khởi tạo database (lazy init — tạo schema + seed nếu chưa có)
    await init_db()

    # 2. Lấy danh sách tickers từ DB (có thể khác DEFAULT nếu user đã thêm)
    tickers = await get_watchlist_tickers(user_id="default")

    # 3. Khởi tạo cache và market data source
    cache  = PriceCache(history_size=50)
    source = create_market_data_source(cache, tickers)

    # Attach vào app.state để routes có thể dùng
    app.state.price_cache  = cache
    app.state.market_data  = source

    # 4. Bắt đầu market data
    await source.start()

    # 5. Bắt đầu portfolio snapshot task (mỗi 30 giây)
    snapshot_task = asyncio.create_task(start_snapshot_task(app))
    app.state.snapshot_task = snapshot_task

    yield  # ← Server đang chạy

    # Teardown
    await source.stop()
    snapshot_task.cancel()
    try:
        await snapshot_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="FinAlly API", lifespan=lifespan)

# API routes
app.include_router(stream.router,     prefix="/api")
app.include_router(prices.router,     prefix="/api")
app.include_router(watchlist.router,  prefix="/api")
app.include_router(portfolio.router,  prefix="/api")
app.include_router(chat.router,       prefix="/api")

# Health check
@app.get("/api/health")
async def health():
    return {"status": "ok"}

# Serve static Next.js export (must be last — catches all unmatched paths)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

### SSE Endpoint — `backend/app/routes/stream.py`

```python
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.market.cache import PriceCache

router = APIRouter()


@router.get("/stream/prices")
async def stream_prices(request: Request):
    """
    SSE stream của giá cổ phiếu.

    - Push-on-change: chỉ emit khi giá thực sự thay đổi
    - Kèm session_change_pct và ceiling/floor status
    - Client dùng native EventSource API (tự reconnect)
    """
    cache: PriceCache = request.app.state.price_cache

    async def event_generator():
        last_seen: dict[str, float] = {}
        while True:
            # Kiểm tra client đã disconnect chưa
            if await request.is_disconnected():
                break

            prices      = cache.get_all()
            session_opens = cache.get_all_session_opens()

            for ticker, update in prices.items():
                # Chỉ emit khi giá thay đổi
                if last_seen.get(ticker) == update.price:
                    continue

                last_seen[ticker] = update.price
                session_open = session_opens.get(ticker, update.open)
                data = json.dumps(update.to_sse_dict(session_open))
                yield f"data: {data}\n\n"

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection":    "keep-alive",
            "X-Accel-Buffering": "no",  # Tắt buffering ở nginx
        },
    )
```

### Prices History Endpoint — `backend/app/routes/prices.py`

```python
from __future__ import annotations

from fastapi import APIRouter, Request

from app.market.cache import PriceCache

router = APIRouter()


@router.get("/prices/history")
async def get_price_history(request: Request):
    """
    Trả về lịch sử giá rolling (tối đa 50 điểm) cho tất cả tickers.
    Dùng để bootstrap sparklines khi trang load.

    Response format:
    {
      "VNM": [
        {"price": 75000, "timestamp": "...", "sessionChangePct": 0.5},
        ...
      ],
      ...
    }
    """
    cache: PriceCache = request.app.state.price_cache
    session_opens     = cache.get_all_session_opens()

    result: dict = {}
    for ticker in cache.tracked_tickers():
        history      = cache.get_history(ticker, limit=50)
        session_open = session_opens.get(ticker, 0.0)
        result[ticker] = [
            {
                "price":           h.price,
                "timestamp":       h.timestamp,
                "sessionChangePct": round(
                    (h.price - session_open) / session_open * 100, 2
                ) if session_open > 0 else 0.0,
            }
            for h in history
        ]

    return result
```

---

## 10. Tích hợp Watchlist

Khi thêm/xóa mã khỏi watchlist, market data source phải được notify ngay.

### `backend/app/routes/watchlist.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.database import db_add_watchlist, db_remove_watchlist, db_get_watchlist
from app.market.base import MarketDataSource

router = APIRouter()


class WatchlistAdd(BaseModel):
    ticker: str


@router.get("/watchlist")
async def get_watchlist(request: Request):
    """Trả về watchlist kèm giá hiện tại."""
    source: MarketDataSource = request.app.state.market_data
    rows = await db_get_watchlist(user_id="default")

    result = []
    for row in rows:
        ticker = row["ticker"]
        update = source.get_price(ticker)
        session_open = source.get_session_open(ticker)

        entry = {
            "ticker":      ticker,
            "addedAt":     row["added_at"],
        }
        if update:
            session_change = 0.0
            if session_open > 0:
                session_change = round(
                    (update.price - session_open) / session_open * 100, 2
                )
            entry.update({
                "price":           update.price,
                "previousPrice":   update.previous_price,
                "basicPrice":      update.basic_price,
                "ceilingPrice":    update.ceiling_price,
                "floorPrice":      update.floor_price,
                "exchange":        update.exchange,
                "sessionChangePct":session_change,
                "isCeiling":       update.is_ceiling,
                "isFloor":         update.is_floor,
            })
        result.append(entry)

    return result


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(request: Request, body: WatchlistAdd):
    ticker = body.ticker.upper().strip()

    if not ticker or len(ticker) > 10:
        raise HTTPException(status_code=400, detail="Mã cổ phiếu không hợp lệ")

    source: MarketDataSource = request.app.state.market_data

    try:
        await db_add_watchlist(
            user_id="default",
            ticker=ticker,
            id=str(uuid.uuid4()),
            added_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        raise HTTPException(status_code=409, detail=f"{ticker} đã có trong watchlist")

    # Notify market data source → bắt đầu track ngay
    source.add_ticker(ticker)

    return {"ticker": ticker, "status": "added"}


@router.delete("/watchlist/{ticker}", status_code=200)
async def remove_from_watchlist(request: Request, ticker: str):
    ticker = ticker.upper().strip()
    source: MarketDataSource = request.app.state.market_data

    await db_remove_watchlist(user_id="default", ticker=ticker)

    # Notify market data source → dừng track và xóa khỏi cache
    source.remove_ticker(ticker)

    return {"ticker": ticker, "status": "removed"}
```

---

## 11. Snapshot Danh Mục

Background task ghi snapshot giá trị danh mục mỗi 30 giây và sau mỗi giao dịch.

### `backend/app/tasks/snapshots.py`

```python
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from app.database import (
    db_get_portfolio,
    db_insert_snapshot,
    db_prune_snapshots,
)
from app.market.base import MarketDataSource


async def record_snapshot(app) -> None:
    """Ghi một snapshot giá trị danh mục vào DB."""
    source: MarketDataSource = app.state.market_data
    positions, cash = await db_get_portfolio(user_id="default")

    # Tính tổng giá trị danh mục
    total_value = cash
    for pos in positions:
        price_update = source.get_price(pos["ticker"])
        if price_update:
            total_value += pos["quantity"] * price_update.price

    await db_insert_snapshot(
        id=str(uuid.uuid4()),
        user_id="default",
        total_value=total_value,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    # Giữ tối đa 500 snapshots
    await db_prune_snapshots(user_id="default", keep=500)


async def start_snapshot_task(app) -> None:
    """Chạy vòng lặp ghi snapshot mỗi 30 giây."""
    while True:
        try:
            await record_snapshot(app)
        except Exception:
            pass
        await asyncio.sleep(30)
```

---

## 12. Session Change %

`session_change_pct` được tính bằng công thức:

```
session_change_pct = (current_price - session_open) / session_open * 100
```

Trong đó `session_open` là giá lúc backend khởi động (đối với simulator) hoặc giá poll đầu tiên trong ngày (đối với VNDirect/SSI).

**Tại sao không dùng "daily change %"?**
- Simulator không có khái niệm "ngày giao dịch"
- Chạy 24/7, không có reset lúc 9:00
- `session_change_pct` nhất quán cho cả hai chế độ (real + simulated)

Dữ liệu được gửi trong mỗi SSE event và trong response `/api/watchlist`.

---

## 13. pyproject.toml — Dependencies

```toml
[project]
name = "finally-backend"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "httpx>=0.27.0",
    "numpy>=1.26.0",
    "aiosqlite>=0.20.0",
    "pydantic>=2.7.0",
    "litellm>=1.40.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
ssi = [
    "ssi-fc-data>=1.0.0",  # Chỉ cần nếu dùng SSI SDK thay vì raw HTTP
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.12.0",
    "httpx>=0.27.0",  # httpx.AsyncClient dùng trong tests
]
```

---

## 14. Testing Strategy

### Unit Tests — Simulator (`tests/market/test_simulator.py`)

```python
import pytest
from app.market.cache import PriceCache
from app.market.simulator import (
    SimulatorDataSource,
    _clamp_price,
    _round_price,
    _generate_correlated_returns,
)
from app.market.models import DEFAULT_STOCKS


class TestPriceClamp:
    def test_ceiling_enforced(self):
        # HOSE: ±7%. basic=100_000 → ceiling=107_000
        assert _clamp_price(110_000, 100_000, "HOSE") == pytest.approx(107_000)

    def test_floor_enforced(self):
        assert _clamp_price(90_000, 100_000, "HOSE") == pytest.approx(93_000)

    def test_within_bounds_unchanged(self):
        assert _clamp_price(102_000, 100_000, "HOSE") == pytest.approx(102_000)

    def test_hnx_wider_band(self):
        # HNX: ±10%
        assert _clamp_price(115_000, 100_000, "HNX") == pytest.approx(110_000)

    def test_upcom_widest(self):
        # UPCOM: ±15%
        result = _clamp_price(120_000, 100_000, "UPCOM")
        assert result == pytest.approx(115_000)


class TestPriceRounding:
    def test_rounds_to_100(self):
        assert _round_price(75_350) == 75_400
        assert _round_price(75_249) == 75_200

    def test_already_rounded(self):
        assert _round_price(75_000) == 75_000


class TestSectorCorrelation:
    def test_banking_stocks_correlated(self):
        """VCB và TCB (cùng banking sector) phải có returns tương quan cao hơn VCB+HPG."""
        banking_stocks = [s for s in DEFAULT_STOCKS if s.sector == "banking"]
        assert len(banking_stocks) >= 2

        # Chạy nhiều lần và kiểm tra tương quan
        import numpy as np
        n = 1000
        dt = 0.5 / (252 * 6.5 * 3600)

        vcb_returns, tcb_returns, hpg_returns = [], [], []
        for _ in range(n):
            r = _generate_correlated_returns(DEFAULT_STOCKS, dt)
            vcb_returns.append(r["VCB"])
            tcb_returns.append(r["TCB"])
            hpg_returns.append(r["HPG"])

        corr_banking = float(np.corrcoef(vcb_returns, tcb_returns)[0, 1])
        corr_cross   = float(np.corrcoef(vcb_returns, hpg_returns)[0, 1])

        # VCB-TCB correlation >> VCB-HPG correlation
        assert corr_banking > corr_cross + 0.3, (
            f"Banking correlation {corr_banking:.2f} not much higher than "
            f"cross-sector {corr_cross:.2f}"
        )


@pytest.mark.asyncio
class TestSimulatorDataSource:
    async def test_prices_positive(self):
        cache  = PriceCache()
        source = SimulatorDataSource(cache, ["VNM", "VCB", "HPG"])
        await source.start()

        import asyncio
        await asyncio.sleep(0.6)  # Bỏ qua ít nhất 1 tick

        for ticker in ["VNM", "VCB", "HPG"]:
            update = source.get_price(ticker)
            assert update is not None
            assert update.price > 0

        await source.stop()

    async def test_prices_within_limits(self):
        cache  = PriceCache()
        source = SimulatorDataSource(cache, ["VNM"])
        await source.start()

        import asyncio
        for _ in range(20):
            await asyncio.sleep(0.5)
            update = source.get_price("VNM")
            if update:
                assert update.price <= update.ceiling_price
                assert update.price >= update.floor_price

        await source.stop()

    async def test_prices_multiples_of_100(self):
        cache  = PriceCache()
        source = SimulatorDataSource(cache, ["VNM"])
        await source.start()

        import asyncio
        await asyncio.sleep(1.0)
        update = source.get_price("VNM")
        assert update is not None
        assert update.price % 100 == 0

        await source.stop()

    async def test_add_remove_ticker(self):
        cache  = PriceCache()
        source = SimulatorDataSource(cache, ["VNM"])
        await source.start()

        # Add new ticker
        source.add_ticker("DGW")
        import asyncio
        await asyncio.sleep(0.6)
        assert source.is_tracking("DGW")
        assert source.get_price("DGW") is not None

        # Remove ticker
        source.remove_ticker("VNM")
        assert not source.is_tracking("VNM")
        assert source.get_price("VNM") is None

        await source.stop()

    async def test_history_populated(self):
        cache  = PriceCache()
        source = SimulatorDataSource(cache, ["VNM"])
        await source.start()

        import asyncio
        await asyncio.sleep(3.0)  # ~6 ticks

        history = source.get_history("VNM", limit=50)
        assert len(history) >= 5  # Có ít nhất 5 điểm lịch sử

        await source.stop()


class TestPriceCache:
    def test_update_and_get(self):
        from app.market.models import PriceUpdate
        cache = PriceCache()
        update = PriceUpdate(
            ticker="VNM", price=75_000, previous_price=74_900,
            open=74_500, high=75_100, low=74_400, volume=100_000,
            timestamp="2026-03-12T09:00:00+00:00",
            basic_price=75_000, ceiling_price=80_200, floor_price=69_800,
            exchange="HOSE",
        )
        cache.update(update)
        result = cache.get("VNM")
        assert result is not None
        assert result.price == 75_000

    def test_remove(self):
        from app.market.models import PriceUpdate
        cache = PriceCache()
        update = PriceUpdate(
            ticker="VNM", price=75_000, previous_price=74_900,
            open=74_500, high=75_100, low=74_400, volume=100_000,
            timestamp="2026-03-12T09:00:00+00:00",
            basic_price=75_000, ceiling_price=80_200, floor_price=69_800,
            exchange="HOSE",
        )
        cache.update(update)
        cache.remove("VNM")
        assert cache.get("VNM") is None

    def test_history_size_capped(self):
        from app.market.models import PriceUpdate
        cache = PriceCache(history_size=5)
        for i in range(10):
            cache.update(PriceUpdate(
                ticker="VNM", price=float(75_000 + i * 100),
                previous_price=float(75_000 + (i-1) * 100),
                open=75_000, high=76_000, low=74_000, volume=i * 100,
                timestamp=f"2026-03-12T09:00:0{i}+00:00",
                basic_price=75_000, ceiling_price=80_200, floor_price=69_800,
                exchange="HOSE",
            ))
        history = cache.get_history("VNM")
        assert len(history) == 5  # Capped at history_size
```

### Integration Test — SSE Endpoint (`tests/routes/test_stream.py`)

```python
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_sse_emits_events():
    """SSE endpoint phải trả ít nhất một event trong 3 giây."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        events = []
        async with client.stream("GET", "/api/stream/prices") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    events.append(line)
                if len(events) >= 3:
                    break

        assert len(events) >= 3, "SSE không emit đủ events"
```

---

## 15. Quyết định thiết kế

| Quyết định | Lý do |
|---|---|
| `PriceCache` là intermediary, không phải `MarketDataSource` trực tiếp | Tách rời producer (data source) và consumer (SSE, routes). Mọi consumer đọc từ cache, không biết dữ liệu đến từ simulator hay API. |
| Push-on-change trong SSE | Tránh gửi dữ liệu trùng khi VNDirect poll interval (15s) dài hơn SSE check interval (0.5s). Tiết kiệm bandwidth và tránh frontend render không cần thiết. |
| Giữ 50 điểm lịch sử trong memory | Đủ cho sparkline 50 candles. Không cần persist ra DB. Mất khi restart — đây là behavior chấp nhận được. |
| `session_open` thay vì `day_change` | Simulator chạy 24/7, không có reset ngày. `session_open` nhất quán và trung thực hơn. |
| Poll tuần tự từng mã với VNDirect | VNDirect API không hỗ trợ batch query nhiều mã trong 1 request. |
| `VNSTOCK_API_KEY` với dấu ":" → SSI | Cho phép một env var duy nhất để chọn cả data source lẫn credentials, không cần thêm env var mới. |
| `asyncio.sleep` trong SSE generator | Không dùng `while True` spinning. 0.5s interval đủ responsive cho trading UI. |
| `threading.Lock` trong `PriceCache` | FastAPI có thể gọi từ multiple threads (khi dùng `run_in_executor`). Lock bảo vệ dict operations không phải atomic. |
| `numpy` cho GBM | Hiệu năng tốt hơn `math.random` cho vector operations, và tương lai có thể vectorize toàn bộ tick computation. |
| Không dùng thư viện `vnstock` | Direct HTTP calls đơn giản hơn, ít dependency hơn, dễ debug hơn, không bị phụ thuộc version churn của third-party. |

---

*Tài liệu này hoàn chỉnh. Backend Engineer có thể implement trực tiếp từ đây mà không cần đọc thêm tài liệu nào khác trong `planning/`.*
