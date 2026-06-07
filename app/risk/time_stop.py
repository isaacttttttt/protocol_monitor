from datetime import datetime, timedelta
from typing import Protocol


class TimeStopSignal(Protocol):
    status: str
    tp1_reached: bool
    created_at: datetime


def should_time_stop(signal: TimeStopSignal, now: datetime, max_hold_hours: int = 48) -> bool:
    if signal.status not in {"TRIGGERED", "MANAGING"}:
        return False
    if signal.tp1_reached:
        return False
    return now - signal.created_at >= timedelta(hours=max_hold_hours)
