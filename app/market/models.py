from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Kline:
    exchange: str
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None
    is_closed: bool
    taker_buy_volume: Decimal | None = None

    @classmethod
    def from_binance_payload(cls, payload: dict[str, Any]) -> "Kline":
        k = payload["k"]
        return cls(
            exchange="BINANCE",
            symbol=k["s"],
            interval=k["i"],
            open_time=datetime.utcfromtimestamp(k["t"] / 1000),
            close_time=datetime.utcfromtimestamp(k["T"] / 1000),
            open=Decimal(k["o"]),
            high=Decimal(k["h"]),
            low=Decimal(k["l"]),
            close=Decimal(k["c"]),
            volume=Decimal(k["v"]),
            quote_volume=Decimal(k["q"]),
            is_closed=bool(k["x"]),
            taker_buy_volume=Decimal(k["V"]) if k.get("V") is not None else None,
        )


MarketEvent = Kline
