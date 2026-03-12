# VNDirect API Research

## Overview

VNDirect (vndirect.com.vn) is one of Vietnam's largest securities brokerages. They expose several unofficial/semi-public REST APIs that can be used to retrieve historical and near-real-time stock price data for HOSE, HNX, and UPCoM exchanges. There is no official public developer portal or API key registration for their data APIs -- the endpoints are discovered via reverse engineering from their web platform (finfo, dstock, dchart).

For real-time streaming data, VNDirect provides a WebSocket service at `wss://price-cmc-04.vndirect.com.vn/realtime/websocket`.

## 1. Historical/EOD Price Data (REST)

### Endpoint

```
GET https://finfo-api.vndirect.com.vn/v4/stock_prices/
```

### Authentication

None required. The API is publicly accessible without API keys or tokens.

### Query Parameters

| Parameter | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `q`       | string | Yes      | Query filter. Format: `code:{SYMBOL}~date:gte:{START}~date:lte:{END}` |
| `sort`    | string | No       | Sort field (e.g., `"date"`) |
| `size`    | int    | No       | Number of records per page (default varies, max ~9999) |
| `page`    | int    | No       | Page number, starts at 1 |

### Query Filter Syntax

The `q` parameter uses `~` as a field separator and supports these operators:
- `code:{SYMBOL}` -- exact match on stock ticker
- `date:gte:{YYYY-MM-DD}` -- date greater than or equal
- `date:lte:{YYYY-MM-DD}` -- date less than or equal

### Example Request

```
GET https://finfo-api.vndirect.com.vn/v4/stock_prices/?q=code:VNM~date:gte:2025-01-01~date:lte:2025-01-31&sort=date&size=31&page=1
```

### Response Format

```json
{
  "currentPage": 1,
  "size": 31,
  "totalElements": 22,
  "totalPages": 1,
  "data": [
    {
      "code": "VNM",
      "date": "2025-01-02",
      "time": "15:00:00",
      "floor": "HOSE",
      "type": "STOCK",
      "basicPrice": 75000,
      "ceilingPrice": 80200,
      "floorPrice": 69800,
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
      "pctChange": 1.2
    }
  ]
}
```

### Response Field Definitions (25 fields per record)

| Field         | Type   | Description |
|---------------|--------|-------------|
| `code`        | string | Stock ticker symbol (e.g., "VNM") |
| `date`        | string | Trading date (YYYY-MM-DD) |
| `time`        | string | Time of last update (HH:MM:SS) |
| `floor`       | string | Exchange: "HOSE", "HNX", or "UPCOM" |
| `type`        | string | Security type: "STOCK", "ETF", etc. |
| `basicPrice`  | float  | Reference price (gia tham chieu) |
| `ceilingPrice`| float  | Ceiling price (gia tran) |
| `floorPrice`  | float  | Floor price (gia san) |
| `open`        | float  | Opening price |
| `high`        | float  | Highest price of the day |
| `low`         | float  | Lowest price of the day |
| `close`       | float  | Closing price |
| `average`     | float  | Average price |
| `adOpen`      | float  | Adjusted opening price |
| `adHigh`      | float  | Adjusted high price |
| `adLow`       | float  | Adjusted low price |
| `adClose`     | float  | Adjusted closing price |
| `adAverage`   | float  | Adjusted average price |
| `nmVolume`    | int    | Normal market trading volume |
| `nmValue`     | float  | Normal market trading value (VND) |
| `ptVolume`    | int    | Put-through (block trade) volume |
| `ptValue`     | float  | Put-through trading value (VND) |
| `change`      | float  | Price change from reference |
| `adChange`    | float  | Adjusted price change |
| `pctChange`   | float  | Percentage change |

### Python Code Example

```python
import requests
from datetime import datetime

API_VNDIRECT = "https://finfo-api.vndirect.com.vn/v4/stock_prices/"

def get_stock_prices(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch historical prices from VNDirect.

    Args:
        symbol: Stock ticker (e.g., "VNM", "VCB")
        start_date: Start date as YYYY-MM-DD
        end_date: End date as YYYY-MM-DD

    Returns:
        List of price records (dicts).
    """
    query = f"code:{symbol}~date:gte:{start_date}~date:lte:{end_date}"
    delta = datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")
    params = {
        "sort": "date",
        "size": delta.days + 1,
        "page": 1,
        "q": query,
    }
    resp = requests.get(API_VNDIRECT, params=params)
    resp.raise_for_status()
    return resp.json()["data"]
```

## 2. Company/Securities Information (REST)

### Endpoint: List All Securities

```
GET https://finfo-api.vndirect.com.vn/stocks?status=all
```

Returns all listed securities with basic metadata.

### Endpoint: Single Security

```
GET https://finfo-api.vndirect.com.vn/stocks?symbol={SYMBOL}
```

## 3. Real-Time Data (WebSocket)

### Connection

```
wss://price-cmc-04.vndirect.com.vn/realtime/websocket
```

No authentication required. Uses the STOMP protocol over WebSocket.

### Message Types

| Type | Name            | Description |
|------|-----------------|-------------|
| `SP` | Stock Partial   | Sent on every trade match or change in top 3 bid/ask prices |
| `BA` | Bid/Ask         | Sent on change in top 3 bid/ask prices (more frequent than SP) |
| `DE` | Derivative      | Derivative market data with top 10 bid/ask levels |
| `MI` | Market Index    | Index updates (VNINDEX, VN30, HNX, HNX30, UPCOM, VNXALL) |

### Index Codes in MI Messages

| Code | Index |
|------|-------|
| 02   | HNX |
| 03   | UPCOM |
| 10   | VNINDEX |
| 11   | VN30 |
| 12   | HNX30 |
| 13   | VNXALL |

### Python WebSocket Example

```python
import asyncio
import ssl
import websockets

async def connect_vndirect():
    uri = "wss://price-cmc-04.vndirect.com.vn/realtime/websocket"
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with websockets.connect(uri, ssl=ssl_context) as ws:
        # STOMP CONNECT frame
        await ws.send("CONNECT\naccept-version:1.1\nheart-beat:0,0\n\n\x00")
        response = await ws.recv()
        print("Connected:", response)

        # Subscribe to a stock channel
        await ws.send(
            "SUBSCRIBE\nid:sub-0\ndestination:/topic/stock\n\n\x00"
        )

        async for message in ws:
            # Parse STOMP MESSAGE frames
            print(message)

asyncio.run(connect_vndirect())
```

## 4. Rate Limits and Reliability

- **No official rate limits documented** -- the API is unofficial/reverse-engineered
- Community projects report the REST API is generally stable for moderate usage
- Aggressive polling (sub-second) may result in IP-based throttling or blocking
- The WebSocket endpoint is more suitable for real-time use cases
- **No free tier / paid tier distinction** -- there is no API key system
- The endpoints may change without notice since they are not a public API product

## 5. Alternative: SSI FastConnect API (Official)

SSI Securities provides an official, documented API called **FastConnect Data (FCData)**. This is the recommended approach for production use as it has proper authentication, documentation, and support.

See the SSI API section below for comparison.

### SSI FCData Overview

- **Base URL**: `https://fc-data.ssi.com.vn/`
- **Auth**: Bearer token via ConsumerID + ConsumerSecret
- **Registration**: Via iBoard at `https://iboard.ssi.com.vn/support/api-service/management`
- **Python package**: `pip install ssi-fc-data`
- **Documentation**: `https://guide.ssi.com.vn/ssi-products/fastconnect-data/api-specs`

### SSI FCData Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v2/Market/AccessToken` | Get bearer token |
| GET | `/api/v2/Market/Securities` | List securities by market |
| GET | `/api/v2/Market/SecuritiesDetails` | Security details |
| GET | `/api/v2/Market/IndexComponents` | Stocks in an index |
| GET | `/api/v2/Market/IndexList` | Available indices |
| GET | `/api/v2/Market/DailyOhlc` | Daily OHLC + volume |
| GET | `/api/v2/Market/IntradayOhlc` | Intraday OHLC |
| GET | `/api/v2/Market/DailyIndex` | Daily index values |
| GET | `/api/v2/Market/DailyStockPrice` | Daily stock price |

### SSI Authentication Flow

```python
import requests

url = "https://fc-data.ssi.com.vn/api/v2/Market/AccessToken"
payload = {
    "consumerID": "your_consumer_id",
    "consumerSecret": "your_consumer_secret",
}
resp = requests.post(url, json=payload)
token = resp.json()["data"]["accessToken"]

# Use token in subsequent requests
headers = {"Authorization": f"Bearer {token}"}
```

### SSI DailyOhlc Parameters

| Parameter   | Type   | Required | Description |
|-------------|--------|----------|-------------|
| Symbol      | string | Yes      | Stock ticker |
| FromDate    | string | Yes      | Start date (DD/MM/YYYY) |
| ToDate      | string | Yes      | End date (DD/MM/YYYY) |
| PageIndex   | int    | No       | Page number |
| PageSize    | int    | No       | Records per page |
| Ascending   | bool   | No       | Sort order |

### SSI Python Client Example

```python
from ssi_fc_data import fc_md_client, model

# config.py
auth_type = "Bearer"
consumerID = "your_consumer_id"
consumerSecret = "your_consumer_secret"
url = "https://fc-data.ssi.com.vn/"
stream_url = "https://fc-data.ssi.com.vn/"

import config

client = fc_md_client.MarketDataClient(config)

# Get daily OHLC data
req = model.daily_ohlc("VNM", "01/01/2025", "31/01/2025", 1, 100, True)
result = client.daily_ohlc(config, req)
print(result)

# Get securities list
req = model.securities("HOSE", 1, 100)
result = client.securities(config, req)
print(result)
```

### SSI Streaming Data

```python
from ssi_fc_data.fc_md_stream import MarketDataStream
from ssi_fc_data.fc_md_client import MarketDataClient

def on_message(message):
    print("Received:", message)

def on_error(error):
    print("Error:", error)

client = MarketDataClient(config)
stream = MarketDataStream(config, client)
stream.start(on_message, on_error, "stockRealtimeByList:HOSE:VNM,VCB,FPT")
```

## 6. Community Python Libraries

### vnstock (by Thinh Vu) -- Recommended

- **PyPI**: `pip install vnstock`
- **GitHub**: `https://github.com/thinh-vu/vnstock`
- **Docs**: `https://vnstocks.com/docs`
- **Latest version**: 3.2.0+ (March 2025)
- **Data sources**: VCI, TCBS, SSI, DNSE
- **Features**: Historical prices, real-time quotes, financials, stock screener, company info
- **License**: Free for personal/research use
- **Note**: Does NOT use VNDirect finfo-api directly in recent versions; uses VCI and TCBS as primary sources

### vnquant (by Pham Dinh Khanh)

- **GitHub**: `https://github.com/phamdinhkhanh/vnquant`
- **Data source**: VNDirect finfo-api (directly)
- **Status**: Less actively maintained

### vietfin

- **GitHub**: `https://github.com/vietfin/vietfin`
- **Note**: Alternative library for Vietnam stock data

## 7. Recommendation for FinAlly Project

For this project, we recommend:

1. **Primary real data source**: VNDirect finfo-api (REST, no auth needed, simplest integration)
2. **Environment variable**: `VNSTOCK_API_KEY` -- when set, activates real data polling
3. **Polling approach**: REST polling of `finfo-api.vndirect.com.vn/v4/stock_prices/` for simplicity (no WebSocket complexity needed since we already use SSE to the frontend)
4. **Fallback**: Built-in simulator when no API key is set

The VNDirect REST API requires no authentication, making it the simplest option for this educational project. However, since it is unofficial, we should design the market data interface to be swappable -- allowing SSI FastConnect or other sources in the future.

## Sources

- [vnquant GitHub Issue #6 - VNDirect API method](https://github.com/phamdinhkhanh/vnquant/issues/6)
- [nguyenngocbinh/vnstock - VNDirect API wrapper](https://github.com/nguyenngocbinh/vnstock)
- [thinh-vu/vnstock - Modern Vietnamese stock toolkit](https://github.com/thinh-vu/vnstock)
- [Real-time VNDirect data](https://github.com/hoangnt2601/Real-time-data-vndirect)
- [SSI FastConnect API Guide](https://guide.ssi.com.vn/ssi-products/fastconnect-data/api-specs)
- [SSI python-fcdata](https://github.com/SSI-Securities-Corporation/python-fcdata)
- [SSI FC Data on PyPI](https://pypi.org/project/ssi-fc-data/)
- [VN Stock API MCP Server](https://lobehub.com/mcp/long0308-vn-stock-api-mcp)
- [AlgoTrade Vietnam - API overview](https://hub.algotrade.vn/knowledge-hub/api-in-vietnam-stock-market/)
