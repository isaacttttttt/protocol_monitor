from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

from app.market.models import Kline

if TYPE_CHECKING:
    from app.backtest.engine import FundingRate

BINANCE_VISION_BASE_URL = "https://data.binance.vision/data/futures/um"
BINANCE_FUTURES_REST_BASE_URL = "https://fapi.binance.com/fapi/v1"
SUPPORTED_SOURCE_INTERVAL = "5m"

_INTERVALS = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "4h": timedelta(hours=4),
}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class _Response(Protocol):
    def __enter__(self) -> BinaryIO: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...


OpenUrl = Callable[..., _Response]
class BinanceArchiveError(RuntimeError):
    """Base error for Binance archive download and parsing failures."""


class ChecksumMismatchError(BinanceArchiveError):
    """Raised when an archive does not match its published SHA-256 digest."""


class ArchiveFormatError(BinanceArchiveError):
    """Raised when a ZIP or CSV archive is malformed."""


class RestFundingUnavailableError(BinanceArchiveError):
    """Raised when the official funding-history REST endpoint is unreachable."""


class KlineValidationError(BinanceArchiveError):
    """Raised when strict Kline validation finds one or more defects."""

    def __init__(self, report: KlineValidationReport) -> None:
        self.report = report
        super().__init__(report.summary())


@dataclass(frozen=True)
class FundingRateRow:
    """One row from Binance's USD-M funding-rate archive."""

    calc_time: datetime
    funding_interval_hours: int | None
    last_funding_rate: Decimal
    mark_price: Decimal | None = None
    symbol: str | None = None

    @property
    def time(self) -> datetime:
        """Return the event timestamp using the backtest engine's naming."""
        return self.calc_time

    @property
    def rate(self) -> float:
        """Return the funding rate using the backtest engine's naming."""
        return float(self.last_funding_rate)

    def to_engine(self) -> FundingRate:
        """Convert this archive row to the backtest engine's funding model."""
        from app.backtest.engine import FundingRate

        return FundingRate(
            time=self.calc_time,
            rate=float(self.last_funding_rate),
            mark_price=float(self.mark_price) if self.mark_price is not None else None,
        )


FundingRateRecord = FundingRateRow


@dataclass(frozen=True)
class MetricsRow:
    """One five-minute row from Binance's USD-M daily metrics archive."""

    create_time: datetime
    symbol: str
    sum_open_interest: Decimal
    sum_open_interest_value: Decimal
    count_toptrader_long_short_ratio: Decimal | None
    sum_toptrader_long_short_ratio: Decimal | None
    count_long_short_ratio: Decimal | None
    sum_taker_long_short_vol_ratio: Decimal | None


MetricsRecord = MetricsRow


@dataclass(frozen=True)
class KlineGap:
    """An unexpected interval between two unique Kline open times."""

    previous_open_time: datetime
    next_open_time: datetime
    actual_delta: timedelta
    missing_bars: int


@dataclass(frozen=True)
class KlineIssue:
    """A row-level Kline validation problem."""

    index: int
    open_time: datetime
    reason: str


@dataclass(frozen=True)
class KlineValidationReport:
    """Completeness and price-integrity findings for a Kline sequence."""

    duplicate_open_times: tuple[datetime, ...] = ()
    gaps: tuple[KlineGap, ...] = ()
    invalid_ohlc: tuple[KlineIssue, ...] = ()
    out_of_order: bool = False

    @property
    def is_valid(self) -> bool:
        """Return true when no duplicate, gap, ordering, or OHLC defect exists."""
        return not (
            self.duplicate_open_times
            or self.gaps
            or self.invalid_ohlc
            or self.out_of_order
        )

    @property
    def valid(self) -> bool:
        """Alias for callers that prefer a shorter report attribute."""
        return self.is_valid

    def summary(self) -> str:
        """Return a compact human-readable validation summary."""
        return (
            "Kline validation failed: "
            f"duplicates={len(self.duplicate_open_times)}, "
            f"gaps={len(self.gaps)}, "
            f"invalid={len(self.invalid_ohlc)}, "
            f"out_of_order={self.out_of_order}"
        )


class BinanceArchiveClient:
    """Download and cache public Binance USD-M historical archives.

    The client performs no authenticated exchange operations. Cached files are
    stored under the same relative directory hierarchy as data.binance.vision.
    When Binance publishes a sibling ``.CHECKSUM`` file, its SHA-256 digest is
    retained beside the ZIP and checked before the archive becomes visible.
    """

    def __init__(
        self,
        cache_dir: str | os.PathLike[str],
        *,
        base_url: str = BINANCE_VISION_BASE_URL,
        rest_base_url: str = BINANCE_FUTURES_REST_BASE_URL,
        timeout_seconds: float = 30.0,
        opener: OpenUrl = urlopen,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.cache_dir = Path(cache_dir)
        self.base_url = base_url.rstrip("/")
        self.rest_base_url = rest_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = opener

    def download_monthly_klines(
        self,
        symbol: str,
        year: int,
        month: int,
        *,
        interval: str = SUPPORTED_SOURCE_INTERVAL,
        force: bool = False,
    ) -> Path:
        """Download one monthly USD-M Kline ZIP."""
        symbol = _symbol(symbol)
        interval = _source_interval(interval)
        period = _month_label(year, month)
        filename = f"{symbol}-{interval}-{period}.zip"
        return self.download(
            PurePosixPath("monthly", "klines", symbol, interval, filename),
            force=force,
        )

    def download_daily_klines(
        self,
        symbol: str,
        day: date,
        *,
        interval: str = SUPPORTED_SOURCE_INTERVAL,
        force: bool = False,
    ) -> Path:
        """Download one daily USD-M Kline ZIP."""
        symbol = _symbol(symbol)
        interval = _source_interval(interval)
        day = _as_date(day)
        filename = f"{symbol}-{interval}-{day.isoformat()}.zip"
        return self.download(
            PurePosixPath("daily", "klines", symbol, interval, filename),
            force=force,
        )

    def download_monthly_funding_rates(
        self,
        symbol: str,
        year: int,
        month: int,
        *,
        force: bool = False,
    ) -> Path:
        """Download one monthly USD-M funding-rate ZIP."""
        symbol = _symbol(symbol)
        period = _month_label(year, month)
        filename = f"{symbol}-fundingRate-{period}.zip"
        return self.download(
            PurePosixPath("monthly", "fundingRate", symbol, filename),
            force=force,
        )

    def download_daily_metrics(
        self,
        symbol: str,
        day: date,
        *,
        force: bool = False,
    ) -> Path:
        """Download one daily USD-M derivatives-metrics ZIP."""
        symbol = _symbol(symbol)
        day = _as_date(day)
        filename = f"{symbol}-metrics-{day.isoformat()}.zip"
        return self.download(
            PurePosixPath("daily", "metrics", symbol, filename),
            force=force,
        )

    def download_kline_range(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        interval: str = SUPPORTED_SOURCE_INTERVAL,
        force: bool = False,
    ) -> list[Path]:
        """Download an inclusive date range, preferring complete monthly ZIPs.

        A partial first or last month is fetched from daily archives. If a full
        monthly ZIP is not yet published, the method falls back to daily ZIPs.
        """
        start, end = _date_range(start, end)
        files: list[Path] = []
        for month_start in _month_starts(start, end):
            month_end = _last_day_of_month(month_start)
            covered_start = max(start, month_start)
            covered_end = min(end, month_end)
            if covered_start == month_start and covered_end == month_end:
                try:
                    files.append(
                        self.download_monthly_klines(
                            symbol,
                            month_start.year,
                            month_start.month,
                            interval=interval,
                            force=force,
                        )
                    )
                    continue
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
            for day in _days(covered_start, covered_end):
                files.append(
                    self.download_daily_klines(
                        symbol,
                        day,
                        interval=interval,
                        force=force,
                    )
                )
        return files

    def download_funding_rate_range(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        force: bool = False,
    ) -> list[Path]:
        """Download all monthly funding-rate ZIPs intersecting a date range."""
        start, end = _date_range(start, end)
        return [
            self.download_monthly_funding_rates(
                symbol,
                month_start.year,
                month_start.month,
                force=force,
            )
            for month_start in _month_starts(start, end)
        ]

    def download_metrics_range(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        force: bool = False,
    ) -> list[Path]:
        """Download all daily metrics ZIPs in an inclusive date range."""
        start, end = _date_range(start, end)
        return [self.download_daily_metrics(symbol, day, force=force) for day in _days(start, end)]

    def load_kline_range(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        interval: str = SUPPORTED_SOURCE_INTERVAL,
        force: bool = False,
        strict: bool = True,
    ) -> list[Kline]:
        """Download, parse, date-filter, sort, and validate a Kline range."""
        start, end = _date_range(start, end)
        bars = [
            bar
            for path in self.download_kline_range(
                symbol,
                start,
                end,
                interval=interval,
                force=force,
            )
            for bar in parse_kline_zip(path, symbol=symbol, interval=interval)
            if start <= bar.open_time.date() <= end
        ]
        bars.sort(key=lambda bar: bar.open_time)
        validate_klines(bars, expected_interval=interval, strict=strict)
        return bars

    def load_funding_rate_range(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        force: bool = False,
    ) -> list[FundingRateRow]:
        """Load funding rates from monthly archives with an official REST fallback.

        Binance Vision does not publish daily funding-rate files. A month whose
        archive is not published yet (normally the current month) is therefore
        fetched from ``GET /fapi/v1/fundingRate`` and cached as raw JSON pages.
        """
        start, end = _date_range(start, end)
        symbol = _symbol(symbol)
        rows: list[FundingRateRow] = []
        for month_start in _month_starts(start, end):
            month_end = _last_day_of_month(month_start)
            covered_start = max(start, month_start)
            covered_end = min(end, month_end)
            try:
                path = self.download_monthly_funding_rates(
                    symbol,
                    month_start.year,
                    month_start.month,
                    force=force,
                )
            except HTTPError as exc:
                if exc.code != 404:
                    raise
                range_start = datetime.combine(covered_start, time.min)
                range_end = datetime.combine(covered_end, time.max).replace(microsecond=999_000)
                rows.extend(
                    self.fetch_rest_funding_rates(
                        symbol,
                        range_start,
                        range_end,
                        force=force,
                    )
                )
                continue
            archived = parse_funding_rate_zip(path)
            _validate_funding_rows(
                archived,
                datetime.combine(month_start, time.min),
                datetime.combine(month_end, time.max),
                symbol=None,
            )
            rows.extend(row for row in archived if covered_start <= row.calc_time.date() <= covered_end)

        rows.sort(key=lambda row: row.calc_time)
        _validate_funding_rows(
            rows,
            datetime.combine(start, time.min),
            datetime.combine(end, time.max),
            symbol=symbol,
        )
        return rows

    def fetch_rest_funding_rates(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        force: bool = False,
    ) -> list[FundingRateRow]:
        """Fetch and cache paginated official USD-M funding history.

        Every raw API page is stored unchanged beneath
        ``rest/fundingRate/<symbol>``. Rows are accepted only when their symbol
        and timestamp lie inside the exact query range, and each page must be
        strictly ordered without duplicate funding timestamps.
        """
        symbol = _symbol(symbol)
        start = _naive_utc(start)
        end = _naive_utc(end)
        if start > end:
            raise ValueError("start must be on or before end")
        start_ms = _datetime_to_milliseconds(start)
        end_ms = _datetime_to_milliseconds(end)
        current_start_ms = start_ms
        rows: list[FundingRateRow] = []
        while current_start_ms <= end_ms:
            page = self._rest_funding_page(
                symbol,
                current_start_ms,
                end_ms,
                force=force,
            )
            if not page:
                break
            rows.extend(page)
            if len(page) < 1000:
                break
            next_start_ms = _datetime_to_milliseconds(page[-1].calc_time) + 1
            if next_start_ms <= current_start_ms:
                raise ArchiveFormatError("Binance funding pagination did not advance")
            current_start_ms = next_start_ms
        _validate_funding_rows(rows, start, end, symbol=symbol)
        return rows

    def _rest_funding_page(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
        *,
        force: bool,
    ) -> list[FundingRateRow]:
        query = urlencode(
            {
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        url = f"{self.rest_base_url}/fundingRate?{query}"
        target = self.cache_dir.joinpath(
            "rest",
            "fundingRate",
            symbol,
            f"{symbol}-fundingRate-{start_ms}-{end_ms}-limit1000.json",
        )
        if target.exists() and not force:
            raw = target.read_bytes()
            return _parse_rest_funding_page(raw, symbol, start_ms, end_ms, target.name)

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary_path(target)
        try:
            try:
                self._stream_to_file(url, temporary)
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                raise RestFundingUnavailableError(
                    f"Binance funding REST request failed for {symbol} "
                    f"between {start_ms} and {end_ms}: {exc}"
                ) from exc
            raw = temporary.read_bytes()
            rows = _parse_rest_funding_page(raw, symbol, start_ms, end_ms, target.name)
            os.replace(temporary, target)
            return rows
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def load_metrics_range(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        force: bool = False,
    ) -> list[MetricsRow]:
        """Download, parse, and date-filter a daily metrics range."""
        start, end = _date_range(start, end)
        rows = [
            row
            for path in self.download_metrics_range(symbol, start, end, force=force)
            for row in parse_metrics_zip(path)
            if start <= row.create_time.date() <= end
        ]
        return sorted(rows, key=lambda row: row.create_time)

    def download(self, relative_path: PurePosixPath | str, *, force: bool = False) -> Path:
        """Download one relative archive path and return its cache location."""
        relative_path = _safe_relative_path(relative_path)
        target = self.cache_dir.joinpath(*relative_path.parts)
        checksum_path = Path(f"{target}.CHECKSUM")
        if target.exists() and not force:
            if checksum_path.exists():
                expected = _checksum_digest(checksum_path.read_text(encoding="ascii"))
                _verify_checksum(target, expected)
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        archive_url = f"{self.base_url}/{relative_path.as_posix()}"
        checksum_text = self._optional_checksum(f"{archive_url}.CHECKSUM")
        expected = _checksum_digest(checksum_text) if checksum_text is not None else None
        temporary = _temporary_path(target)
        try:
            self._stream_to_file(archive_url, temporary)
            if expected is not None:
                _verify_checksum(temporary, expected)
            os.replace(temporary, target)
            if checksum_text is not None:
                _atomic_write_text(checksum_path, checksum_text)
            elif checksum_path.exists():
                checksum_path.unlink()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def _optional_checksum(self, url: str) -> str | None:
        try:
            with self._opener(url, timeout=self.timeout_seconds) as response:
                return response.read().decode("ascii")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def _stream_to_file(self, url: str, target: Path) -> None:
        with self._opener(url, timeout=self.timeout_seconds) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)


def parse_kline_zip(
    path: str | os.PathLike[str],
    *,
    symbol: str | None = None,
    interval: str | None = None,
) -> list[Kline]:
    """Parse a Binance Kline ZIP into the application's Kline model."""
    path = Path(path)
    inferred_symbol, inferred_interval = _infer_kline_identity(path.name)
    symbol = _symbol(symbol or inferred_symbol)
    interval = _source_interval(interval or inferred_interval)
    bars: list[Kline] = []
    for line_number, row in _zip_csv_rows(path):
        try:
            bars.append(
                Kline(
                    exchange="BINANCE",
                    symbol=symbol,
                    interval=interval,
                    open_time=_timestamp(row["open_time"]),
                    close_time=_timestamp(row["close_time"]),
                    open=_required_decimal(row, "open"),
                    high=_required_decimal(row, "high"),
                    low=_required_decimal(row, "low"),
                    close=_required_decimal(row, "close"),
                    volume=_required_decimal(row, "volume"),
                    quote_volume=_optional_decimal(row.get("quote_volume")),
                    is_closed=True,
                    taker_buy_volume=_optional_decimal(row.get("taker_buy_volume")),
                )
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ArchiveFormatError(f"{path.name}: invalid Kline row {line_number}: {exc}") from exc
    if not bars:
        raise ArchiveFormatError(f"{path.name}: Kline archive contains no data rows")
    return bars


def parse_funding_rate_zip(
    path: str | os.PathLike[str],
    *,
    symbol: str | None = None,
) -> list[FundingRateRow]:
    """Parse a Binance monthly funding-rate ZIP into structured rows."""
    path = Path(path)
    symbol = _symbol(symbol or _infer_funding_symbol(path.name))
    rows: list[FundingRateRow] = []
    for line_number, row in _zip_csv_rows(path):
        try:
            rows.append(
                FundingRateRow(
                    calc_time=_timestamp(row["calc_time"]),
                    funding_interval_hours=int(row["funding_interval_hours"]),
                    last_funding_rate=_required_decimal(row, "last_funding_rate"),
                    symbol=symbol,
                )
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ArchiveFormatError(f"{path.name}: invalid funding row {line_number}: {exc}") from exc
    if not rows:
        raise ArchiveFormatError(f"{path.name}: funding archive contains no data rows")
    return rows


def to_engine_funding_rates(rows: Iterable[FundingRateRow]) -> list[FundingRate]:
    """Convert parsed archive/REST rows to backtest-engine funding points."""
    return [row.to_engine() for row in rows]


def parse_metrics_zip(path: str | os.PathLike[str]) -> list[MetricsRow]:
    """Parse a Binance daily metrics ZIP into structured rows."""
    path = Path(path)
    rows: list[MetricsRow] = []
    for line_number, row in _zip_csv_rows(path):
        try:
            rows.append(
                MetricsRow(
                    create_time=_timestamp(row["create_time"]),
                    symbol=_symbol(row["symbol"]),
                    sum_open_interest=_required_decimal(row, "sum_open_interest"),
                    sum_open_interest_value=_required_decimal(row, "sum_open_interest_value"),
                    count_toptrader_long_short_ratio=_optional_decimal(
                        row.get("count_toptrader_long_short_ratio")
                    ),
                    sum_toptrader_long_short_ratio=_optional_decimal(
                        row.get("sum_toptrader_long_short_ratio")
                    ),
                    count_long_short_ratio=_optional_decimal(row.get("count_long_short_ratio")),
                    sum_taker_long_short_vol_ratio=_optional_decimal(
                        row.get("sum_taker_long_short_vol_ratio")
                    ),
                )
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ArchiveFormatError(f"{path.name}: invalid metrics row {line_number}: {exc}") from exc
    if not rows:
        raise ArchiveFormatError(f"{path.name}: metrics archive contains no data rows")
    return rows


def _parse_rest_funding_page(
    raw: bytes,
    symbol: str,
    start_ms: int,
    end_ms: int,
    source_name: str,
) -> list[FundingRateRow]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArchiveFormatError(f"{source_name}: invalid funding JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ArchiveFormatError(f"{source_name}: expected a JSON array from Binance fundingRate")
    rows: list[FundingRateRow] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ArchiveFormatError(f"{source_name}: funding item {index} is not an object")
        try:
            row_symbol = _symbol(str(item["symbol"]))
            funding_time_ms = int(item["fundingTime"])
            rate = Decimal(str(item["fundingRate"]))
            mark_price = _optional_decimal(
                str(item["markPrice"]) if item.get("markPrice") is not None else None
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ArchiveFormatError(f"{source_name}: invalid funding item {index}: {exc}") from exc
        if row_symbol != symbol:
            raise ArchiveFormatError(
                f"{source_name}: funding item {index} has symbol {row_symbol}, expected {symbol}"
            )
        if not start_ms <= funding_time_ms <= end_ms:
            raise ArchiveFormatError(
                f"{source_name}: funding item {index} timestamp {funding_time_ms} is outside "
                f"[{start_ms}, {end_ms}]"
            )
        rows.append(
            FundingRateRow(
                calc_time=_timestamp(str(funding_time_ms)),
                funding_interval_hours=None,
                last_funding_rate=rate,
                mark_price=mark_price,
                symbol=row_symbol,
            )
        )
    _validate_funding_rows(
        rows,
        _timestamp(str(start_ms)),
        _timestamp(str(end_ms)),
        symbol=symbol,
        source_name=source_name,
    )
    return rows


def _validate_funding_rows(
    rows: Sequence[FundingRateRow],
    start: datetime,
    end: datetime,
    *,
    symbol: str | None,
    source_name: str = "funding rates",
) -> None:
    previous: datetime | None = None
    for index, row in enumerate(rows):
        if not start <= row.calc_time <= end:
            raise ArchiveFormatError(
                f"{source_name}: row {index} timestamp {row.calc_time.isoformat()} is outside "
                f"[{start.isoformat()}, {end.isoformat()}]"
            )
        if symbol is not None and row.symbol != symbol:
            raise ArchiveFormatError(
                f"{source_name}: row {index} has symbol {row.symbol!r}, expected {symbol!r}"
            )
        if previous is not None and row.calc_time <= previous:
            raise ArchiveFormatError(
                f"{source_name}: funding timestamps must be strictly increasing without duplicates"
            )
        if not row.last_funding_rate.is_finite():
            raise ArchiveFormatError(f"{source_name}: row {index} has a non-finite funding rate")
        if row.mark_price is not None and (
            not row.mark_price.is_finite() or row.mark_price <= 0
        ):
            raise ArchiveFormatError(f"{source_name}: row {index} has an invalid mark price")
        previous = row.calc_time


def validate_klines(
    klines: Sequence[Kline],
    *,
    expected_interval: str = SUPPORTED_SOURCE_INTERVAL,
    strict: bool = False,
) -> KlineValidationReport:
    """Check duplicates, interval gaps, ordering, and OHLCV integrity."""
    duration = _interval_duration(expected_interval)
    open_times = [bar.open_time for bar in klines]
    counts: dict[datetime, int] = defaultdict(int)
    for open_time in open_times:
        counts[open_time] += 1
    duplicates = tuple(sorted(open_time for open_time, count in counts.items() if count > 1))
    unique_times = sorted(counts)
    gaps: list[KlineGap] = []
    for previous, current in zip(unique_times, unique_times[1:]):
        delta = current - previous
        if delta != duration:
            missing = max(0, math.ceil(delta / duration) - 1)
            gaps.append(KlineGap(previous, current, delta, missing))

    issues: list[KlineIssue] = []
    epoch = _epoch_for(open_times[0]) if open_times else datetime(1970, 1, 1)
    duration_seconds = int(duration.total_seconds())
    for index, bar in enumerate(klines):
        reasons = _kline_reasons(bar, expected_interval, duration, epoch, duration_seconds)
        issues.extend(KlineIssue(index, bar.open_time, reason) for reason in reasons)

    report = KlineValidationReport(
        duplicate_open_times=duplicates,
        gaps=tuple(gaps),
        invalid_ohlc=tuple(issues),
        out_of_order=any(current <= previous for previous, current in zip(open_times, open_times[1:])),
    )
    if strict and not report.is_valid:
        raise KlineValidationError(report)
    return report


def aggregate_klines(
    klines: Sequence[Kline],
    target_interval: Literal["15m", "4h"],
    *,
    strict: bool = True,
) -> list[Kline]:
    """Causally aggregate complete, UTC-aligned 5m bars into 15m or 4h bars.

    The function only emits a target bar after every constituent 5m candle is
    present. In non-strict mode, malformed or incomplete buckets are omitted.
    """
    target_duration = _interval_duration(target_interval)
    source_duration = _INTERVALS[SUPPORTED_SOURCE_INTERVAL]
    expected_count = int(target_duration / source_duration)
    source = sorted(klines, key=lambda bar: bar.open_time)
    report = validate_klines(source, expected_interval=SUPPORTED_SOURCE_INTERVAL, strict=False)
    if strict and not report.is_valid:
        raise KlineValidationError(report)

    buckets: dict[datetime, list[Kline]] = defaultdict(list)
    for bar in source:
        buckets[_bucket_start(bar.open_time, target_duration)].append(bar)

    aggregated: list[Kline] = []
    for bucket_start in sorted(buckets):
        bars = buckets[bucket_start]
        expected_opens = [bucket_start + index * source_duration for index in range(expected_count)]
        if len(bars) != expected_count or [bar.open_time for bar in bars] != expected_opens:
            continue
        if any(bar.interval != SUPPORTED_SOURCE_INTERVAL for bar in bars):
            continue
        quote_volume = (
            sum((bar.quote_volume for bar in bars if bar.quote_volume is not None), Decimal(0))
            if all(bar.quote_volume is not None for bar in bars)
            else None
        )
        taker_buy_volume = (
            sum((bar.taker_buy_volume for bar in bars if bar.taker_buy_volume is not None), Decimal(0))
            if all(bar.taker_buy_volume is not None for bar in bars)
            else None
        )
        aggregated.append(
            Kline(
                exchange=bars[0].exchange,
                symbol=bars[0].symbol,
                interval=target_interval,
                open_time=bucket_start,
                close_time=bars[-1].close_time,
                open=bars[0].open,
                high=max(bar.high for bar in bars),
                low=min(bar.low for bar in bars),
                close=bars[-1].close,
                volume=sum((bar.volume for bar in bars), Decimal(0)),
                quote_volume=quote_volume,
                is_closed=all(bar.is_closed for bar in bars),
                taker_buy_volume=taker_buy_volume,
            )
        )
    return aggregated


aggregate_5m_klines = aggregate_klines


def _zip_csv_rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    try:
        with ZipFile(path) as archive:
            csv_members = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
            if not csv_members:
                raise ArchiveFormatError(f"{path.name}: ZIP contains no CSV member")
            for member in csv_members:
                with archive.open(member) as binary:
                    yield from _csv_rows(binary, path.name)
    except (BadZipFile, OSError) as exc:
        raise ArchiveFormatError(f"{path.name}: invalid ZIP archive: {exc}") from exc


def _csv_rows(binary: BinaryIO, archive_name: str) -> Iterator[tuple[int, dict[str, str]]]:
    with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
        reader = csv.reader(text)
        try:
            first = next(reader)
        except StopIteration:
            return
        if not first:
            raise ArchiveFormatError(f"{archive_name}: CSV starts with an empty row")
        normalized = [_normalize_header(value) for value in first]
        if _looks_like_header(normalized):
            headers = normalized
            start_line = 2
        else:
            headers = _legacy_headers(len(first))
            start_line = 1
            yield start_line, _row_dict(headers, first, archive_name, start_line)
            start_line += 1
        for line_number, values in enumerate(reader, start=start_line):
            if not values or not any(value.strip() for value in values):
                continue
            yield line_number, _row_dict(headers, values, archive_name, line_number)


def _legacy_headers(column_count: int) -> list[str]:
    if column_count >= 12:
        return [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "count",
            "taker_buy_volume",
            "taker_buy_quote_volume",
            "ignore",
        ]
    if column_count == 3:
        return ["calc_time", "funding_interval_hours", "last_funding_rate"]
    raise ArchiveFormatError(f"unrecognized headerless Binance CSV with {column_count} columns")


def _row_dict(headers: list[str], values: list[str], archive_name: str, line_number: int) -> dict[str, str]:
    if len(values) != len(headers):
        raise ArchiveFormatError(
            f"{archive_name}: row {line_number} has {len(values)} columns; expected {len(headers)}"
        )
    return dict(zip(headers, (value.strip() for value in values)))


def _looks_like_header(values: list[str]) -> bool:
    known = {"open_time", "calc_time", "create_time"}
    return bool(known.intersection(values))


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _required_decimal(row: dict[str, str], key: str) -> Decimal:
    value = row[key]
    if not value:
        raise ValueError(f"{key} is empty")
    return Decimal(value)


def _optional_decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip() or value.strip().lower() in {"nan", "null", "none"}:
        return None
    return Decimal(value)


def _timestamp(value: str) -> datetime:
    value = value.strip()
    try:
        numeric = Decimal(value)
    except InvalidOperation:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _naive_utc(parsed)
    magnitude = abs(numeric)
    divisor = Decimal(1)
    if magnitude >= Decimal("1e17"):
        divisor = Decimal(1_000_000_000)
    elif magnitude >= Decimal("1e14"):
        divisor = Decimal(1_000_000)
    elif magnitude >= Decimal("1e11"):
        divisor = Decimal(1_000)
    return datetime.fromtimestamp(float(numeric / divisor), tz=UTC).replace(tzinfo=None)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _kline_reasons(
    bar: Kline,
    expected_interval: str,
    duration: timedelta,
    epoch: datetime,
    duration_seconds: int,
) -> list[str]:
    reasons: list[str] = []
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(not value.is_finite() or value <= 0 for value in prices):
        reasons.append("OHLC prices must be finite and positive")
    elif bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.low > bar.high:
        reasons.append("high/low does not contain open and close")
    if not bar.volume.is_finite() or bar.volume < 0:
        reasons.append("volume must be finite and non-negative")
    if bar.quote_volume is not None and (not bar.quote_volume.is_finite() or bar.quote_volume < 0):
        reasons.append("quote_volume must be finite and non-negative")
    if bar.taker_buy_volume is not None and (
        not bar.taker_buy_volume.is_finite()
        or bar.taker_buy_volume < 0
        or bar.taker_buy_volume > bar.volume
    ):
        reasons.append("taker_buy_volume must be between zero and volume")
    if bar.interval != expected_interval:
        reasons.append(f"interval is {bar.interval!r}, expected {expected_interval!r}")
    if bar.close_time <= bar.open_time or bar.close_time > bar.open_time + duration:
        reasons.append("close_time lies outside the candle interval")
    seconds_from_epoch = int((bar.open_time - epoch).total_seconds())
    if seconds_from_epoch % duration_seconds:
        reasons.append("open_time is not aligned to the expected interval")
    return reasons


def _bucket_start(value: datetime, duration: timedelta) -> datetime:
    epoch = _epoch_for(value)
    seconds = int((value - epoch).total_seconds())
    bucket_seconds = int(duration.total_seconds())
    return epoch + timedelta(seconds=seconds - seconds % bucket_seconds)


def _epoch_for(value: datetime) -> datetime:
    return datetime(1970, 1, 1, tzinfo=value.tzinfo)


def _interval_duration(interval: str) -> timedelta:
    try:
        return _INTERVALS[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval!r}") from exc


def _source_interval(interval: str) -> str:
    if interval != SUPPORTED_SOURCE_INTERVAL:
        raise ValueError(f"only Binance {SUPPORTED_SOURCE_INTERVAL} source archives are supported")
    return interval


def _symbol(value: str) -> str:
    value = value.strip().upper()
    if not value or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"invalid Binance symbol: {value!r}")
    return value


def _infer_kline_identity(filename: str) -> tuple[str, str]:
    match = re.match(r"^(?P<symbol>[A-Za-z0-9_-]+)-(?P<interval>\d+[mhdwM])-", filename)
    if not match:
        raise ValueError("symbol and interval are required when the ZIP filename is non-standard")
    return match.group("symbol"), match.group("interval")


def _infer_funding_symbol(filename: str) -> str:
    match = re.match(r"^(?P<symbol>[A-Za-z0-9_-]+)-fundingRate-", filename)
    if not match:
        raise ValueError("symbol is required when the funding ZIP filename is non-standard")
    return match.group("symbol")


def _datetime_to_milliseconds(value: datetime) -> int:
    value = _naive_utc(value)
    delta = value - datetime(1970, 1, 1)
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _month_label(year: int, month: int) -> str:
    try:
        return date(year, month, 1).strftime("%Y-%m")
    except ValueError as exc:
        raise ValueError(f"invalid year/month: {year}-{month}") from exc


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _date_range(start: date, end: date) -> tuple[date, date]:
    start = _as_date(start)
    end = _as_date(end)
    if start > end:
        raise ValueError("start must be on or before end")
    return start, end


def _month_starts(start: date, end: date) -> Iterator[date]:
    current = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)
    while current <= final:
        yield current
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)


def _last_day_of_month(month_start: date) -> date:
    next_month = date(
        month_start.year + (month_start.month == 12),
        month_start.month % 12 + 1,
        1,
    )
    return next_month - timedelta(days=1)


def _days(start: date, end: date) -> Iterator[date]:
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def _safe_relative_path(value: PurePosixPath | str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive path: {value!r}")
    if any(not _SAFE_COMPONENT.fullmatch(part.replace(".", "_")) for part in path.parts):
        raise ValueError(f"unsafe archive path: {value!r}")
    return path


def _checksum_digest(text: str) -> str:
    tokens = text.strip().split()
    if not tokens or not _SHA256.fullmatch(tokens[0]):
        raise ArchiveFormatError("invalid Binance .CHECKSUM content")
    return tokens[0].lower()


def _verify_checksum(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise ChecksumMismatchError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def _temporary_path(target: Path) -> Path:
    descriptor, value = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
    os.close(descriptor)
    return Path(value)


def _atomic_write_text(target: Path, value: str) -> None:
    temporary = _temporary_path(target)
    try:
        temporary.write_text(value, encoding="ascii")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "ArchiveFormatError",
    "BINANCE_FUTURES_REST_BASE_URL",
    "BINANCE_VISION_BASE_URL",
    "BinanceArchiveClient",
    "BinanceArchiveError",
    "ChecksumMismatchError",
    "FundingRateRecord",
    "FundingRateRow",
    "KlineGap",
    "KlineIssue",
    "KlineValidationError",
    "KlineValidationReport",
    "MetricsRecord",
    "MetricsRow",
    "RestFundingUnavailableError",
    "aggregate_5m_klines",
    "aggregate_klines",
    "parse_funding_rate_zip",
    "parse_kline_zip",
    "parse_metrics_zip",
    "to_engine_funding_rates",
    "validate_klines",
]
