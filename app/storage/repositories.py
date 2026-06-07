from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, desc, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.market.models import Kline
from app.signals.models import Signal
from app.storage.models import indicators, klines, notifications, signals, strategy_states


def _decimal(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


class KlineRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def upsert_kline(self, kline: Kline) -> None:
        values = {
            "exchange": kline.exchange,
            "symbol": kline.symbol,
            "interval": kline.interval,
            "open_time": kline.open_time,
            "close_time": kline.close_time,
            "open": kline.open,
            "high": kline.high,
            "low": kline.low,
            "close": kline.close,
            "volume": kline.volume,
            "quote_volume": kline.quote_volume,
            "is_closed": kline.is_closed,
        }
        async with self.session_factory() as session:
            bind_name = session.bind.dialect.name if session.bind else ""
            if bind_name == "postgresql":
                stmt = pg_insert(klines).values(**values).on_conflict_do_update(
                    index_elements=["exchange", "symbol", "interval", "open_time"],
                    set_=values,
                )
            else:
                await session.execute(
                    delete(klines).where(
                        klines.c.exchange == kline.exchange,
                        klines.c.symbol == kline.symbol,
                        klines.c.interval == kline.interval,
                        klines.c.open_time == kline.open_time,
                    )
                )
                stmt = insert(klines).values(**values)
            await session.execute(stmt)
            await session.commit()

    async def get_recent(self, exchange: str, symbol: str, interval: str, limit: int) -> list[Kline]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(klines)
                    .where(klines.c.exchange == exchange, klines.c.symbol == symbol, klines.c.interval == interval)
                    .order_by(desc(klines.c.open_time))
                    .limit(limit)
                )
            ).mappings()
            items = list(rows)
        return [
            Kline(
                exchange=row["exchange"],
                symbol=row["symbol"],
                interval=row["interval"],
                open_time=row["open_time"],
                close_time=row["close_time"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                quote_volume=row["quote_volume"],
                is_closed=row["is_closed"],
            )
            for row in reversed(items)
        ]

    async def count_klines(self) -> int:
        async with self.session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(klines)) or 0)


class SignalRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def save_signal(self, signal: Signal) -> None:
        values = {
            "signal_id": signal.signal_id,
            "exchange": signal.exchange,
            "symbol": signal.symbol,
            "book": signal.book,
            "strategy_name": signal.strategy_name,
            "level": signal.level.value,
            "direction": signal.direction,
            "status": signal.status,
            "trigger_price": signal.trigger_price,
            "entry": signal.entry,
            "sl": signal.sl,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
            "rr_to_tp1": signal.rr_to_tp1,
            "position_r": signal.position_r,
            "trigger_reason": signal.trigger_reason,
            "invalid_condition": signal.invalid_condition,
            "risk_flags": signal.risk_flags,
            "btc_filter": signal.btc_filter,
            "flow_state": signal.flow_state,
            "raw_snapshot": signal.raw_snapshot,
            "created_at": signal.created_at,
            "tp1_reached": signal.tp1_reached,
        }
        async with self.session_factory() as session:
            await session.execute(insert(signals).values(**values))
            await session.commit()

    async def get_recent_signals(self, hours: int, now: datetime | None = None) -> list[dict]:
        cutoff = (now or datetime.utcnow()) - timedelta(hours=hours)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(signals)
                    .where(signals.c.created_at >= cutoff)
                    .order_by(desc(signals.c.created_at))
                )
            ).mappings()
            return [dict(row) for row in rows]

    async def save_strategy_state(self, values: dict) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(strategy_states).where(strategy_states.c.strategy_id == values["strategy_id"]))
            await session.execute(insert(strategy_states).values(**values))
            await session.commit()

    async def get_strategy_states(self) -> dict[str, dict]:
        async with self.session_factory() as session:
            rows = (await session.execute(select(strategy_states))).mappings()
            return {row["strategy_id"]: dict(row) for row in rows}

    async def save_notification(self, values: dict) -> None:
        async with self.session_factory() as session:
            await session.execute(insert(notifications).values(**values))
            await session.commit()


class IndicatorRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def save_snapshot(self, values: dict) -> None:
        async with self.session_factory() as session:
            await session.execute(
                delete(indicators).where(
                    indicators.c.exchange == values["exchange"],
                    indicators.c.symbol == values["symbol"],
                    indicators.c.interval == values["interval"],
                    indicators.c.ts == values["ts"],
                )
            )
            await session.execute(insert(indicators).values(**values))
            await session.commit()
