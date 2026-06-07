from datetime import datetime, timedelta


class SignalCooldown:
    def __init__(self) -> None:
        self._last_sent: dict[tuple[str, str, str, str], datetime] = {}

    def allowed(self, symbol: str, strategy_id: str, level: str, reason: str, now: datetime, minutes: int) -> bool:
        key = (symbol, strategy_id, level, reason if level == "L4" else "")
        previous = self._last_sent.get(key)
        if previous and now - previous < timedelta(minutes=minutes):
            return False
        self._last_sent[key] = now
        return True
