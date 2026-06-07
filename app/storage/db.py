import asyncio

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
            return
        except OperationalError as exc:
            if "already exists" not in str(exc).lower() or attempt == 2:
                raise
            await asyncio.sleep(0.25)
