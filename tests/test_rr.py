from app.risk.rr import calc_rr


def test_calc_rr_long():
    assert calc_rr("LONG", 100, 90, 130) == 3


def test_calc_rr_short():
    assert calc_rr("SHORT", 100, 110, 80) == 2


def test_calc_rr_invalid_risk():
    assert calc_rr("LONG", 100, 110, 130) == 0
