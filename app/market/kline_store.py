from collections import defaultdict, deque
from collections.abc import Iterable

from app.market.models import Kline
from app.storage.repositories import KlineRepository


class KlineStore:
    def __init__(self, repository: KlineRepository, cache_limit: int = 1000) -> None:
        self.repository = repository
        self.cache_limit = cache_limit
        self._cache: dict[tuple[str, str, str], deque[Kline]] = defaultdict(lambda: deque(maxlen=cache_limit))

    async def upsert_kline(self, kline: Kline) -> None:
        key = (kline.exchange, kline.symbol, kline.interval)
        bucket = self._cache[key]
        if bucket and bucket[-1].open_time == kline.open_time:
            bucket[-1] = kline
        else:
            bucket.append(kline)
        if kline.is_closed:
            await self.repository.upsert_kline(kline)

    def get_recent(self, exchange: str, symbol: str, interval: str, limit: int) -> list[Kline]:
        key = (exchange, symbol, interval)
        return list(self._cache[key])[-limit:]

    async def load_from_db(self, symbols: Iterable[dict], intervals_limit: int | None = None) -> None:
        for item in symbols:
            intervals = item.get("intervals", [])
            if intervals_limit is not None:
                intervals = intervals[:intervals_limit]
            for interval in intervals:
                rows = await self.repository.get_recent(
                    item["exchange"],
                    item["symbol"],
                    interval,
                    self.cache_limit,
                )
                key = (item["exchange"], item["symbol"], interval)
                self._cache[key].extend(rows)
