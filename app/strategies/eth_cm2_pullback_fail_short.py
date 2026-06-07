from decimal import Decimal

from app.risk.rr import calc_rr
from app.signals.models import Signal, SignalLevel
from app.strategies.base import BaseStrategy, StrategyContext
from app.strategies.state_machine import StrategyStateEnum


class EthCm2PullbackFailShort(BaseStrategy):
    async def on_market_update(self, context: StrategyContext) -> list[Signal]:
        if context.event.symbol != self.symbol or not context.event.is_closed:
            return []
        signals: list[Signal] = []
        price = context.event.close
        zone = self.config["zones"]["pullback_zone_1"]
        fail_back = Decimal(str(self.config["levels"]["fail_back_level"]))
        first_invalid = Decimal(str(self.config["levels"]["first_invalid"]))
        stop = Decimal(str(self.config["stop_loss"]["price"]))
        tp1 = Decimal(str(self.config["targets"]["tp1"]))
        tp2 = Decimal(str(self.config["targets"]["tp2"]))
        tp3 = Decimal(str(self.config["targets"]["tp3"]))
        min_rr = float(self.config.get("risk", {}).get("min_rr_to_tp1", 1.5))
        btc = self._btc_filter(context.store)
        cvd = self._cvd(context.store, "15m")

        if context.event.interval == "15m" and price > first_invalid:
            self._set_state(StrategyStateEnum.INVALID)
            signals.append(self._make_signal(SignalLevel.L4, price, "INVALID", "15M 收盘站上 1605，C-M2 做空失效", "放弃 C-M2 空头等待新结构", btc_filter=btc, flow_state=cvd))
            return signals
        if btc.strong_bullish:
            self._set_state(StrategyStateEnum.INVALID)
            signals.append(self._make_signal(SignalLevel.L4, price, "INVALID", "BTC 强反弹，ETH 做空条件降级/失效", "暂停 C-M2 L3 做空", btc_filter=btc, flow_state=cvd))
            return signals
        if Decimal(str(zone["low"])) <= price <= Decimal(str(zone["high"])):
            self._set_state(StrategyStateEnum.WATCHING, touched_zone=True)
            signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", "价格进入 1583-1605 第一反抽压力区", "15M 收盘站上 1605 或 BTC 强反弹", btc_filter=btc, flow_state=cvd))

        touched = bool(self.state.context.get("touched_zone"))
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
                    )
                )
            else:
                signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", f"触发价接近但 R/R={rr:.2f} < {min_rr}，禁止 L3", "等待更好入场或放弃", btc_filter=btc, flow_state=cvd, risk_flags={"rr_too_low": True}))
        return signals
