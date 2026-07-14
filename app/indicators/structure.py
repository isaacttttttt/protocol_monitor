from dataclasses import dataclass
from typing import Literal, Sequence

from app.market.models import Kline


@dataclass(frozen=True)
class StructureState:
    trend: Literal["UP", "DOWN", "RANGE"]
    bos_up: bool
    bos_down: bool
    choch_up: bool
    choch_down: bool
    last_swing_high: float | None
    last_swing_low: float | None
    swing_high_confirmed: bool = False
    swing_low_confirmed: bool = False


def detect_structure(
    candles: list[Kline],
    lookback: int = 20,
    pivot_left: int = 2,
    pivot_right: int = 2,
) -> StructureState:
    """Return the latest causal market-structure state.

    A pivot becomes usable only after ``pivot_right`` closed candles confirm it.
    ``lookback`` limits how much history is evaluated, but never changes the
    confirmation delay.
    """
    window = candles[-lookback:] if lookback > 0 else candles
    return detect_structure_values(
        [float(candle.high) for candle in window],
        [float(candle.low) for candle in window],
        [float(candle.close) for candle in window],
        pivot_left=pivot_left,
        pivot_right=pivot_right,
    )


def detect_structure_values(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    pivot_left: int = 2,
    pivot_right: int = 2,
) -> StructureState:
    states = detect_structure_series_values(
        highs,
        lows,
        closes,
        pivot_left=pivot_left,
        pivot_right=pivot_right,
    )
    return states[-1] if states else _empty_state()


def detect_structure_series_values(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    pivot_left: int = 2,
    pivot_right: int = 2,
) -> list[StructureState]:
    """Build a causal state series whose past values cannot be repainted."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must have equal lengths")
    if pivot_left < 1 or pivot_right < 1:
        raise ValueError("pivot_left and pivot_right must be positive")

    trend: Literal["UP", "DOWN", "RANGE"] = "RANGE"
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    high_is_broken = True
    low_is_broken = True
    states: list[StructureState] = []

    for index, close_value in enumerate(closes):
        bos_up = bos_down = choch_up = choch_down = False
        confirmed_index = index - pivot_right
        new_high = False
        new_low = False

        if confirmed_index >= pivot_left:
            start = confirmed_index - pivot_left
            end = confirmed_index + pivot_right + 1
            high_window = [float(value) for value in highs[start:end]]
            low_window = [float(value) for value in lows[start:end]]
            candidate_high = float(highs[confirmed_index])
            candidate_low = float(lows[confirmed_index])

            if candidate_high == max(high_window) and high_window.count(candidate_high) == 1:
                last_swing_high = candidate_high
                high_is_broken = False
                new_high = True
            if candidate_low == min(low_window) and low_window.count(candidate_low) == 1:
                last_swing_low = candidate_low
                low_is_broken = False
                new_low = True

        previous_close = float(closes[index - 1]) if index else float(close_value)
        close = float(close_value)
        breaks_high = (
            last_swing_high is not None
            and not high_is_broken
            and close > last_swing_high
            and (previous_close <= last_swing_high or new_high)
        )
        breaks_low = (
            last_swing_low is not None
            and not low_is_broken
            and close < last_swing_low
            and (previous_close >= last_swing_low or new_low)
        )

        if breaks_high:
            if trend == "DOWN":
                choch_up = True
            else:
                bos_up = True
            trend = "UP"
            high_is_broken = True
        elif breaks_low:
            if trend == "UP":
                choch_down = True
            else:
                bos_down = True
            trend = "DOWN"
            low_is_broken = True

        reference_start = max(0, index - 20)
        reference_high = last_swing_high
        reference_low = last_swing_low
        if index > 0 and reference_high is None:
            reference_high = max(float(value) for value in highs[reference_start:index])
        if index > 0 and reference_low is None:
            reference_low = min(float(value) for value in lows[reference_start:index])

        states.append(
            StructureState(
                trend=trend,
                bos_up=bos_up,
                bos_down=bos_down,
                choch_up=choch_up,
                choch_down=choch_down,
                last_swing_high=reference_high,
                last_swing_low=reference_low,
                swing_high_confirmed=last_swing_high is not None,
                swing_low_confirmed=last_swing_low is not None,
            )
        )

    return states


def _empty_state() -> StructureState:
    return StructureState("RANGE", False, False, False, False, None, None)
