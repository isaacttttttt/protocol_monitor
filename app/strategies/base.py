from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from app.indicators.cvd import calculate_cvd_proxy
from app.indicators.macd import calculate_macd
from app.indicators.structure import detect_structure
from app.market.kline_store import KlineStore
from app.market.models import Kline
from app.risk.btc_filter import BtcFilterState, evaluate_btc_filter
from app.signals.models import Signal, SignalLevel
from app.strategies.state_machine import StrategyStateEnum


@dataclass
class StrategyState:
    state: StrategyStateEnum = StrategyStateEnum.IDLE
    context: dict[str, Any] = field(default_factory=dict)
    entered_state_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StrategyContext:
    event: Kline
    store: KlineStore
    now: datetime = field(default_factory=datetime.utcnow)


class BaseStrategy:
    id: str
    symbol: str
    book: Literal["Micro", "Macro"]
    direction: Literal["LONG", "SHORT", "NEUTRAL"]

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.id = config["id"]
        self.exchange = config.get("exchange", "BINANCE")
        self.symbol = config["symbol"]
        self.book = config.get("book", "Micro")
        self.strategy_name = config.get("strategy_name", self.id)
        self.direction = config.get("direction", "NEUTRAL")
        self.max_hold_hours = int(config.get("max_hold_hours", 48))
        self.state = StrategyState()

    async def on_market_update(self, context: StrategyContext) -> list[Signal]:
        raise NotImplementedError

    async def load_state(self) -> StrategyState:
        return self.state

    async def save_state(self, state: StrategyState) -> None:
        self.state = state

    def state_record(self) -> dict[str, Any]:
        return {
            "strategy_id": self.id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "book": self.book,
            "strategy_name": self.strategy_name,
            "state": self.state.state.value,
            "direction": self.direction,
            "context": self.state.context,
            "last_signal_id": self.state.context.get("last_signal_id"),
            "entered_state_at": self.state.entered_state_at,
            "updated_at": datetime.utcnow(),
        }

    def _set_state(self, state: StrategyStateEnum, **context: Any) -> None:
        if self.state.state != state:
            self.state.entered_state_at = datetime.utcnow()
        self.state.state = state
        self.state.context.update(context)

    def _entry_locked(self, now: datetime, cooldown_hours: int = 4) -> bool:
        """Prevent duplicate entries and briefly hold terminal strategy states."""
        if self.state.state in {StrategyStateEnum.TRIGGERED, StrategyStateEnum.MANAGING}:
            return True
        if self.state.state not in {StrategyStateEnum.INVALID, StrategyStateEnum.EXPIRED, StrategyStateEnum.COOLDOWN}:
            return False
        elapsed = (now - self.state.entered_state_at).total_seconds() / 3600
        if elapsed < cooldown_hours:
            return True
        self.state = StrategyState()
        return False

    def on_signal_resolved(self, signal_id: str, resolved_at: datetime) -> None:
        """Move the matching live strategy into a short re-entry cooldown."""
        if self.state.context.get("last_signal_id") != signal_id:
            return
        self.state = StrategyState(
            state=StrategyStateEnum.COOLDOWN,
            context={"resolved_signal_id": signal_id},
            entered_state_at=resolved_at,
        )

    def _btc_filter(self, store: KlineStore) -> BtcFilterState:
        candles = self._closed_recent(store, "BTCUSDT", "15m", 60)
        return evaluate_btc_filter(candles, calculate_macd(candles), calculate_cvd_proxy(candles), detect_structure(candles))

    def _cvd(self, store: KlineStore, interval: str, lookback: int = 20):
        return calculate_cvd_proxy(
            self._closed_recent(store, self.symbol, interval, lookback + 5),
            lookback=lookback,
        )

    def _last_close(self, store: KlineStore, interval: str) -> Decimal | None:
        candles = self._closed_recent(store, self.symbol, interval, 1)
        return candles[-1].close if candles else None

    def _closed_recent(self, store: KlineStore, symbol: str, interval: str, limit: int) -> list[Kline]:
        candles = store.get_recent(self.exchange, symbol, interval, limit * 2)
        return [candle for candle in candles if candle.is_closed][-limit:]

    def _make_signal(
        self,
        level: SignalLevel,
        price: Decimal,
        status: str,
        reason: str,
        invalid_condition: str,
        **kwargs: Any,
    ) -> Signal:
        signal = Signal(
            signal_id=f"{self.id}-{level.value}-{uuid4().hex[:10]}",
            exchange=self.exchange,
            symbol=self.symbol,
            book=self.book,
            strategy_name=self.strategy_name,
            level=level,
            direction=self.direction,
            status=status,
            trigger_price=price,
            entry=kwargs.get("entry"),
            sl=kwargs.get("sl"),
            tp1=kwargs.get("tp1"),
            tp2=kwargs.get("tp2"),
            tp3=kwargs.get("tp3"),
            rr_to_tp1=kwargs.get("rr_to_tp1"),
            position_r=kwargs.get("position_r", 0.0),
            trigger_reason=reason,
            invalid_condition=invalid_condition,
            risk_flags=kwargs.get("risk_flags", {}),
            btc_filter=asdict(kwargs["btc_filter"]) if kwargs.get("btc_filter") else {},
            flow_state=asdict(kwargs["flow_state"]) if kwargs.get("flow_state") else {},
            raw_snapshot=kwargs.get("raw_snapshot", {}),
        )
        self.state.context["last_signal_id"] = signal.signal_id
        return signal

    def _time_stop_if_due(self, price: Decimal, now: datetime) -> Signal | None:
        if self.state.state not in {StrategyStateEnum.TRIGGERED, StrategyStateEnum.MANAGING}:
            return None
        elapsed_hours = (now - self.state.entered_state_at).total_seconds() / 3600
        if elapsed_hours < self.max_hold_hours:
            return None
        self._set_state(StrategyStateEnum.EXPIRED, time_stop_hours=self.max_hold_hours)
        return self._make_signal(
            SignalLevel.L4,
            price,
            "EXPIRED",
            f"Micro 信号已达到 {self.max_hold_hours}H 时间止损",
            "未确认 TP1 的策略不得继续延长为 Macro 持仓",
            risk_flags={"time_stop": True, "elapsed_hours": round(elapsed_hours, 2)},
        )
