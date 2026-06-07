from datetime import datetime, timedelta

from app.risk.cooldown import SignalCooldown


def test_signal_cooldown_dedupes():
    cd = SignalCooldown()
    now = datetime(2026, 1, 1)
    assert cd.allowed("ETHUSDT", "s1", "L2", "same", now, 30)
    assert not cd.allowed("ETHUSDT", "s1", "L2", "same", now + timedelta(minutes=5), 30)
    assert cd.allowed("ETHUSDT", "s1", "L2", "same", now + timedelta(minutes=31), 30)
