from decimal import Decimal

from app.indicators.atr import calculate_atr
from app.risk.rr import calc_rr
from app.risk.stops import atr_buffered_stop
from app.signals.models import Signal, SignalLevel
from app.strategies.base import BaseStrategy, StrategyContext
from app.strategies.state_machine import StrategyStateEnum


class EthCm2PullbackFailShort(BaseStrategy):
    async def on_market_update(self, context: StrategyContext) -> list[Signal]:
        if context.event.symbol != self.symbol or not context.event.is_closed:
            return []
        signals: list[Signal] = []
        price = context.event.close
        expired = self._time_stop_if_due(price, context.now)
        if expired:
            return [expired]
        zones = self.config["zones"]
        first_invalid = Decimal(str(self.config["levels"]["first_invalid"]))
        tp1 = Decimal(str(self.config["targets"]["tp1"]))
        tp2 = Decimal(str(self.config["targets"]["tp2"]))
        tp3 = Decimal(str(self.config["targets"]["tp3"]))
        min_rr = float(self.config.get("risk", {}).get("min_rr_to_tp1", 1.5))
        btc = self._btc_filter(context.store)
        cvd = self._cvd(context.store, "15m")
        active_zone_name = str(self.state.context.get("active_zone") or "")
        active_zone = zones.get(active_zone_name) if active_zone_name else None

        for zone_name, zone in zones.items():
            low = Decimal(str(zone["low"]))
            high = Decimal(str(zone["high"]))
            if low <= price <= high:
                active_zone_name = zone_name
                active_zone = zone
                self._set_state(
                    StrategyStateEnum.WATCHING,
                    touched_zone=True,
                    active_zone=zone_name,
                    active_fail_level=str(low),
                    active_invalid_level=str(high),
                )
                signals.append(
                    self._make_signal(
                        SignalLevel.L2,
                        price,
                        "WATCHING",
                        f"价格进入 {low}-{high} 反抽压力区（{zone_name}）",
                        f"15M 收盘站上 {high} 或 BTC 强反弹",
                        btc_filter=btc,
                        flow_state=cvd,
                    )
                )
                break

        invalid_level = Decimal(str(self.state.context.get("active_invalid_level") or first_invalid))
        if context.event.interval == "15m" and price > invalid_level:
            self._set_state(StrategyStateEnum.INVALID)
            signals.append(self._make_signal(SignalLevel.L4, price, "INVALID", f"15M 收盘站上 {invalid_level}，C-M2 做空失效", "放弃当前 C-M2 空头，等待新结构", btc_filter=btc, flow_state=cvd))
            return signals
        if btc.strong_bullish:
            self._set_state(StrategyStateEnum.INVALID)
            signals.append(self._make_signal(SignalLevel.L4, price, "INVALID", "BTC 强反弹，ETH 做空条件降级/失效", "暂停 C-M2 L3 做空", btc_filter=btc, flow_state=cvd))
            return signals
        touched = bool(self.state.context.get("touched_zone"))
        fail_back = Decimal(str(self.state.context.get("active_fail_level") or self.config["levels"]["fail_back_level"]))
        stop_config = self.config.get("stop_loss", {})
        structure_stop = float(stop_config.get("price") or (active_zone or {}).get("high") or invalid_level)
        if active_zone_name == "pullback_zone_2" and active_zone:
            structure_stop = max(structure_stop, float(active_zone["high"]))
        atr = calculate_atr(context.store.get_recent(self.exchange, self.symbol, "15m", 100))
        stop = Decimal(
            str(
                atr_buffered_stop(
                    "SHORT",
                    structure_stop,
                    atr,
                    float(stop_config.get("atr_buffer_multiplier", 0.0)),
                )
            )
        )
        rr = calc_rr("SHORT", float(price), float(stop), float(tp1))
        if context.event.interval == "5m" and touched and price < fail_back and not cvd.makes_new_high and not btc.strong_bullish:
            if rr >= min_rr:
                self._set_state(StrategyStateEnum.TRIGGERED)
                signals.append(
                    self._make_signal(
                        SignalLevel.L3,
                        price,
                        "TRIGGERED",
                        "5M 收盘跌回 1583 下方，CVD 未创新高，BTC 未强反弹，R/R 合格",
                        "15M 收盘站上 1605 或 BTC 强反弹",
                        entry=price,
                        sl=stop,
                        tp1=tp1,
                        tp2=tp2,
                        tp3=tp3,
                        rr_to_tp1=round(rr, 2),
                        position_r=float(self.config.get("risk", {}).get("default_position_r", 0.25)),
                        btc_filter=btc,
                        flow_state=cvd,
                        raw_snapshot={
                            "active_zone": active_zone_name,
                            "structure_stop": structure_stop,
                            "atr14": atr,
                            "atr_buffer_multiplier": float(stop_config.get("atr_buffer_multiplier", 0.0)),
                        },
                    )
                )
            else:
                signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", f"触发价接近但 R/R={rr:.2f} < {min_rr}，禁止 L3", "等待更好入场或放弃", btc_filter=btc, flow_state=cvd, risk_flags={"rr_too_low": True}))
        return signals
