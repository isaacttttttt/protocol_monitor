from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.market.xnys_calendar import NEW_YORK_TZ, xnys_session


@dataclass(frozen=True)
class ScheduledReportDecision:
    due: bool
    local_time: datetime
    slot: datetime | None
    reason: str
    trigger: Literal["scheduled", "manual", "skip"] = "skip"


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

    grace_minutes = int(schedule.get("grace_minutes", 10))
    if grace_minutes < 0 or grace_minutes >= 30:
        raise ValueError("report schedule grace_minutes must be between 0 and 29")

    weekdays = _weekdays(schedule.get("weekdays", [1, 2, 3, 4, 5]))
    configured_slots = [
        datetime.combine(local_now.date(), _parse_time(value), tzinfo=local_timezone)
        for value in (schedule.get("times") or [])
    ]

    candidate_values = schedule.get("candidate_times") or []
    manual_runs = bool(schedule.get("manual_run_outside_candidates", False))
    if manual_runs and not candidate_values:
        raise ValueError(
            "report schedule candidate_times are required when manual runs are enabled"
        )
    if manual_runs:
        candidate_timezone_name = str(
            schedule.get("candidate_timezone") or timezone_name
        )
        try:
            candidate_timezone = ZoneInfo(candidate_timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown report candidate timezone: {candidate_timezone_name}"
            ) from exc
        candidate_now = now.astimezone(candidate_timezone)
        candidate_weekdays = _weekdays(
            schedule.get("candidate_weekdays", [1, 2, 3, 4, 5])
        )
        candidate_slots = [
            datetime.combine(
                candidate_now.date(),
                _parse_time(value),
                tzinfo=candidate_timezone,
            )
            for value in candidate_values
        ]
        candidate = (
            _matching_slot(candidate_now, candidate_slots, grace_minutes)
            if candidate_now.isoweekday() in candidate_weekdays
            else None
        )
        if candidate is None:
            return ScheduledReportDecision(
                True,
                local_now,
                None,
                "manual run outside cron candidate slots",
                "manual",
            )

    if local_now.isoweekday() not in weekdays:
        return ScheduledReportDecision(False, local_now, None, "outside configured weekdays")

    market_now = now.astimezone(NEW_YORK_TZ)
    market_session = xnys_session(market_now.date())
    if market_session is None:
        return ScheduledReportDecision(False, local_now, None, "XNYS market closed")
    if not market_session.open_time <= market_now < market_session.close_time:
        return ScheduledReportDecision(False, local_now, None, "outside XNYS market session")

    slot = _matching_slot(local_now, configured_slots, grace_minutes)
    if slot is not None:
        return ScheduledReportDecision(
            True,
            local_now,
            slot,
            "matched configured slot",
            "scheduled",
        )

    reason = (
        "candidate slot is not a push slot"
        if manual_runs
        else "outside configured time slots"
    )
    return ScheduledReportDecision(False, local_now, None, reason)


def _matching_slot(
    local_now: datetime,
    slots: list[datetime],
    grace_minutes: int,
) -> datetime | None:
    for slot in slots:
        delay_minutes = (local_now - slot).total_seconds() / 60
        if 0 <= delay_minutes <= grace_minutes:
            return slot
    return None


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
