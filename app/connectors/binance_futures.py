import asyncio
import json

import websockets
from loguru import logger

from app.market.data_bus import MarketDataBus
from app.market.models import Kline


class BinanceFuturesConnector:
    def __init__(self, ws_base: str, symbols: list[dict], bus: MarketDataBus, reconnect_seconds: int = 5) -> None:
        self.ws_base = ws_base.rstrip("/")
        self.symbols = [item for item in symbols if item.get("exchange") == "BINANCE" and item.get("enabled", True)]
        self.bus = bus
        self.reconnect_seconds = reconnect_seconds

    def _stream_url(self) -> str:
        streams: list[str] = []
        for item in self.symbols:
            for interval in item.get("intervals", []):
                streams.append(f"{item['symbol'].lower()}@kline_{interval}")
        joined = "/".join(streams)
        base = self.ws_base
        if base.endswith("/ws"):
            base = base[: -len("/ws")]
        return f"{base}/stream?streams={joined}" if "/stream" not in base else f"{base}?streams={joined}"

    async def run(self) -> None:
        while True:
            try:
                url = self._stream_url()
                logger.info("connecting Binance futures websocket: {}", url)
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
                    async for raw in websocket:
                        payload = json.loads(raw)
                        data = payload.get("data", payload)
                        if data.get("e") == "kline":
                            await self.bus.publish(Kline.from_binance_payload(data))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Binance websocket error: {}", exc)
                await asyncio.sleep(self.reconnect_seconds)
