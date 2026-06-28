from app.risk.stops import atr_buffered_stop


def test_atr_buffered_short_stop_is_above_structure_stop():
    assert atr_buffered_stop("SHORT", 1605, atr=20, multiplier=0.2) == 1609


def test_atr_buffered_long_stop_is_below_structure_stop():
    assert atr_buffered_stop("LONG", 1538, atr=20, multiplier=0.3) == 1532

