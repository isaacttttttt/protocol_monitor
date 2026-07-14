from app.indicators.structure import detect_structure_series_values, detect_structure_values


HIGHS = [10, 11, 14, 12, 11, 15, 13, 12]
LOWS = [8, 9, 10, 9, 8, 11, 9, 7]
CLOSES = [9, 10, 13, 10, 9, 14.5, 10, 7]


def test_pivot_is_exposed_only_after_right_side_confirmation():
    unconfirmed = detect_structure_values(HIGHS[:4], LOWS[:4], CLOSES[:4])
    confirmed = detect_structure_values(HIGHS[:5], LOWS[:5], CLOSES[:5])

    assert unconfirmed.last_swing_high == 14
    assert unconfirmed.swing_high_confirmed is False
    assert confirmed.last_swing_high == 14
    assert confirmed.swing_high_confirmed is True


def test_structure_distinguishes_bos_from_choch():
    states = detect_structure_series_values(HIGHS, LOWS, CLOSES)

    assert states[5].bos_up is True
    assert states[5].choch_up is False
    assert states[7].choch_down is True
    assert states[7].bos_down is False
    assert states[7].trend == "DOWN"


def test_future_candles_do_not_repaint_past_structure_states():
    base_states = detect_structure_series_values(HIGHS, LOWS, CLOSES)
    extended_states = detect_structure_series_values(
        HIGHS + [20, 21, 19],
        LOWS + [6, 10, 11],
        CLOSES + [18, 20, 12],
    )

    assert extended_states[: len(base_states)] == base_states
