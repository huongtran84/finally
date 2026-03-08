import asyncio
import json

from starlette.requests import Request
from starlette.responses import StreamingResponse

from .cache import PriceCache


SSE_CHECK_INTERVAL = 0.25  # seconds between checking for new data


async def price_stream(request: Request, cache: PriceCache) -> StreamingResponse:
    """
    SSE endpoint generator. Yields 'data: {...}\\n\\n' events whenever
    the price cache has new data.
    """

    async def event_generator():
        last_version = -1  # force initial send

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            current_version = cache.version
            if current_version > last_version:
                # Prices have changed — send all latest prices
                all_prices = await cache.get_all_latest()
                ticks = [tick.model_dump(mode="json") for tick in all_prices.values()]

                # SSE format: each event is "data: <json>\n\n"
                payload = json.dumps(ticks, default=str)
                yield f"data: {payload}\n\n"

                last_version = current_version

            await asyncio.sleep(SSE_CHECK_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
