from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import main
from app.config.settings import Settings
from app.review.report_schedule import evaluate_report_schedule


SCHEDULE = {
    "report_schedule": {
        "enabled": True,
        "timezone": "America/New_York",
        "weekdays": [1, 2, 3, 4, 5],
        "times": ["10:30", "11:30", "12:30", "13:30", "14:30", "15:30"],
        "grace_minutes": 10,
    }
}
NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    "hour,minute",
    [(10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)],
)
def test_weekday_configured_push_times_are_due(hour: int, minute: int):
    local_now = datetime(2026, 7, 13, hour, minute, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(local_now.astimezone(UTC), SCHEDULE)

    assert decision.due is True
    assert decision.slot == local_now


def test_dst_moves_same_new_york_slot_by_one_utc_hour():
    summer = evaluate_report_schedule(datetime(2026, 7, 13, 14, 30, tzinfo=UTC), SCHEDULE)
    winter = evaluate_report_schedule(datetime(2026, 1, 12, 15, 30, tzinfo=UTC), SCHEDULE)

    assert summer.due is True
    assert summer.local_time.hour == 10
    assert winter.due is True
    assert winter.local_time.hour == 10


@pytest.mark.parametrize("hour,minute", [(9, 30), (16, 0), (16, 30)])
def test_candidate_cron_extra_times_do_not_push(hour: int, minute: int):
    local_now = datetime(2026, 7, 13, hour, minute, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(local_now.astimezone(UTC), SCHEDULE)

    assert decision.due is False
    expected_reason = "outside XNYS market session" if hour >= 16 else "outside configured time slots"
    assert decision.reason == expected_reason


def test_weekend_does_not_push_even_at_configured_time():
    saturday = datetime(2026, 7, 18, 10, 30, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(saturday.astimezone(UTC), SCHEDULE)

    assert decision.due is False
    assert decision.reason == "outside configured weekdays"


def test_good_friday_does_not_push():
    good_friday = datetime(2026, 4, 3, 10, 30, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(good_friday.astimezone(UTC), SCHEDULE)

    assert decision.due is False
    assert decision.reason == "XNYS market closed"


def test_early_close_rejects_slots_after_market_close():
    black_friday = datetime(2026, 11, 27, 13, 30, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(black_friday.astimezone(UTC), SCHEDULE)

    assert decision.due is False
    assert decision.reason == "outside XNYS market session"


def test_early_close_keeps_pre_close_slot():
    black_friday = datetime(2026, 11, 27, 12, 30, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(black_friday.astimezone(UTC), SCHEDULE)

    assert decision.due is True
    assert decision.slot == black_friday


@pytest.mark.parametrize(
    "local_now",
    [
        datetime(2026, 7, 2, 15, 30, tzinfo=NEW_YORK),
        datetime(2027, 7, 2, 15, 30, tzinfo=NEW_YORK),
    ],
)
def test_independence_day_observation_does_not_invent_early_close(local_now):
    decision = evaluate_report_schedule(local_now.astimezone(UTC), SCHEDULE)

    assert decision.due is True
    assert decision.slot == local_now


def test_small_railway_start_delay_is_accepted_but_late_run_is_not():
    slot = datetime(2026, 7, 13, 10, 30, tzinfo=NEW_YORK)

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
    extra_candidate = datetime(2026, 7, 13, 16, 0, tzinfo=NEW_YORK)

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
    configured_slot = datetime(2026, 7, 13, 13, 30, tzinfo=NEW_YORK)

    ran = await main.run_scheduled_report(
        hours=1,
        send=True,
        now=configured_slot.astimezone(UTC),
    )

    assert ran is True
    assert calls == [(1, True)]
