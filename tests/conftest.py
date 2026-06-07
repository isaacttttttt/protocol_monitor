from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.market.models import Kline


def make_kline(symbol: str, interval: str, open_: float, high: float, low: float, close: float, volume: float = 100, minutes: int = 0) -> Kline:
    ts = datetime(2026, 1, 1) + timedelta(minutes=minutes)
    return Kline(
        exchange="BINANCE",
        symbol=symbol,
        interval=interval,
        open_time=ts,
        close_time=ts + timedelta(minutes=1),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(volume * close)),
        is_closed=True,
    )


class DummyRepo:
    async def upsert_kline(self, kline):
        return None

    async def get_recent(self, exchange, symbol, interval, limit):
        return []


@pytest.fixture
def kline_factory():
    return make_kline
