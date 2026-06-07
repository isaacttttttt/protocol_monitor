import asyncio
from collections.abc import AsyncIterator

from app.market.models import MarketEvent


class MarketDataBus:
    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=maxsize)

    async def publish(self, event: MarketEvent) -> None:
        await self._queue.put(event)

    async def subscribe(self) -> AsyncIterator[MarketEvent]:
        while True:
            yield await self._queue.get()
