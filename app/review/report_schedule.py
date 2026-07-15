from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    timezone_name = str(schedule.get("timezone") or "Asia/Shanghai")
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

    configured_slots = [
        datetime.combine(local_now.date(), _parse_time(value), tzinfo=local_timezone)
        for value in (schedule.get("times") or [])
    ]
    weekdays = _weekdays(schedule.get("weekdays", [1, 2, 3, 4, 5]))
    if local_now.isoweekday() in weekdays:
        slot = _matching_slot(local_now, configured_slots, grace_minutes)
        if slot is not None:
            return ScheduledReportDecision(
                True,
                local_now,
                slot,
                "matched configured slot",
                "scheduled",
            )

    candidate_values = schedule.get("candidate_times") or []
    manual_runs = bool(schedule.get("manual_run_outside_candidates", False))
    if manual_runs and not candidate_values:
        raise ValueError(
            "report schedule candidate_times are required when manual runs are enabled"
        )
    candidate_slots = [
        datetime.combine(local_now.date(), _parse_time(value), tzinfo=local_timezone)
        for value in candidate_values
    ]
    candidate = _matching_slot(local_now, candidate_slots, grace_minutes)
    if candidate is not None:
        reason = (
            "outside configured weekdays"
            if local_now.isoweekday() not in weekdays
            else "candidate slot is not a push slot"
        )
        return ScheduledReportDecision(False, local_now, candidate, reason)

    if manual_runs:
        return ScheduledReportDecision(
            True,
            local_now,
            None,
            "manual run outside cron candidate slots",
            "manual",
        )

    return ScheduledReportDecision(False, local_now, None, "outside configured time slots")


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
