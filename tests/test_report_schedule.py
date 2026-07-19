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
        "candidate_timezone": "UTC",
        "candidate_weekdays": [1, 2, 3, 4, 5],
        "candidate_times": [
            "14:30",
            "15:30",
            "16:30",
            "17:30",
            "18:30",
            "19:30",
            "20:30",
        ],
        "manual_run_outside_candidates": True,
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
    assert decision.trigger == "scheduled"


def test_dst_moves_same_new_york_slot_by_one_utc_hour():
    summer = evaluate_report_schedule(datetime(2026, 7, 13, 14, 30, tzinfo=UTC), SCHEDULE)
    winter = evaluate_report_schedule(datetime(2026, 1, 12, 15, 30, tzinfo=UTC), SCHEDULE)

    assert summer.due is True
    assert summer.local_time.hour == 10
    assert winter.due is True
    assert winter.local_time.hour == 10


@pytest.mark.parametrize(
    "run_time,expected_reason",
    [
        (datetime(2026, 7, 13, 20, 30, tzinfo=UTC), "outside XNYS market session"),
        (datetime(2026, 1, 12, 14, 30, tzinfo=UTC), "candidate slot is not a push slot"),
    ],
)
def test_candidate_cron_extra_times_do_not_push(run_time: datetime, expected_reason: str):
    decision = evaluate_report_schedule(run_time, SCHEDULE)

    assert decision.due is False
    assert decision.reason == expected_reason
    assert decision.trigger == "skip"


def test_weekend_run_is_manual_even_at_configured_time():
    saturday = datetime(2026, 7, 18, 10, 30, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(saturday.astimezone(UTC), SCHEDULE)

    assert decision.due is True
    assert decision.reason == "manual run outside cron candidate slots"
    assert decision.trigger == "manual"


def test_schedule_without_manual_runs_skips_weekend():
    schedule = {
        **SCHEDULE,
        "report_schedule": {
            **SCHEDULE["report_schedule"],
            "manual_run_outside_candidates": False,
        },
    }
    saturday = datetime(2026, 7, 18, 10, 30, tzinfo=NEW_YORK)

    decision = evaluate_report_schedule(saturday.astimezone(UTC), schedule)

    assert decision.due is False
    assert decision.reason == "outside configured weekdays"
    assert decision.trigger == "skip"


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


def test_small_railway_start_delay_is_scheduled_and_late_run_is_manual():
    slot = datetime(2026, 7, 13, 10, 30, tzinfo=NEW_YORK)

    accepted = evaluate_report_schedule((slot + timedelta(minutes=9)).astimezone(UTC), SCHEDULE)
    rejected = evaluate_report_schedule((slot + timedelta(minutes=11)).astimezone(UTC), SCHEDULE)

    assert accepted.due is True
    assert accepted.trigger == "scheduled"
    assert rejected.due is True
    assert rejected.trigger == "manual"


def test_schedule_requires_timezone_aware_now():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_report_schedule(datetime(2026, 7, 13, 10, 0), SCHEDULE)


def test_manual_run_can_execute_on_a_market_holiday_outside_candidate_window():
    manual_time = datetime(2026, 4, 3, 16, 54, tzinfo=UTC)

    decision = evaluate_report_schedule(manual_time, SCHEDULE)

    assert decision.due is True
    assert decision.trigger == "manual"


@pytest.mark.parametrize(
    "manual_time",
    [
        datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 13, 22, 0, tzinfo=UTC),
    ],
)
def test_manual_run_can_execute_before_or_after_market_session(manual_time: datetime):
    decision = evaluate_report_schedule(manual_time, SCHEDULE)

    assert decision.due is True
    assert decision.trigger == "manual"


def test_disabled_schedule_rejects_manual_run():
    schedule = {
        "report_schedule": {
            **SCHEDULE["report_schedule"],
            "enabled": False,
        }
    }

    decision = evaluate_report_schedule(datetime(2026, 7, 13, 17, 54, tzinfo=UTC), schedule)

    assert decision.due is False
    assert decision.reason == "schedule disabled"
    assert decision.trigger == "skip"


def test_manual_runs_require_candidate_times():
    schedule = {
        "report_schedule": {
            **SCHEDULE["report_schedule"],
            "candidate_times": [],
        }
    }

    with pytest.raises(ValueError, match="candidate_times"):
        evaluate_report_schedule(datetime(2026, 7, 13, 16, 54, tzinfo=UTC), schedule)


def test_manual_runs_validate_candidate_timezone():
    schedule = {
        "report_schedule": {
            **SCHEDULE["report_schedule"],
            "candidate_timezone": "Not/A-Timezone",
        }
    }

    with pytest.raises(ValueError, match="candidate timezone"):
        evaluate_report_schedule(datetime(2026, 7, 13, 16, 54, tzinfo=UTC), schedule)


@pytest.mark.asyncio
async def test_scheduled_entrypoint_skips_report_work_outside_allowlist(monkeypatch):
    calls = []

    async def fake_run_report(*, hours, send):
        calls.append((hours, send))

    monkeypatch.setattr(main, "get_settings", lambda: Settings(log_level="CRITICAL"))
    monkeypatch.setattr(main, "load_system_config", lambda: {"automation": SCHEDULE})
    monkeypatch.setattr(main, "run_report", fake_run_report)
    extra_candidate = datetime(2026, 7, 13, 20, 30, tzinfo=UTC)

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


@pytest.mark.asyncio
async def test_scheduled_entrypoint_runs_report_for_manual_button(monkeypatch):
    calls = []

    async def fake_run_report(*, hours, send):
        calls.append((hours, send))

    monkeypatch.setattr(main, "get_settings", lambda: Settings(log_level="CRITICAL"))
    monkeypatch.setattr(main, "load_system_config", lambda: {"automation": SCHEDULE})
    monkeypatch.setattr(main, "run_report", fake_run_report)
    manual_time = datetime(2026, 7, 13, 17, 54, tzinfo=UTC)

    ran = await main.run_scheduled_report(
        hours=1,
        send=True,
        now=manual_time,
    )

    assert ran is True
    assert calls == [(1, True)]
