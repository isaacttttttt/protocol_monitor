from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import main
from app.config.settings import Settings
from app.review.report_schedule import evaluate_report_schedule


SCHEDULE = {
    "report_schedule": {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "weekdays": [1, 2, 3, 4, 5],
        "times": ["00:00", "10:00", "21:30", "22:00", "22:30", "23:00"],
        "grace_minutes": 10,
    }
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    "hour,minute",
    [(0, 0), (10, 0), (21, 30), (22, 0), (22, 30), (23, 0)],
)
def test_weekday_configured_push_times_are_due(hour: int, minute: int):
    local_now = datetime(2026, 7, 13, hour, minute, tzinfo=SHANGHAI)

    decision = evaluate_report_schedule(local_now.astimezone(UTC), SCHEDULE)

    assert decision.due is True
    assert decision.slot == local_now


def test_local_monday_midnight_uses_sunday_utc_candidate():
    sunday_utc = datetime(2026, 7, 12, 16, 0, tzinfo=UTC)

    decision = evaluate_report_schedule(sunday_utc, SCHEDULE)

    assert decision.due is True
    assert decision.local_time == datetime(2026, 7, 13, 0, 0, tzinfo=SHANGHAI)


@pytest.mark.parametrize("hour,minute", [(10, 30), (21, 0), (23, 30)])
def test_candidate_cron_extra_times_do_not_push(hour: int, minute: int):
    local_now = datetime(2026, 7, 13, hour, minute, tzinfo=SHANGHAI)

    decision = evaluate_report_schedule(local_now.astimezone(UTC), SCHEDULE)

    assert decision.due is False
    assert decision.reason == "outside configured time slots"


def test_weekend_does_not_push_even_at_configured_time():
    saturday = datetime(2026, 7, 18, 10, 0, tzinfo=SHANGHAI)

    decision = evaluate_report_schedule(saturday.astimezone(UTC), SCHEDULE)

    assert decision.due is False
    assert decision.reason == "outside configured weekdays"


def test_small_railway_start_delay_is_accepted_but_late_run_is_not():
    slot = datetime(2026, 7, 13, 10, 0, tzinfo=SHANGHAI)

    accepted = evaluate_report_schedule((slot + timedelta(minutes=9)).astimezone(UTC), SCHEDULE)
    rejected = evaluate_report_schedule((slot + timedelta(minutes=11)).astimezone(UTC), SCHEDULE)

    assert accepted.due is True
    assert rejected.due is False


def test_schedule_requires_timezone_aware_now():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_report_schedule(datetime(2026, 7, 13, 10, 0), SCHEDULE)


@pytest.mark.asyncio
async def test_scheduled_entrypoint_skips_report_work_outside_allowlist(monkeypatch):
    calls = []

    async def fake_run_report(*, hours, send):
        calls.append((hours, send))

    monkeypatch.setattr(main, "get_settings", lambda: Settings(log_level="CRITICAL"))
    monkeypatch.setattr(main, "load_system_config", lambda: {"automation": SCHEDULE})
    monkeypatch.setattr(main, "run_report", fake_run_report)
    extra_candidate = datetime(2026, 7, 13, 10, 30, tzinfo=SHANGHAI)

    ran = await main.run_scheduled_report(
        hours=1,
        send=True,
        now=extra_candidate.astimezone(UTC),
    )

    assert ran is False
    assert calls == []


@pytest.mark.asyncio
async def test_scheduled_entrypoint_runs_report_at_allowed_slot(monkeypatch):
    calls = []

    async def fake_run_report(*, hours, send):
        calls.append((hours, send))

    monkeypatch.setattr(main, "get_settings", lambda: Settings(log_level="CRITICAL"))
    monkeypatch.setattr(main, "load_system_config", lambda: {"automation": SCHEDULE})
    monkeypatch.setattr(main, "run_report", fake_run_report)
    configured_slot = datetime(2026, 7, 13, 21, 30, tzinfo=SHANGHAI)

    ran = await main.run_scheduled_report(
        hours=1,
        send=True,
        now=configured_slot.astimezone(UTC),
    )

    assert ran is True
    assert calls == [(1, True)]
