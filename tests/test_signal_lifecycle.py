from datetime import datetime, timedelta

from app.signals.lifecycle import SignalLifecycleManager


class LifecycleRepo:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    async def get_manageable_signals(self, exchange=None, symbol=None):
        return self.rows

    async def update_signal_lifecycle(self, signal_id, **values):
        self.updates.append((signal_id, values))


def _row(created_at, **overrides):
    row = {
        "signal_id": "signal-1",
        "direction": "LONG",
        "entry": 100,
        "sl": 90,
        "tp1": 115,
        "tp2": 125,
        "book": "Micro",
        "status": "TRIGGERED",
        "tp1_reached": False,
        "created_at": created_at,
    }
    row.update(overrides)
    return row


async def test_lifecycle_marks_stop_before_target_on_ambiguous_bar(kline_factory):
    created = datetime(2026, 1, 1)
    repo = LifecycleRepo([_row(created)])
    candle = kline_factory("ETHUSDT", "5m", 100, 116, 89, 105, minutes=5)

    await SignalLifecycleManager(repo).on_closed_kline(candle)

    assert repo.updates[0][1]["result"] == "LOSS_SL"


async def test_lifecycle_moves_to_managing_at_tp1(kline_factory):
    created = datetime(2026, 1, 1)
    repo = LifecycleRepo([_row(created)])
    candle = kline_factory("ETHUSDT", "5m", 110, 116, 105, 115, minutes=5)

    await SignalLifecycleManager(repo).on_closed_kline(candle)

    assert repo.updates[0][1]["status"] == "MANAGING"
    assert repo.updates[0][1]["tp1_reached"] is True
