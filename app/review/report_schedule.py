from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.market.xnys_calendar import NEW_YORK_TZ, xnys_session


@dataclass(frozen=True)
class ScheduledReportDecision:
    due: bool
    local_time: datetime
    slot: datetime | None
    reason: str


def evaluate_report_schedule(
    now: datetime,
    automation_config: dict,
) -> ScheduledReportDecision:
    """Evaluate a Railway candidate run against the configured local schedule."""
    if now.tzinfo is None:
        raise ValueError("scheduled report time must be timezone-aware")

    schedule = automation_config.get("report_schedule") or {}
    timezone_name = str(schedule.get("timezone") or "America/New_York")
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown report schedule timezone: {timezone_name}") from exc

    local_now = now.astimezone(local_timezone)
    if not bool(schedule.get("enabled", False)):
        return ScheduledReportDecision(False, local_now, None, "schedule disabled")

    weekdays = _weekdays(schedule.get("weekdays", [1, 2, 3, 4, 5]))
    if local_now.isoweekday() not in weekdays:
        return ScheduledReportDecision(False, local_now, None, "outside configured weekdays")

    market_now = now.astimezone(NEW_YORK_TZ)
    market_session = xnys_session(market_now.date())
    if market_session is None:
        return ScheduledReportDecision(False, local_now, None, "XNYS market closed")
    if not market_session.open_time <= market_now < market_session.close_time:
        return ScheduledReportDecision(False, local_now, None, "outside XNYS market session")

    grace_minutes = int(schedule.get("grace_minutes", 10))
    if grace_minutes < 0 or grace_minutes >= 30:
        raise ValueError("report schedule grace_minutes must be between 0 and 29")

    configured_times = schedule.get("times") or []
    slots = [
        datetime.combine(local_now.date(), _parse_time(value), tzinfo=local_timezone)
        for value in configured_times
    ]
    for slot in slots:
        delay_minutes = (local_now - slot).total_seconds() / 60
        if 0 <= delay_minutes <= grace_minutes:
            return ScheduledReportDecision(True, local_now, slot, "matched configured slot")

    return ScheduledReportDecision(False, local_now, None, "outside configured time slots")


def _parse_time(value: object) -> time:
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"invalid report schedule time: {value!r}") from exc


def _weekdays(values: object) -> frozenset[int]:
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("report schedule weekdays must be a list of ISO weekday numbers")
    weekdays = frozenset(int(value) for value in values)
    if not weekdays or any(value < 1 or value > 7 for value in weekdays):
        raise ValueError("report schedule weekdays must contain values from 1 to 7")
    return weekdays
