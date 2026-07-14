from decimal import Decimal

from app.signals.models import Signal, SignalLevel
from app.strategies.base import BaseStrategy, StrategyContext
from app.strategies.state_machine import StrategyStateEnum


class EthStandAboveLevel(BaseStrategy):
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
        level = Decimal(str(self.config["levels"]["stand_above"]))
        support = Decimal(str(self.config["levels"]["support_low"]))
        fail = Decimal(str(self.config["levels"]["fail_back"]))
        cvd = self._cvd(context.store, "15m")

        if context.event.interval == "5m" and price < fail:
            self._set_state(StrategyStateEnum.INVALID)
            return [self._make_signal(SignalLevel.L4, price, "INVALID", "5M 收盘跌回 1583，站稳 1605 失败", "不要把短线反弹当作宏观反转", flow_state=cvd)]

        if context.event.interval != "15m" or price <= level:
            return signals

        self._set_state(StrategyStateEnum.WATCHING)
        signals.append(self._make_signal(SignalLevel.L2, price, "WATCHING", "15M 收盘站上 1605", "5M 收盘跌回 1583", flow_state=cvd))
        lows = [c.low for c in self._closed_recent(context.store, self.symbol, "5m", 2)]
        last_six = self._closed_recent(context.store, self.symbol, "5m", 6)
        no_fail_30m = all(c.close >= fail for c in last_six)
        if len(lows) == 2 and min(lows) >= support and no_fail_30m and cvd.delta > 0:
            self._set_state(StrategyStateEnum.TRIGGERED)
            signals.append(self._make_signal(SignalLevel.L3, price, "TRIGGERED", "15M 站上 1605，最近两根 5M low >= 1600，30 分钟未跌回 1583，CVD delta > 0", "这不是宏观反转，宏观修复最低仍需 1982-2044", flow_state=cvd, position_r=0.0))
        return signals
