from decimal import Decimal

from app.risk.rr import calc_rr
from app.signals.models import Signal, SignalLevel
from app.strategies.base import BaseStrategy, StrategyContext
from app.strategies.state_machine import StrategyStateEnum


class EthCm3LiquiditySweepLong(BaseStrategy):
    async def on_market_update(self, context: StrategyContext) -> list[Signal]:
        if context.event.symbol != self.symbol or not context.event.is_closed:
            return []
        signals: list[Signal] = []
        price = context.event.close
        sweep_level = Decimal(str(self.config["levels"]["sweep_level_1"]))
        reclaim = Decimal(str(self.config["levels"]["reclaim_level_1"]))
        tp1 = Decimal(str(self.config["targets"]["tp1"]))
        tp2 = Decimal(str(self.config["targets"]["tp2"]))
        tp3 = Decimal(str(self.config["targets"]["tp3"]))
        min_rr = float(self.config.get("risk", {}).get("min_rr_to_tp1", 1.5))
        btc = self._btc_filter(context.store)
        cvd = self._cvd(context.store, "15m")

        if context.event.low < sweep_level:
            self._set_state(StrategyStateEnum.WATCHING, sweep_low=str(context.event.low))
            signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", "ETH 刺破 1544，进入扫低反杀观察", "15M 收盘继续低于 sweep low 或 BTC 强空", btc_filter=btc, flow_state=cvd))

        sweep_low = Decimal(str(self.state.context.get("sweep_low", "0")))
        if sweep_low and context.event.interval == "15m" and price < sweep_low:
            self._set_state(StrategyStateEnum.INVALID)
            return [self._make_signal(SignalLevel.L4, price, "INVALID", "15M 收盘低于 sweep low，扫低反杀失败", "逆势 Micro 短多失效，不得升级为 Macro 多单", btc_filter=btc, flow_state=cvd)]
        if btc.strong_bearish:
            self._set_state(StrategyStateEnum.INVALID)
            return [self._make_signal(SignalLevel.L4, price, "INVALID", "BTC strong bearish，ETH 逆势短多失效", "暂停 C-M3 多头", btc_filter=btc, flow_state=cvd)]
        if sweep_low and context.event.interval == "15m" and price > reclaim and not cvd.makes_new_low and not btc.strong_bearish:
            stop = sweep_low
            rr = calc_rr("LONG", float(price), float(stop), float(tp1))
            if rr >= min_rr:
                self._set_state(StrategyStateEnum.TRIGGERED)
                signals.append(self._make_signal(SignalLevel.L3, price, "TRIGGERED", "刺破 1544 后 15M 收盘站回 1570，CVD 未创新低，BTC 未强空，R/R 合格", "15M 收盘低于 sweep low 或 BTC strong bearish；不得升级 Macro", entry=price, sl=stop, tp1=tp1, tp2=tp2, tp3=tp3, rr_to_tp1=round(rr, 2), position_r=min(float(self.config.get("risk", {}).get("default_position_r", 0.25)), 0.5), btc_filter=btc, flow_state=cvd))
            else:
                signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", f"扫低反杀接近触发但 R/R={rr:.2f} < {min_rr}，禁止 L3", "等待更好入场或放弃", btc_filter=btc, flow_state=cvd, risk_flags={"rr_too_low": True}))
        return signals
