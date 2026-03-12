# FinAlly — AI Trading Workstation (Thị Trường Chứng Khoán Việt Nam)

## Project Specification

## 1. Vision

FinAlly (Finance Ally) là một workstation giao dịch chứng khoán được hỗ trợ bởi AI, được thiết kế đặc biệt cho **thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM)**. Hệ thống stream dữ liệu thị trường thời gian thực, cho phép người dùng giao dịch danh mục mô phỏng, và tích hợp trợ lý LLM có thể phân tích danh mục và thực hiện lệnh theo ngôn ngữ tự nhiên (tiếng Việt). Giao diện lấy cảm hứng từ Bloomberg terminal kết hợp ngữ cảnh thị trường Việt Nam.

Đây là dự án  AI agentic. Được xây dựng hoàn toàn bởi các Coding Agent, minh họa cách các AI agent có thể phối hợp tạo ra ứng dụng full-stack production-quality. Các agent tương tác thông qua các file trong `planning/`.

## 2. User Experience

### First Launch

Người dùng chạy một lệnh Docker duy nhất (hoặc script khởi động). Trình duyệt mở tại `http://localhost:8000`. Không cần đăng nhập. Ngay lập tức hiển thị:

- Danh sách theo dõi gồm 10 mã cổ phiếu Việt Nam mặc định với giá cập nhật trực tiếp
- **200.000.000 VNĐ** tiền ảo (~$8.000 USD)
- Giao diện terminal giao dịch chuyên nghiệp, dark theme
- Bảng chat AI sẵn sàng hỗ trợ bằng **tiếng Việt**

### What the User Can Do

- **Xem giá stream** — giá nhấp nháy xanh (tăng) hoặc đỏ (giảm); hiển thị màu **tím** khi giá chạm trần (ceiling price) và **xanh lam** khi chạm sàn (floor price) — đặc thù thị trường Việt Nam
- **Xem biểu đồ nến nhỏ (mini candlestick)** bên cạnh mỗi mã. Khi tải trang, frontend lấy lịch sử từ `GET /api/prices/history`, sau đó cập nhật liên tục qua SSE
- **Click vào mã cổ phiếu** để xem biểu đồ chi tiết lớn hơn ở khu vực chính
- **Mua và bán cổ phiếu** — lệnh thị trường (MP), khớp tức thì theo giá hiện tại, không phí, **lô tối thiểu 100 cổ phiếu** (quy định sàn Việt Nam). Toast xác nhận mỗi lệnh đã khớp (mã, chiều, khối lượng, giá)
- **Theo dõi danh mục** — heatmap (treemap) hiển thị vị thế theo trọng số và tô màu theo P&L; biểu đồ P&L theo thời gian
- **Xem bảng vị thế** — mã CP, khối lượng, giá vốn bình quân, giá hiện tại, P&L chưa thực hiện, % thay đổi
- **Chat với trợ lý AI** — hỏi về danh mục, nhận phân tích, để AI thực hiện lệnh và quản lý watchlist bằng **tiếng Việt**
- **Quản lý watchlist** — thêm/xóa mã thủ công hoặc qua chat AI

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |
| Lot size = 100 shares | Tuân thủ quy định tối thiểu của HOSE/HNX/UPCoM; không hỗ trợ cổ phiếu lẻ |
| Price in VNĐ | Đơn vị tiền tệ chuẩn của thị trường Việt Nam; hiển thị theo định dạng VN |
| Price limit simulation | Biên độ ±7% (HOSE), ±10% (HNX), ±15% (UPCoM) phản ánh thực tế thị trường |


---

## 3b. Đặc Thù Thị Trường Chứng Khoán Việt Nam

### Đơn vị tiền tệ
- Tất cả giá và số dư hiển thị theo **VNĐ (Việt Nam Đồng)**
- Giá cổ phiếu Việt Nam thường có 3 chữ số cuối là 00 hoặc 00 (đơn vị đồng), ví dụ: 75.000 VNĐ/cp
- Hiển thị với dấu chấm phân cách nghìn theo định dạng Việt Nam (75.000, không phải 75,000)

### Quy tắc lô cổ phiếu
- **HOSE và HNX**: Lô tối thiểu = **100 cổ phiếu**
- **UPCoM**: Lô tối thiểu = **100 cổ phiếu**
- Mọi lệnh mua/bán phải là bội số của 100 — backend validate và từ chối nếu không đúng

### Biên độ dao động giá (Price Limit)
| Sàn | Biên độ | Màu giá trần | Màu giá sàn |
|-----|---------|--------------|-------------|
| HOSE | ±7% | Tím (#7B2FBE) | Xanh lam (#0077B6) |
| HNX | ±10% | Tím (#7B2FBE) | Xanh lam (#0077B6) |
| UPCoM | ±15% | Tím (#7B2FBE) | Xanh lam (#0077B6) |

- Giá không thể vượt quá ceiling price (giá trần) hoặc thấp hơn floor price (giá sàn) trong cùng phiên
- Simulator kẹp giá trong biên độ này mỗi chu kỳ cập nhật

### Phiên giao dịch (chỉ áp dụng nếu dùng dữ liệu thật)
- **ATO** (09:00 – 09:15): Khớp lệnh mở cửa
- **Liên tục sáng** (09:15 – 11:30)
- **Nghỉ trưa** (11:30 – 13:00)
- **Liên tục chiều** (13:00 – 14:30)
- **ATC** (14:30 – 14:45): Khớp lệnh đóng cửa
- Simulator chạy 24/7 nhưng có thể cấu hình giới hạn theo giờ giao dịch thật

### Mã cổ phiếu (Ticker format)
- Mã CP Việt Nam gồm **2–4 ký tự chữ in hoa** (ví dụ: VNM, VCB, HPG, FPTS)
- Không có tiền tố sàn trong watchlist; sàn được lưu riêng trong metadata

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── db/                   # Schema definitions, seed data, migration logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/db/`** contains schema SQL definitions and seed logic. The backend lazily initializes the database on first request — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Bắt buộc: OpenRouter API key cho tính năng chat AI
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Tùy chọn: API key cho dữ liệu thị trường Việt Nam thật (SSI iBoard hoặc VNDirect)
# Nếu không có, dùng trình mô phỏng giá tích hợp (khuyến nghị cho hầu hết người dùng)
VNSTOCK_API_KEY=

# Tùy chọn: Sàn dữ liệu thực ("SSI" hoặc "VNDIRECT"), mặc định "SSI"
VNSTOCK_PROVIDER=SSI

# Tùy chọn: Set "true" để dùng mock LLM cho testing
LLM_MOCK=false

# Tùy chọn: Override LLM model (mặc định: openrouter/openai/gpt-oss-120b)
LLM_MODEL=openrouter/openai/gpt-oss-120b
```

### Behavior

- Nếu `VNSTOCK_API_KEY` được set → backend dùng SSI hoặc VNDirect API để lấy dữ liệu thật
- Nếu `VNSTOCK_API_KEY` trống → backend dùng trình mô phỏng tích hợp
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- If `LLM_MODEL` is set → backend uses the specified model instead of the default `openrouter/openai/gpt-oss-120b`
- The backend reads `.env` from the project root (mounted into the container or read via docker `--env-file`)

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Tạo giá theo mô hình Geometric Brownian Motion (GBM) với drift và volatility cấu hình theo từng mã
- Cập nhật mỗi ~500ms
- Các mã cùng ngành di chuyển tương quan (e.g., ngân hàng cùng tăng/giảm)
- Sự kiện ngẫu nhiên — đột biến 2-5% để tăng tính thực tế
- **Giá khởi điểm thực tế theo thị trường Việt Nam** (đơn vị VNĐ):
  - VNM ~75.000, VCB ~92.000, VIC ~42.000, HPG ~28.000, FPT ~130.000
  - MWG ~45.000, TCB ~25.000, VHM ~38.000, GAS ~85.000, MSN ~68.000
- **Quy tắc biên độ dao động (price limit)**:
  - HOSE: ±7% so với giá tham chiếu
  - HNX: ±10% so với giá tham chiếu
  - UPCoM: ±15% so với giá tham chiếu
  - Giá được kẹp trong biên độ này — giá tím (ceiling) và xanh lam (floor)
- Chạy như background task in-process — không cần dependency ngoài

### SSI/VNDirect Data API (Optional)

- REST API polling (không WebSocket) — đơn giản hơn, hoạt động ổn định
- Poll toàn bộ mã đang theo dõi theo interval cấu hình
- Sử dụng **SSI iBoard API** hoặc **VNDirect API** nếu key được cung cấp
- Free tier: poll mỗi 15 giây
- Paid tier: poll mỗi 2–5 giây
- Parse response về cùng định dạng với simulator
- Biến môi trường: `VNSTOCK_API_KEY` (thay thế `MASSIVE_API_KEY`)

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, timestamp, and a rolling history (last 50 prices) for each ticker
- The rolling history is served via `GET /api/prices/history` so sparklines can populate immediately on page load
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### Dynamic Ticker Management

When a ticker is added to (or removed from) the watchlist, the watchlist endpoint notifies the market data layer so it begins (or stops) generating/polling prices for that ticker immediately. The contract:

- **Watchlist add** → market data source starts tracking the new ticker (simulator seeds it at a realistic price; Massive poller adds it to the next poll batch)
- **Watchlist remove** → market data source stops tracking the ticker and removes it from the price cache
- The market data layer exposes `add_ticker(ticker)` and `remove_ticker(ticker)` methods on the abstract interface for this purpose

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- Server pushes price updates only when the price cache has new data (i.e., a price has actually changed) — not on a fixed cadence. This avoids sending duplicate data when the Massive API poll interval is longer than the SSE check interval
- Each SSE event contains ticker, price, previous price, timestamp, and change direction
- Each ticker also includes a `session_change_pct` field: the percentage change from the ticker's price when the backend started (since the simulator has no concept of trading days, "daily change" is meaningless — this is "change since session start")
- Client handles reconnection automatically (EventSource has built-in retry)

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `200000000.0`) — đơn vị VNĐ (200 triệu đồng)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` INTEGER — **khối lượng tính theo lô (bội số của 100 cp)**, không hỗ trợ cổ phiếu lẻ trên HOSE/HNX
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` INTEGER — **khối lượng tính theo lô (bội số của 100 cp)**, không hỗ trợ cổ phiếu lẻ trên HOSE/HNX
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task, and immediately after each trade execution. Retention: the backend keeps the most recent 500 snapshots per user; older snapshots are pruned each time a new one is inserted.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — trades executed, watchlist changes made; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=200000000.0` (200 triệu VNĐ)
- Ten watchlist entries (mã cổ phiếu Việt Nam): **VNM, VCB, VIC, HPG, FPT, MWG, TCB, VHM, GAS, MSN**
  - VNM: Vinamilk (HOSE)
  - VCB: Vietcombank (HOSE)
  - VIC: Vingroup (HOSE)
  - HPG: Hòa Phát Group (HOSE)
  - FPT: FPT Corporation (HOSE)
  - MWG: Thế Giới Di Động (HOSE)
  - TCB: Techcombank (HOSE)
  - VHM: Vinhomes (HOSE)
  - GAS: PetroVietnam Gas (HOSE)
  - MSN: Masan Group (HOSE)

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates (push-on-change) |
| GET | `/api/prices/history` | Rolling price history (last 50 per ticker) for sparkline bootstrap |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Thực hiện lệnh: `{ticker, quantity, side}`. Trả `200` khi thành công, hoặc `400` với `{error: "..."}` khi thất bại (số dư không đủ, không đủ CP, mã không hợp lệ, **khối lượng không phải bội số 100**) |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message, receive complete JSON response (message + executed actions) |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads the last 20 messages of conversation history from the `chat_messages` table (this balances context quality with token budget)
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill. The model is configurable via the `LLM_MODEL` env var (default: `openrouter/openai/gpt-oss-120b`)
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response
7. Stores the message and executed actions in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "VNM", "side": "buy", "quantity": 200}
  ],
  "watchlist_changes": [
    {"ticker": "DGW", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)
- `watchlist_changes` (optional): Array of watchlist modifications

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

If a trade fails validation (e.g., insufficient cash), the error is included in the chat response so the LLM can inform the user.

### System Prompt Guidance

LLM được hướng dẫn là **"FinAlly, trợ lý giao dịch chứng khoán Việt Nam"** với các chỉ dẫn:
- Trả lời **bằng tiếng Việt** (trừ khi người dùng hỏi bằng tiếng Anh)
- Phân tích cơ cấu danh mục, mức độ tập trung rủi ro và P&L
- Đề xuất giao dịch có lý do rõ ràng, tham chiếu ngành, chỉ số P/E, P/B theo chuẩn VN
- Gợi ý theo bội số 100 cổ phiếu (quy tắc lô tối thiểu của HOSE/HNX)
- Hiểu ngữ cảnh thị trường Việt Nam: giá tham chiếu, giá trần/sàn, phiên ATO/ATC
- Thực hiện lệnh khi người dùng yêu cầu hoặc đồng ý
- Quản lý watchlist chủ động
- Súc tích, có dữ liệu, phân tích theo phong cách chuyên gia chứng khoán Việt
- Luôn trả lời JSON hợp lệ theo schema

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), session change % (change since backend started), and a sparkline mini-chart (bootstrapped from `GET /api/prices/history` on page load, then extended with SSE data)
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — ô nhập liệu gồm: trường mã CP, trường khối lượng (bội số 100, không nhận số lẻ), nút Mua và nút Bán. Lệnh thị trường, khớp tức thì. Khi thành công, toast xác nhận (mã, chiều, khối lượng, giá khớp). Khi thất bại, toast hiển thị lỗi từ API (e.g., "Số dư không đủ", "Không đủ cổ phiếu để bán", "Khối lượng phải là bội số của 100").
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Canvas-based charting library preferred (Lightweight Charts or Recharts) for performance
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss, **quantity not multiple of 100**)
- Vietnam market rules: price limit enforcement (ceiling/floor), lot size validation, VNĐ formatting
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow
- API routes: correct status codes, response shapes, error handling

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, position updates or disappears
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- SSE resilience: disconnect and verify reconnection

---

## 13. Design Decisions Log

This section records decisions made during document review. All resolutions have been incorporated into the relevant sections above.

| # | Issue | Resolution | Section Updated |
|---|-------|------------|-----------------|
| 1 | SSE cadence vs. data source cadence mismatch | SSE pushes only when prices actually change, not on a fixed cadence | 6 — SSE Streaming |
| 2 | Sparkline data loss on page reload | Backend maintains rolling 50-price history per ticker; served via `GET /api/prices/history`; frontend bootstraps sparklines on load | 6 — Shared Price Cache, 8 — API Endpoints, 10 — Frontend |
| 3 | "Daily change %" meaningless for simulator | Renamed to "session change %" — percentage change from ticker's price when backend started | 6 — SSE Streaming, 10 — Frontend |
| 4 | Portfolio snapshots grow unboundedly | Retain last 500 snapshots per user; prune on insert | 7 — Database |
| 5 | Fractional shares not mentioned in UX | Trade bar explicitly accepts decimals; fractional shares confirmed as supported | 2 — UX, 10 — Frontend |
| 6 | Watchlist ↔ Market Data coupling undefined | Market data interface exposes `add_ticker`/`remove_ticker`; watchlist endpoints call these on add/remove | 6 — Dynamic Ticker Management |
| 7 | Chat history loading unspecified | Load last 20 messages for LLM context | 9 — LLM Integration |
| 8 | Error UX for manual trade failures | Trade API returns `400` with `{error: "..."}` JSON; frontend shows error toast | 8 — API Endpoints, 10 — Frontend |
| 9 | No trade confirmation feedback | Toast notification on every trade execution (success or failure), both manual and AI-initiated | 2 — UX, 10 — Frontend |
| 10 | LLM model hardcoded, no fallback | Model configurable via `LLM_MODEL` env var; default remains `openrouter/openai/gpt-oss-120b` | 5 — Env Vars, 9 — LLM Integration |
