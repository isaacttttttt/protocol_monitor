from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, desc, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.market.models import Kline
from app.signals.models import Signal
from app.storage.models import indicator_archives, indicators, klines, notifications, signals, strategy_states


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
            "taker_buy_volume": kline.taker_buy_volume,
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
                taker_buy_volume=row["taker_buy_volume"],
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

    async def get_manageable_signals(self, exchange: str | None = None, symbol: str | None = None) -> list[dict]:
        """Return triggered paper trades that still require lifecycle updates."""
        conditions = [
            signals.c.status.in_(["TRIGGERED", "MANAGING"]),
            signals.c.entry.is_not(None),
            signals.c.sl.is_not(None),
        ]
        if exchange is not None:
            conditions.append(signals.c.exchange == exchange)
        if symbol is not None:
            conditions.append(signals.c.symbol == symbol)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(signals).where(*conditions)
                )
            ).mappings()
            return [dict(row) for row in rows]

    async def update_signal_lifecycle(self, signal_id: str, **values: object) -> None:
        """Persist a paper-trade state transition."""
        if not values:
            return
        async with self.session_factory() as session:
            await session.execute(update(signals).where(signals.c.signal_id == signal_id).values(**values))
            await session.commit()

    async def get_portfolio_risk_state(
        self,
        now: datetime | None = None,
        risk_per_1r_pct: float = 1.0,
    ) -> dict[str, float | int]:
        """Reconstruct paper-equity drawdown and recent loss state from outcomes."""
        current_time = now or datetime.utcnow()
        day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(signals)
                    .where(signals.c.result.is_not(None))
                    .order_by(signals.c.resolved_at, signals.c.created_at)
                )
            ).mappings()
            items = [dict(row) for row in rows]
        equity = peak = 1.0
        drawdown = 0.0
        daily_r = 0.0
        consecutive_losses = 0
        for item in items:
            result_r = _paper_result_r(item)
            if result_r is None:
                continue
            resolved_at = item.get("resolved_at") or item.get("created_at")
            if resolved_at and resolved_at >= day_start:
                daily_r += result_r * float(item.get("position_r") or 0.0)
            pnl_fraction = result_r * float(item.get("position_r") or 0.0) * risk_per_1r_pct / 100
            equity *= 1 + pnl_fraction
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak if peak else 0.0)
            if result_r < 0:
                consecutive_losses += 1
            elif result_r > 0:
                consecutive_losses = 0
        return {
            "daily_realized_r": daily_r,
            "current_drawdown_pct": drawdown * 100,
            "consecutive_losses": consecutive_losses,
        }

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

    async def was_notification_sent(self, signal_id: str, channel: str) -> bool:
        async with self.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(notifications)
                .where(
                    notifications.c.signal_id == signal_id,
                    notifications.c.channel == channel,
                    notifications.c.status == "SENT",
                )
            )
            return bool(count)


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


def _paper_result_r(row: dict) -> float | None:
    result = str(row.get("result") or "")
    if result == "LOSS_SL":
        return -1.0
    if result in {"BREAKEVEN_AFTER_TP1", "TIME_STOP", "TP1_REACHED"}:
        return 0.0
    if result != "WIN_TP2":
        return None
    entry = float(row.get("entry") or 0.0)
    stop = float(row.get("sl") or 0.0)
    target = float(row.get("tp2") or row.get("tp1") or 0.0)
    direction = str(row.get("direction") or "")
    risk = entry - stop if direction == "LONG" else stop - entry
    reward = target - entry if direction == "LONG" else entry - target
    return reward / risk if risk > 0 else None


class IndicatorArchiveRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def save_snapshot(self, payload: dict) -> None:
        generated_at_raw = str(payload["generated_at"]).replace("Z", "+00:00")
        generated_at = datetime.fromisoformat(generated_at_raw).replace(tzinfo=None)
        values = {
            "run_id": payload["run_id"],
            "generated_at": generated_at,
            "payload": payload,
        }
        async with self.session_factory() as session:
            bind_name = session.bind.dialect.name if session.bind else ""
            if bind_name == "postgresql":
                stmt = pg_insert(indicator_archives).values(**values).on_conflict_do_update(
                    index_elements=["run_id"],
                    set_=values,
                )
                await session.execute(stmt)
            else:
                await session.execute(delete(indicator_archives).where(indicator_archives.c.run_id == payload["run_id"]))
                await session.execute(insert(indicator_archives).values(**values))
            await session.commit()
