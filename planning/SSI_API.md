# SSI FastConnect Data API

Tài liệu nghiên cứu API SSI FastConnect Data cho dự án FinAlly.

## Tổng quan

SSI Securities Corporation cung cấp **FastConnect API** cho phép bên thứ ba tích hợp lấy dữ liệu thị trường chứng khoán Việt Nam. Bộ API chia thành:

- **FastConnect Data (FC Data)**: Dữ liệu thị trường (giá, OHLC, danh sách CK, chỉ số)
- **FastConnect Trading (FC Trading)**: Đặt lệnh giao dịch (không dùng trong dự án này)

**Base URL**: `https://fc-data.ssi.com.vn/`

**Python SDK**: `ssi-fc-data` (PyPI) — SDK chính thức từ SSI

## Đăng ký API Key

1. Tạo tài khoản SSI (hoặc đăng nhập nếu đã có)
2. Truy cập: https://iboard.ssi.com.vn/support/api-service/management
3. Tạo API key — nhận `consumerID` và `consumerSecret`
4. Key chỉ hiển thị **một lần** khi tạo, cần lưu lại ngay

## Xác thực (Authentication)

Tất cả endpoint đều yêu cầu Bearer token. Lấy token qua:

```
POST https://fc-data.ssi.com.vn/api/v2/Market/AccessToken
Content-Type: application/json

{
  "consumerID": "c058f55761814787882b2c8df1336e25",
  "consumerSecret": "144cac45770949519d2dfd20edb5b6ab"
}
```

Response:
```json
{
  "message": "Success",
  "status": 200,
  "data": {
    "accessToken": "eyJhbGciOiJSUzI1NiIsI..."
  }
}
```

Token được dùng trong header cho mọi request tiếp theo:
```
Authorization: Bearer eyJhbGciOiJSUzI1NiIsI...
```

## API Endpoints (v2)

### GET /api/v2/Market/Securities
Danh sách chứng khoán theo sàn.

| Param | Type | Mô tả |
|-------|------|-------|
| Market | string | Sàn: `HOSE`, `HNX`, `UPCOM` |
| PageIndex | int | Trang (từ 1) |
| PageSize | int | Số bản ghi/trang |

### GET /api/v2/Market/SecuritiesDetails
Thông tin chi tiết mã chứng khoán.

| Param | Type | Mô tả |
|-------|------|-------|
| Market | string | Sàn: `HOSE`, `HNX`, `UPCOM` |
| Symbol | string | Mã CK, ví dụ `VNM` |
| PageIndex | int | Trang |
| PageSize | int | Số bản ghi/trang |

### GET /api/v2/Market/DailyStockPrice
Giá cổ phiếu trong ngày. **Endpoint chính cho dự án FinAlly.**

| Param | Type | Mô tả |
|-------|------|-------|
| Symbol | string | Mã CK (VNM, VCB, ...) |
| Market | string | Sàn: `HOSE`, `HNX`, `UPCOM` |
| FromDate | string | Ngày bắt đầu (dd/MM/yyyy) |
| ToDate | string | Ngày kết thúc (dd/MM/yyyy) |
| PageIndex | int | Mặc định 1, tối đa 10 |
| PageSize | int | Mặc định 10 |

Response fields (dự kiến):
- `Symbol` — Mã CK
- `TradingDate` — Ngày giao dịch
- `Open`, `High`, `Low`, `Close` — OHLC
- `Volume` — Khối lượng
- `BasicPrice` — Giá tham chiếu
- `CeilingPrice` — Giá trần
- `FloorPrice` — Giá sàn

### GET /api/v2/Market/DailyOhlc
Dữ liệu nến ngày (OHLC) theo khoảng thời gian.

| Param | Type | Mô tả |
|-------|------|-------|
| Symbol | string | Mã CK |
| FromDate | string | Ngày bắt đầu (dd/MM/yyyy) |
| ToDate | string | Ngày kết thúc (dd/MM/yyyy) |
| PageIndex | int | Trang |
| PageSize | int | Số bản ghi |
| Ascending | bool | Sắp xếp tăng dần |

### GET /api/v2/Market/IntradayOhlc
Dữ liệu nến trong ngày (intraday).

| Param | Type | Mô tả |
|-------|------|-------|
| Symbol | string | Mã CK |
| FromDate | string | dd/MM/yyyy |
| ToDate | string | dd/MM/yyyy |
| Resolution | int | Phút/nến (1, 5, 15, 30, 60) |
| PageIndex | int | Trang |
| PageSize | int | Số bản ghi |
| Ascending | bool | Sắp xếp |

### GET /api/v2/Market/IndexList
Danh sách chỉ số theo sàn.

### GET /api/v2/Market/IndexComponents
Thành phần của chỉ số (VN30, HNX30, ...).

### GET /api/v2/Market/DailyIndex
Giá trị chỉ số theo ngày.

## Streaming (Real-time)

SSI cung cấp streaming qua SignalR/WebSocket tại cùng base URL. SDK `ssi-fc-data` hỗ trợ subscribe channel:

```python
from ssi_fc_data.fc_md_stream import MarketDataStream

def on_message(message):
    print(message)

def on_error(error):
    print(error)

stream = MarketDataStream(config, client)
stream.start(on_message, on_error, "X-QUOTE:ALL")
```

Channels phổ biến:
- `X-QUOTE:ALL` — Tất cả giá realtime
- `B-QUOTE:ALL` — Bảng giá
- `R-DEPTH:VNM` — Sổ lệnh theo mã

## Python Code Examples

### Cài đặt

```bash
uv add ssi-fc-data
```

### Config

```python
# ssi_config.py
auth_type = "Bearer"
consumerID = "your_consumer_id"
consumerSecret = "your_consumer_secret"
url = "https://fc-data.ssi.com.vn/"
stream_url = "https://fc-data.ssi.com.vn/"
```

### Lấy danh sách chứng khoán

```python
from ssi_fc_data import fc_md_client, model
import ssi_config as config

client = fc_md_client.MarketDataClient(config)

# Lấy danh sách CK trên HOSE
req = model.securities("HOSE", 1, 100)
result = client.securities(config, req)
print(result)
```

### Lấy giá cổ phiếu trong ngày

```python
from ssi_fc_data import fc_md_client, model
import ssi_config as config

client = fc_md_client.MarketDataClient(config)

# Giá VNM ngày hôm nay
req = model.daily_stock_price(
    symbol="VNM",
    market="HOSE",
    from_date="11/03/2026",
    to_date="11/03/2026",
    page_index=1,
    page_size=10
)
result = client.daily_stock_price(config, req)
print(result)
```

### Lấy OHLC theo ngày

```python
req = model.daily_ohlc(
    symbol="VCB",
    from_date="01/03/2026",
    to_date="11/03/2026",
    page_index=1,
    page_size=20,
    ascending=True
)
result = client.daily_ohlc(config, req)
print(result)
```

### Polling giá nhiều mã

```python
import time
from ssi_fc_data import fc_md_client, model
import ssi_config as config

client = fc_md_client.MarketDataClient(config)
tickers = ["VNM", "VCB", "VIC", "HPG", "FPT", "MWG", "TCB", "VHM", "GAS", "MSN"]

def poll_prices():
    """Poll giá tất cả mã trong watchlist."""
    prices = {}
    today = time.strftime("%d/%m/%Y")
    for ticker in tickers:
        req = model.daily_stock_price(
            symbol=ticker,
            market="HOSE",
            from_date=today,
            to_date=today,
            page_index=1,
            page_size=1
        )
        result = client.daily_stock_price(config, req)
        if result and result.get("data"):
            prices[ticker] = result["data"][0]
    return prices

# Poll mỗi 15 giây
while True:
    prices = poll_prices()
    for ticker, data in prices.items():
        print(f"{ticker}: {data.get('Close', 'N/A')}")
    time.sleep(15)
```

## Rate Limits

- Free tier: Khuyến nghị poll tối đa mỗi **15 giây**
- Paid tier: Có thể poll mỗi **2-5 giây**
- Streaming: Dùng streaming thay vì polling để tránh vi phạm rate limit
- SSI khuyến cáo dùng streaming cho dữ liệu realtime

## Lưu ý cho dự án FinAlly

1. **Approach**: Dùng REST polling (DailyStockPrice) thay vì streaming — đơn giản hơn, phù hợp kiến trúc SSE push hiện tại
2. **Batch polling**: Gọi tuần tự cho từng mã (API không hỗ trợ batch nhiều mã trong 1 request)
3. **Fallback**: Nếu không có API key → chuyển sang simulator
4. **Giá tham chiếu**: Response bao gồm basicPrice, ceilingPrice, floorPrice — cần thiết cho logic biên độ giá
5. **Thời gian giao dịch**: API chỉ trả dữ liệu trong giờ giao dịch (09:00-14:45); ngoài giờ trả giá đóng cửa

## Tài liệu tham khảo

- SSI FastConnect API Guide: https://guide.ssi.com.vn/ssi-products
- SSI Python SDK: https://github.com/SSI-Securities-Corporation/python-fcdata
- SSI API Docs: https://github.com/SSI-Securities-Corporation/docs
- SSI iBoard: https://iboard.ssi.com.vn/
