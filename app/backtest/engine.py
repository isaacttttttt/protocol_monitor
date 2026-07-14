from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
from statistics import mean, pstdev
from typing import Literal, Sequence

from app.market.models import Kline

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class EntryIntent:
    """A decision made after a closed bar and filled at the next bar open."""

    direction: Direction
    stop: float
    target: float
    reason: str
    risk_fraction: float = 0.0025
    max_hold_bars: int | None = None
    minimum_rr: float = 0.0
    strategy: str = "UNKNOWN"


@dataclass(frozen=True)
class FundingRate:
    time: datetime
    rate: float
    mark_price: float | None = None


@dataclass(frozen=True)
class BacktestConfig:
    """Execution assumptions for a conservative bar-based simulation."""

    fee_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 3.0
    starting_equity: float = 1.0
    stop_first_when_ambiguous: bool = True


@dataclass(frozen=True)
class BacktestTrade:
    strategy: str
    direction: Direction
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry: float
    exit: float
    stop: float
    target: float
    exit_reason: str
    gross_r: float
    fee_r: float
    funding_r: float
    net_r: float
    risk_fraction: float
    pnl_fraction: float


@dataclass(frozen=True)
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    net_return_pct: float = 0.0
    annualized_return_pct: float | None = None
    max_drawdown_pct: float = 0.0
    calmar_ratio: float | None = None
    sharpe_per_trade: float | None = None
    win_rate_pct: float = 0.0
    profit_factor: float | None = None
    average_win_r: float | None = None
    average_loss_r: float | None = None
    exposure_pct: float = 0.0


class BacktestEngine:
    """Evaluate closed-bar entry intents without look-ahead execution."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        bars: Sequence[Kline],
        intents: dict[int, EntryIntent],
        funding_rates: Sequence[FundingRate] = (),
    ) -> BacktestResult:
        if len(bars) < 2:
            return BacktestResult(equity_curve=[self.config.starting_equity])
        trades: list[BacktestTrade] = []
        occupied_bars = 0
        next_free_index = 1
        for signal_index in sorted(intents):
            entry_index = signal_index + 1
            if signal_index < 0 or entry_index >= len(bars) or entry_index < next_free_index:
                continue
            intent = intents[signal_index]
            trade, exit_index = self._simulate_trade(
                bars,
                signal_index,
                entry_index,
                intent,
                funding_rates,
            )
            if trade is None:
                continue
            trades.append(trade)
            occupied_bars += exit_index - entry_index + 1
            next_free_index = exit_index + 1
        return self._result(trades, bars, occupied_bars)

    def _simulate_trade(
        self,
        bars: Sequence[Kline],
        signal_index: int,
        entry_index: int,
        intent: EntryIntent,
        funding_rates: Sequence[FundingRate],
    ) -> tuple[BacktestTrade | None, int]:
        raw_entry = float(bars[entry_index].open)
        entry = self._slipped(raw_entry, intent.direction, entering=True)
        risk = entry - intent.stop if intent.direction == "LONG" else intent.stop - entry
        if risk <= 0:
            return None, entry_index
        reward = intent.target - entry if intent.direction == "LONG" else entry - intent.target
        if reward <= 0 or reward / risk < intent.minimum_rr:
            return None, entry_index
        final_index = len(bars) - 1
        if intent.max_hold_bars is not None:
            final_index = min(final_index, entry_index + max(0, intent.max_hold_bars - 1))
        exit_index = final_index
        raw_exit = float(bars[final_index].close)
        exit_reason = "END_OF_DATA" if final_index == len(bars) - 1 else "TIME_STOP"
        for index in range(entry_index, final_index + 1):
            bar = bars[index]
            open_price = float(bar.open)
            stop_gap = (
                open_price <= intent.stop
                if intent.direction == "LONG"
                else open_price >= intent.stop
            )
            target_gap = (
                open_price >= intent.target
                if intent.direction == "LONG"
                else open_price <= intent.target
            )
            if stop_gap:
                exit_index = index
                raw_exit = open_price
                exit_reason = "STOP_GAP"
                break
            if target_gap:
                exit_index = index
                raw_exit = intent.target
                exit_reason = "TARGET_GAP"
                break
            stop_hit, target_hit = self._bar_hits(bar, intent)
            if stop_hit and target_hit:
                target_hit = not self.config.stop_first_when_ambiguous
                stop_hit = self.config.stop_first_when_ambiguous
                exit_reason = "AMBIGUOUS_STOP_FIRST" if stop_hit else "AMBIGUOUS_TARGET_FIRST"
            elif stop_hit:
                exit_reason = "STOP"
            elif target_hit:
                exit_reason = "TARGET"
            else:
                continue
            exit_index = index
            raw_exit = intent.stop if stop_hit else intent.target
            break
        exit_price = self._slipped(raw_exit, intent.direction, entering=False)
        signed_move = exit_price - entry if intent.direction == "LONG" else entry - exit_price
        gross_r = signed_move / risk
        fee_fraction = self.config.fee_bps_per_side / 10_000
        fees = (entry + exit_price) * fee_fraction
        fee_r = fees / risk
        funding_r = self._funding_r(
            funding_rates,
            intent.direction,
            bars[entry_index].open_time,
            bars[exit_index].close_time,
            entry,
            risk,
        )
        net_r = gross_r - fee_r + funding_r
        trade = BacktestTrade(
            strategy=intent.strategy,
            direction=intent.direction,
            signal_time=bars[signal_index].close_time,
            entry_time=bars[entry_index].open_time,
            exit_time=bars[exit_index].close_time,
            entry=entry,
            exit=exit_price,
            stop=float(intent.stop),
            target=float(intent.target),
            exit_reason=exit_reason,
            gross_r=gross_r,
            fee_r=fee_r,
            funding_r=funding_r,
            net_r=net_r,
            risk_fraction=intent.risk_fraction,
            pnl_fraction=intent.risk_fraction * net_r,
        )
        return trade, exit_index

    @staticmethod
    def _funding_r(
        funding_rates: Sequence[FundingRate],
        direction: Direction,
        entry_time: datetime,
        exit_time: datetime,
        entry: float,
        risk: float,
    ) -> float:
        direction_sign = 1.0 if direction == "LONG" else -1.0
        pnl = 0.0
        entry_timestamp = _utc_timestamp(entry_time)
        exit_timestamp = _utc_timestamp(exit_time)
        for point in funding_rates:
            point_timestamp = _utc_timestamp(point.time)
            if entry_timestamp < point_timestamp <= exit_timestamp:
                mark = point.mark_price or entry
                pnl -= direction_sign * point.rate * mark
        return pnl / risk

    @staticmethod
    def _bar_hits(bar: Kline, intent: EntryIntent) -> tuple[bool, bool]:
        low = float(bar.low)
        high = float(bar.high)
        if intent.direction == "LONG":
            return low <= intent.stop, high >= intent.target
        return high >= intent.stop, low <= intent.target

    def _slipped(self, price: float, direction: Direction, *, entering: bool) -> float:
        slippage = self.config.slippage_bps_per_side / 10_000
        adverse_sign = 1 if (direction == "LONG") == entering else -1
        return price * (1 + adverse_sign * slippage)

    def _result(self, trades: list[BacktestTrade], bars: Sequence[Kline], occupied_bars: int) -> BacktestResult:
        equity = self.config.starting_equity
        for trade in trades:
            equity *= 1 + trade.pnl_fraction
        returns = [trade.net_r for trade in trades]
        wins = [value for value in returns if value > 0]
        losses = [value for value in returns if value < 0]
        curve = self._mark_to_market_curve(bars, trades)
        max_drawdown = _max_drawdown(curve)
        net_return = equity / self.config.starting_equity - 1
        profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else None)
        sharpe = None
        if len(returns) >= 2 and pstdev(returns) > 0:
            sharpe = mean(returns) / pstdev(returns) * math.sqrt(len(returns))
        elapsed_days = (
            (bars[-1].close_time - bars[0].open_time).total_seconds() / 86_400
            if len(bars) >= 2
            else 0.0
        )
        annualized_return = None
        if elapsed_days > 0 and equity > 0:
            annualized_return = (equity / self.config.starting_equity) ** (365.25 / elapsed_days) - 1
        calmar_base = annualized_return if annualized_return is not None else net_return
        calmar = calmar_base / max_drawdown if max_drawdown > 0 else (math.inf if calmar_base > 0 else None)
        return BacktestResult(
            trades=trades,
            equity_curve=curve,
            net_return_pct=net_return * 100,
            annualized_return_pct=annualized_return * 100 if annualized_return is not None else None,
            max_drawdown_pct=max_drawdown * 100,
            calmar_ratio=calmar,
            sharpe_per_trade=sharpe,
            win_rate_pct=(len(wins) / len(returns) * 100) if returns else 0.0,
            profit_factor=profit_factor,
            average_win_r=mean(wins) if wins else None,
            average_loss_r=mean(losses) if losses else None,
            exposure_pct=(occupied_bars / len(bars) * 100) if bars else 0.0,
        )

    def _mark_to_market_curve(
        self,
        bars: Sequence[Kline],
        trades: Sequence[BacktestTrade],
    ) -> list[float]:
        equity = self.config.starting_equity
        curve = [equity]
        trade_index = 0
        for bar in bars:
            bar_open = _utc_timestamp(bar.open_time)
            bar_close = _utc_timestamp(bar.close_time)
            while (
                trade_index < len(trades)
                and _utc_timestamp(trades[trade_index].exit_time) <= bar_open
            ):
                equity *= 1 + trades[trade_index].pnl_fraction
                trade_index += 1
            mark_equity = equity
            if trade_index < len(trades):
                trade = trades[trade_index]
                entry_time = _utc_timestamp(trade.entry_time)
                exit_time = _utc_timestamp(trade.exit_time)
                if entry_time <= bar_open and exit_time <= bar_close:
                    equity *= 1 + trade.pnl_fraction
                    trade_index += 1
                    mark_equity = equity
                elif entry_time <= bar_open < exit_time:
                    mark = float(bar.close)
                    risk = trade.entry - trade.stop if trade.direction == "LONG" else trade.stop - trade.entry
                    signed_move = mark - trade.entry if trade.direction == "LONG" else trade.entry - mark
                    mark_r = signed_move / risk if risk > 0 else 0.0
                    mark_equity = equity * (1 + trade.risk_fraction * mark_r)
            curve.append(max(mark_equity, 0.0))
        return curve


def _max_drawdown(equity_curve: Sequence[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _utc_timestamp(value: datetime) -> float:
    """Treat legacy naive market timestamps as UTC for stable comparisons."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
