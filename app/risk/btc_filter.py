from dataclasses import dataclass

from app.indicators.cvd import CvdResult
from app.indicators.macd import MacdResult
from app.indicators.structure import StructureState
from app.market.models import Kline


@dataclass(frozen=True)
class BtcFilterState:
    strong_bullish: bool
    strong_bearish: bool
    neutral: bool
    reason: str


def evaluate_btc_filter(candles: list[Kline], macd: MacdResult, cvd: CvdResult, structure: StructureState) -> BtcFilterState:
    if (
        not candles
        or structure.last_swing_high is None
        or structure.last_swing_low is None
        or not structure.swing_high_confirmed
        or not structure.swing_low_confirmed
    ):
        return BtcFilterState(False, False, True, "insufficient BTC context")
    close = float(candles[-1].close)
    strong_bullish = close > structure.last_swing_high and macd.histogram > 0 and cvd.delta > 0
    strong_bearish = close < structure.last_swing_low and macd.histogram < 0 and cvd.delta < 0
    if strong_bullish:
        return BtcFilterState(True, False, False, "BTC 15m broke swing high with positive MACD and CVD")
    if strong_bearish:
        return BtcFilterState(False, True, False, "BTC 15m broke swing low with negative MACD and CVD")
    return BtcFilterState(False, False, True, "BTC neutral")
