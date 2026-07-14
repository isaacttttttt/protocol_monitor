from decimal import Decimal

from app.indicators.atr import calculate_atr
from app.risk.rr import calc_rr
from app.risk.stops import atr_buffered_stop
from app.signals.models import Signal, SignalLevel
from app.strategies.base import BaseStrategy, StrategyContext
from app.strategies.state_machine import StrategyStateEnum
from app.strategies.dynamic_levels import build_liquidity_sweep_plan


class EthCm3LiquiditySweepLong(BaseStrategy):
    async def on_market_update(self, context: StrategyContext) -> list[Signal]:
        if context.event.symbol != self.symbol or not context.event.is_closed:
            return []
        signals: list[Signal] = []
        price = context.event.close
        expired = self._time_stop_if_due(price, context.now)
        if expired:
            return [expired]
        if self._entry_locked(context.now):
            return []
        dynamic = bool(self.config.get("dynamic_levels", {}).get("enabled", False))
        dynamic_plan = build_liquidity_sweep_plan(
            context.store,
            self.exchange,
            self.symbol,
            context.event.close_time,
        ) if dynamic else None
        sweep_levels = [
            Decimal(str(value))
            for key, value in self.config["levels"].items()
            if key.startswith("sweep_level_")
        ]
        reclaim_levels = [
            Decimal(str(value))
            for key, value in self.config["levels"].items()
            if key.startswith("reclaim_level_")
        ]
        sweep_level = Decimal(
            str(self.state.context.get("active_sweep_level") or (dynamic_plan.sweep_level if dynamic_plan is not None else max(sweep_levels)))
        )
        reclaim = Decimal(
            str(self.state.context.get("active_reclaim") or (dynamic_plan.reclaim if dynamic_plan is not None else min(reclaim_levels)))
        )
        tp1 = Decimal(str(self.config["targets"]["tp1"]))
        tp2 = Decimal(str(self.config["targets"]["tp2"]))
        tp3 = Decimal(str(self.config["targets"]["tp3"]))
        min_rr = float(self.config.get("risk", {}).get("min_rr_to_tp1", 1.5))
        btc = self._btc_filter(context.store)
        cvd = self._cvd(context.store, "15m")

        full_range = max(float(context.event.high - context.event.low), 1e-12)
        lower_wick = float(min(context.event.open, context.event.close) - context.event.low)
        min_wick_ratio = float(self.config.get("dynamic_levels", {}).get("min_lower_wick_ratio", 0.3))
        wick_confirmed = dynamic_plan is None or lower_wick / full_range >= min_wick_ratio
        if context.event.interval in {"5m", "15m"} and context.event.low < sweep_level and wick_confirmed:
            existing_plan = self.state.context.get("dynamic_plan")
            existing_sweep_low = Decimal(str(self.state.context.get("sweep_low") or context.event.low))
            self._set_state(
                StrategyStateEnum.WATCHING,
                sweep_low=str(min(existing_sweep_low, context.event.low)),
                active_sweep_level=str(sweep_level),
                active_reclaim=str(reclaim),
                dynamic_plan=existing_plan or ({
                    "sweep_level": float(sweep_level),
                    "reclaim": float(reclaim),
                    "atr": dynamic_plan.atr,
                    "lower_wick_ratio": lower_wick / full_range,
                } if dynamic_plan is not None else None),
            )
            signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", f"ETH 刺破动态扫低位 {sweep_level}，进入反杀观察", "15M 收盘继续低于 sweep low 或 BTC 强空", btc_filter=btc, flow_state=cvd))

        sweep_low = Decimal(str(self.state.context.get("sweep_low", "0")))
        if sweep_low and context.event.interval == "15m" and price < sweep_low:
            self._set_state(StrategyStateEnum.INVALID)
            return [self._make_signal(SignalLevel.L4, price, "INVALID", "15M 收盘低于 sweep low，扫低反杀失败", "逆势 Micro 短多失效，不得升级为 Macro 多单", btc_filter=btc, flow_state=cvd)]
        if btc.strong_bearish:
            self._set_state(StrategyStateEnum.INVALID)
            return [self._make_signal(SignalLevel.L4, price, "INVALID", "BTC strong bearish，ETH 逆势短多失效", "暂停 C-M3 多头", btc_filter=btc, flow_state=cvd)]
        if sweep_low and context.event.interval == "15m" and price > reclaim and not cvd.makes_new_low and not btc.strong_bearish:
            stop_config = self.config.get("stop_loss", {})
            atr = calculate_atr(self._closed_recent(context.store, self.symbol, "15m", 100))
            stop = Decimal(
                str(
                    atr_buffered_stop(
                        "LONG",
                        float(sweep_low),
                        atr,
                        float(stop_config.get("atr_buffer_multiplier", 0.0)),
                    )
                )
            )
            if self.state.context.get("dynamic_plan"):
                risk_distance = price - stop
                if risk_distance > 0:
                    tp1 = price + risk_distance * Decimal("1.5")
                    tp2 = price + risk_distance * Decimal("2.5")
                    tp3 = price + risk_distance * Decimal("4.0")
            rr = calc_rr("LONG", float(price), float(stop), float(tp1))
            if rr >= min_rr:
                self._set_state(StrategyStateEnum.TRIGGERED)
                signals.append(self._make_signal(SignalLevel.L3, price, "TRIGGERED", f"刺破 {sweep_level} 后 15M 收盘站回 {reclaim}，CVD 未创新低，BTC 未强空，R/R 合格", "15M 收盘低于 sweep low 或 BTC strong bearish；不得升级 Macro", entry=price, sl=stop, tp1=tp1, tp2=tp2, tp3=tp3, rr_to_tp1=round(rr, 2), position_r=min(float(self.config.get("risk", {}).get("default_position_r", 0.25)), 0.5), btc_filter=btc, flow_state=cvd, raw_snapshot={"configured_sweep_levels": [float(value) for value in sweep_levels], "configured_reclaim_levels": [float(value) for value in reclaim_levels], "dynamic_plan": self.state.context.get("dynamic_plan"), "atr14": atr, "atr_buffer_multiplier": float(stop_config.get("atr_buffer_multiplier", 0.0))}))
            else:
                signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", f"扫低反杀接近触发但 R/R={rr:.2f} < {min_rr}，禁止 L3", "等待更好入场或放弃", btc_filter=btc, flow_state=cvd, risk_flags={"rr_too_low": True}))
        return signals
