from dataclasses import dataclass
from datetime import datetime, timedelta

from app.risk.time_stop import should_time_stop


@dataclass
class FakeSignal:
    status: str
    tp1_reached: bool
    created_at: datetime


def test_48h_time_stop():
    created = datetime(2026, 1, 1)
    assert should_time_stop(FakeSignal("TRIGGERED", False, created), created + timedelta(hours=48))


def test_no_time_stop_after_tp1():
    created = datetime(2026, 1, 1)
    assert not should_time_stop(FakeSignal("TRIGGERED", True, created), created + timedelta(hours=60))
