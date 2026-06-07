from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class NotificationMessage:
    channel: str
    title: str
    body: str
    level: str
    signal_id: str
    symbol: str
    created_at: datetime


@dataclass(frozen=True)
class NotificationResult:
    ok: bool
    error: str | None = None


class BaseNotifier(Protocol):
    async def send(self, message: NotificationMessage) -> NotificationResult:
        ...
