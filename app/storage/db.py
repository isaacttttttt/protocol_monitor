import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config.settings import Settings
from app.storage.models import metadata


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, future=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    for attempt in range(3):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
                columns = await conn.run_sync(
                    lambda sync_conn: {column["name"] for column in inspect(sync_conn).get_columns("klines")}
                )
                if "taker_buy_volume" not in columns:
                    await conn.execute(text("ALTER TABLE klines ADD COLUMN taker_buy_volume NUMERIC"))
            return
        except OperationalError as exc:
            if "already exists" not in str(exc).lower() or attempt == 2:
                raise
            await asyncio.sleep(0.25)
