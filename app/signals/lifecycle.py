from __future__ import annotations

from datetime import datetime, timedelta

from app.market.models import Kline
from app.storage.repositories import SignalRepository


class SignalLifecycleManager:
    """Resolve monitor-only L3 signals against subsequent closed candles."""

    def __init__(self, repository: SignalRepository, micro_max_hold_hours: int = 48) -> None:
        self.repository = repository
        self.micro_max_hold_hours = micro_max_hold_hours

    async def on_closed_kline(self, candle: Kline, now: datetime | None = None) -> list[str]:
        if not candle.is_closed:
            return []
        current_time = now or candle.close_time
        resolved: list[str] = []
        rows = await self.repository.get_manageable_signals(candle.exchange, candle.symbol)
        for row in rows:
            if row["created_at"] >= candle.close_time:
                continue
            if await self._advance(row, candle, current_time):
                resolved.append(str(row["signal_id"]))
        return resolved

    async def _advance(self, row: dict, candle: Kline, now: datetime) -> bool:
        direction = str(row.get("direction") or "")
        entry = float(row["entry"])
        stop = float(row["sl"])
        tp1 = float(row["tp1"]) if row.get("tp1") is not None else None
        tp2 = float(row["tp2"]) if row.get("tp2") is not None else None
        managing = bool(row.get("tp1_reached")) or row.get("status") == "MANAGING"
        effective_stop = entry if managing else stop
        stop_hit = float(candle.low) <= effective_stop if direction == "LONG" else float(candle.high) >= effective_stop
        target = tp2 if managing and tp2 is not None else tp1
        target_hit = False
        if target is not None:
            target_hit = float(candle.high) >= target if direction == "LONG" else float(candle.low) <= target

        # With OHLC bars, the intrabar path is unknown. Resolve ambiguous bars
        # against the strategy to avoid overstating historical performance.
        if stop_hit:
            await self.repository.update_signal_lifecycle(
                row["signal_id"],
                status="RESOLVED",
                sl_reached=not managing,
                resolved_at=now,
                result="BREAKEVEN_AFTER_TP1" if managing else "LOSS_SL",
            )
            return True
        if target_hit and not managing:
            await self.repository.update_signal_lifecycle(
                row["signal_id"],
                status="MANAGING",
                tp1_reached=True,
                result="TP1_REACHED",
            )
            return False
        if target_hit and managing:
            await self.repository.update_signal_lifecycle(
                row["signal_id"],
                status="RESOLVED",
                resolved_at=now,
                result="WIN_TP2",
            )
            return True
        if (
            row.get("book") == "Micro"
            and not managing
            and now - row["created_at"] >= timedelta(hours=self.micro_max_hold_hours)
        ):
            await self.repository.update_signal_lifecycle(
                row["signal_id"],
                status="EXPIRED",
                expired_at=now,
                resolved_at=now,
                result="TIME_STOP",
            )
            return True
        return False
