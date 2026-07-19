from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo


NEW_YORK_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


@dataclass(frozen=True)
class XNYSSession:
    session_date: date
    open_time: datetime
    close_time: datetime
    early_close: bool


def xnys_session(session_date: date) -> XNYSSession | None:
    """Return the regular XNYS session, including recurring holiday rules."""
    if not is_xnys_trading_day(session_date):
        return None

    early_close = session_date in _early_closes(session_date.year)
    close = EARLY_CLOSE if early_close else REGULAR_CLOSE
    return XNYSSession(
        session_date=session_date,
        open_time=datetime.combine(session_date, REGULAR_OPEN, tzinfo=NEW_YORK_TZ),
        close_time=datetime.combine(session_date, close, tzinfo=NEW_YORK_TZ),
        early_close=early_close,
    )


def is_xnys_trading_day(session_date: date) -> bool:
    return session_date.weekday() < 5 and session_date not in _holidays(session_date.year)


@lru_cache(maxsize=None)
def _holidays(year: int) -> frozenset[date]:
    holidays = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }

    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        holidays.add(new_year + timedelta(days=1))
    elif new_year.weekday() < 5:
        holidays.add(new_year)

    if year >= 2022:
        holidays.add(_observed_fixed_holiday(date(year, 6, 19)))
    holidays.add(_observed_fixed_holiday(date(year, 7, 4)))
    holidays.add(_observed_fixed_holiday(date(year, 12, 25)))
    return frozenset(holidays)


@lru_cache(maxsize=None)
def _early_closes(year: int) -> frozenset[date]:
    early_closes: set[date] = set()

    thanksgiving = _nth_weekday(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    if is_xnys_trading_day(black_friday):
        early_closes.add(black_friday)

    christmas_eve = date(year, 12, 24)
    if is_xnys_trading_day(christmas_eve):
        early_closes.add(christmas_eve)

    independence_eve = date(year, 7, 3)
    if is_xnys_trading_day(independence_eve):
        early_closes.add(independence_eve)

    return frozenset(early_closes)


def _observed_fixed_holiday(holiday: date) -> date:
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        first_next_month = date(year + 1, 1, 1)
    else:
        first_next_month = date(year, month + 1, 1)
    last = first_next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
