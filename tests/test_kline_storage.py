from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.storage.db import init_db
from app.storage.repositories import KlineRepository


async def test_kline_repository_preserves_taker_buy_volume(kline_factory):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await init_db(engine)
        repository = KlineRepository(async_sessionmaker(engine, expire_on_commit=False))
        candle = kline_factory("ETHUSDT", "15m", 100, 105, 99, 103, 1000)
        candle = type(candle)(**{**candle.__dict__, "taker_buy_volume": 620})

        await repository.upsert_kline(candle)
        restored = await repository.get_recent("BINANCE", "ETHUSDT", "15m", 1)

        assert float(restored[0].taker_buy_volume) == 620
    finally:
        await engine.dispose()
