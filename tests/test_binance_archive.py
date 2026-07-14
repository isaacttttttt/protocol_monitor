from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.backtest.binance_archive import (
    ArchiveFormatError,
    BinanceArchiveClient,
    ChecksumMismatchError,
    KlineValidationError,
    RestFundingUnavailableError,
    aggregate_klines,
    parse_funding_rate_zip,
    parse_kline_zip,
    parse_metrics_zip,
    to_engine_funding_rates,
    validate_klines,
)
from app.market.models import Kline


def _zip_payload(member: str, csv_text: str) -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(member, csv_text)
    return output.getvalue()


def _write_zip(path: Path, member: str, csv_text: str) -> Path:
    path.write_bytes(_zip_payload(member, csv_text))
    return path


def _bar(minutes: int, *, invalid: bool = False) -> Kline:
    opened = datetime(2025, 7, 1) + timedelta(minutes=minutes)
    open_price = Decimal(100 + minutes)
    return Kline(
        exchange="BINANCE",
        symbol="ETHUSDT",
        interval="5m",
        open_time=opened,
        close_time=opened + timedelta(minutes=5) - timedelta(milliseconds=1),
        open=open_price,
        high=open_price - 1 if invalid else open_price + 2,
        low=open_price - 2,
        close=open_price + 1,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        is_closed=True,
        taker_buy_volume=Decimal("6"),
    )


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def test_download_caches_archive_and_verifies_published_checksum(tmp_path: Path) -> None:
    payload = b"zip payload"
    digest = hashlib.sha256(payload).hexdigest()
    archive_url = (
        "https://example.test/monthly/klines/ETHUSDT/5m/"
        "ETHUSDT-5m-2025-07.zip"
    )
    calls: list[str] = []

    def opener(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        assert timeout == 5
        if url.endswith(".CHECKSUM"):
            return _FakeResponse(f"{digest}  ETHUSDT-5m-2025-07.zip\n".encode())
        assert url == archive_url
        return _FakeResponse(payload)

    client = BinanceArchiveClient(
        tmp_path,
        base_url="https://example.test",
        timeout_seconds=5,
        opener=opener,
    )

    first = client.download_monthly_klines("ethusdt", 2025, 7)
    second = client.download_monthly_klines("ETHUSDT", 2025, 7)

    assert first == second
    assert first.read_bytes() == payload
    assert Path(f"{first}.CHECKSUM").exists()
    assert calls == [f"{archive_url}.CHECKSUM", archive_url]


def test_download_all_archive_layouts_when_checksum_is_unavailable(tmp_path: Path) -> None:
    requested: list[str] = []

    def opener(url: str, *, timeout: float) -> _FakeResponse:
        requested.append(url)
        if url.endswith(".CHECKSUM"):
            raise HTTPError(url, 404, "not found", None, None)
        return _FakeResponse(b"archive")

    client = BinanceArchiveClient(tmp_path, base_url="https://example.test", opener=opener)

    paths = [
        client.download_monthly_klines("ETHUSDT", 2025, 7),
        client.download_daily_klines("ETHUSDT", date(2025, 7, 1)),
        client.download_monthly_funding_rates("ETHUSDT", 2025, 7),
        client.download_daily_metrics("ETHUSDT", date(2025, 7, 1)),
    ]

    assert [path.name for path in paths] == [
        "ETHUSDT-5m-2025-07.zip",
        "ETHUSDT-5m-2025-07-01.zip",
        "ETHUSDT-fundingRate-2025-07.zip",
        "ETHUSDT-metrics-2025-07-01.zip",
    ]
    assert all(path.read_bytes() == b"archive" for path in paths)
    assert any("/monthly/fundingRate/ETHUSDT/" in url for url in requested)
    assert any("/daily/metrics/ETHUSDT/" in url for url in requested)


def test_download_rejects_checksum_mismatch_without_caching_file(tmp_path: Path) -> None:
    def opener(url: str, *, timeout: float) -> _FakeResponse:
        if url.endswith(".CHECKSUM"):
            return _FakeResponse(("0" * 64 + "  file.zip\n").encode())
        return _FakeResponse(b"corrupt")

    client = BinanceArchiveClient(tmp_path, base_url="https://example.test", opener=opener)

    with pytest.raises(ChecksumMismatchError):
        client.download_daily_metrics("ETHUSDT", date(2025, 7, 1))

    assert not list(tmp_path.rglob("*.zip"))
    assert not list(tmp_path.rglob("*.part"))


def test_parse_kline_zip_preserves_decimal_volume_fields(tmp_path: Path) -> None:
    csv_text = "\n".join(
        [
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore",
            "1751328000000,2484.30,2488.56,2483.65,2486.15,10013.348,1751328299999,24895119.50270,9905,5219.618,12977338.98729,0",
            "1751328300000,2486.15,2492.90,2483.50,2492.89,9195.262,1751328599999,22878638.05295,9846,5341.286,13292518.99505,0",
        ]
    )
    archive = _write_zip(
        tmp_path / "ETHUSDT-5m-2025-07-01.zip",
        "ETHUSDT-5m-2025-07-01.csv",
        csv_text,
    )

    bars = parse_kline_zip(archive)

    assert len(bars) == 2
    assert bars[0].open_time == datetime(2025, 7, 1)
    assert bars[0].open == Decimal("2484.30")
    assert bars[0].quote_volume == Decimal("24895119.50270")
    assert bars[0].taker_buy_volume == Decimal("5219.618")
    assert validate_klines(bars).is_valid


def test_parse_headerless_kline_csv_and_reject_malformed_rows(tmp_path: Path) -> None:
    valid = _write_zip(
        tmp_path / "ETHUSDT-5m-2025-07-01.zip",
        "data.csv",
        "1751328000000,1,2,0.5,1.5,10,1751328299999,15,4,6,9,0\n",
    )
    invalid = _write_zip(
        tmp_path / "ETHUSDT-5m-2025-07-02.zip",
        "data.csv",
        "open_time,open,high\n1,2\n",
    )

    assert parse_kline_zip(valid)[0].close == Decimal("1.5")
    with pytest.raises(ArchiveFormatError, match="columns"):
        parse_kline_zip(invalid)


def test_parse_funding_and_metrics_archives(tmp_path: Path) -> None:
    funding = _write_zip(
        tmp_path / "ETHUSDT-fundingRate-2025-07.zip",
        "funding.csv",
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "1751328000007,8,0.00008191\n",
    )
    metrics = _write_zip(
        tmp_path / "ETHUSDT-metrics-2025-07-01.zip",
        "metrics.csv",
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2025-07-01 00:05:00,ETHUSDT,1893129.089,4706583953.32646,"
        "1.91519612,3.27485900,1.46745437,1.08735800\n",
    )

    funding_row = parse_funding_rate_zip(funding)[0]
    metrics_row = parse_metrics_zip(metrics)[0]

    assert funding_row.calc_time == datetime(2025, 7, 1, 0, 0, 0, 7000)
    assert funding_row.last_funding_rate == Decimal("0.00008191")
    assert funding_row.rate == float(funding_row.last_funding_rate)
    assert funding_row.symbol == "ETHUSDT"
    assert metrics_row.create_time == datetime(2025, 7, 1, 0, 5)
    assert metrics_row.sum_open_interest == Decimal("1893129.089")
    assert metrics_row.sum_taker_long_short_vol_ratio == Decimal("1.08735800")


def test_rest_funding_history_caches_raw_response_and_converts_for_engine(tmp_path: Path) -> None:
    first_ms = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)
    second_ms = int(datetime(2026, 7, 1, 8, tzinfo=UTC).timestamp() * 1000)
    payload = json.dumps(
        [
            {
                "symbol": "ETHUSDT",
                "fundingTime": first_ms,
                "fundingRate": "0.00001000",
                "markPrice": "2500.50",
            },
            {
                "symbol": "ETHUSDT",
                "fundingTime": second_ms,
                "fundingRate": "-0.00002000",
                "markPrice": "2520.25",
            },
        ],
        separators=(",", ":"),
    ).encode()
    calls: list[str] = []

    def opener(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        assert url.startswith("https://fapi.example.test/fundingRate?")
        assert "symbol=ETHUSDT" in url
        assert "limit=1000" in url
        return _FakeResponse(payload)

    client = BinanceArchiveClient(
        tmp_path,
        base_url="https://archive.example.test",
        rest_base_url="https://fapi.example.test",
        opener=opener,
    )
    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 2)

    first = client.fetch_rest_funding_rates("ETHUSDT", start, end)
    second = client.fetch_rest_funding_rates("ETHUSDT", start, end)
    engine_rows = to_engine_funding_rates(first)

    assert first == second
    assert len(calls) == 1
    assert first[0].funding_interval_hours is None
    assert first[0].mark_price == Decimal("2500.50")
    assert engine_rows[0].time == first[0].calc_time
    assert engine_rows[0].rate == pytest.approx(0.00001)
    assert engine_rows[0].mark_price == pytest.approx(2500.50)
    cached = list((tmp_path / "rest" / "fundingRate" / "ETHUSDT").glob("*.json"))
    assert len(cached) == 1
    assert cached[0].read_bytes() == payload


def test_rest_funding_history_rejects_out_of_range_response_before_caching(tmp_path: Path) -> None:
    out_of_range_ms = int(datetime(2026, 7, 3, tzinfo=UTC).timestamp() * 1000)
    payload = json.dumps(
        [
            {
                "symbol": "ETHUSDT",
                "fundingTime": out_of_range_ms,
                "fundingRate": "0.00001",
                "markPrice": "2500",
            }
        ]
    ).encode()

    def opener(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(payload)

    client = BinanceArchiveClient(
        tmp_path,
        rest_base_url="https://fapi.example.test",
        opener=opener,
    )

    with pytest.raises(ArchiveFormatError, match="outside"):
        client.fetch_rest_funding_rates(
            "ETHUSDT",
            datetime(2026, 7, 1),
            datetime(2026, 7, 2),
        )

    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.part"))


def test_rest_funding_network_failure_is_identifiable_and_not_cached(tmp_path: Path) -> None:
    def opener(url: str, *, timeout: float) -> _FakeResponse:
        raise TimeoutError("endpoint timed out")

    client = BinanceArchiveClient(
        tmp_path,
        rest_base_url="https://fapi.example.test",
        opener=opener,
    )

    with pytest.raises(RestFundingUnavailableError, match="ETHUSDT"):
        client.fetch_rest_funding_rates(
            "ETHUSDT",
            datetime(2026, 7, 1),
            datetime(2026, 7, 2),
        )

    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.part"))


def test_unpublished_monthly_funding_archive_falls_back_to_rest(tmp_path: Path) -> None:
    funding_ms = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000)
    payload = json.dumps(
        [
            {
                "symbol": "ETHUSDT",
                "fundingTime": funding_ms,
                "fundingRate": "0.00001",
                "markPrice": "2500",
            }
        ]
    ).encode()
    calls: list[str] = []

    def opener(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        if url.startswith("https://archive.example.test/"):
            raise HTTPError(url, 404, "not published", None, None)
        return _FakeResponse(payload)

    client = BinanceArchiveClient(
        tmp_path,
        base_url="https://archive.example.test",
        rest_base_url="https://fapi.example.test",
        opener=opener,
    )

    rows = client.load_funding_rate_range(
        "ETHUSDT",
        date(2026, 7, 1),
        date(2026, 7, 14),
    )

    assert [row.calc_time for row in rows] == [datetime(2026, 7, 1)]
    assert any("/monthly/fundingRate/ETHUSDT/" in url for url in calls)
    assert any("/fundingRate?" in url for url in calls)


def test_validation_detects_duplicates_gaps_ordering_and_bad_ohlc() -> None:
    bars = [_bar(0), _bar(0), _bar(10), _bar(15, invalid=True)]

    report = validate_klines(bars)

    assert report.duplicate_open_times == (datetime(2025, 7, 1),)
    assert len(report.gaps) == 1
    assert report.gaps[0].missing_bars == 1
    assert report.out_of_order
    assert any("high/low" in issue.reason for issue in report.invalid_ohlc)
    with pytest.raises(KlineValidationError) as captured:
        validate_klines(bars, strict=True)
    assert captured.value.report == report


@pytest.mark.parametrize(("target_interval", "source_count"), [("15m", 3), ("4h", 48)])
def test_causal_aggregation_uses_only_complete_aligned_buckets(
    target_interval: str,
    source_count: int,
) -> None:
    bars = [_bar(index * 5) for index in range(source_count)]
    bars.append(_bar(source_count * 5))

    aggregated = aggregate_klines(bars, target_interval, strict=False)

    assert len(aggregated) == 1
    candle = aggregated[0]
    assert candle.interval == target_interval
    assert candle.open == bars[0].open
    assert candle.close == bars[source_count - 1].close
    assert candle.high == max(bar.high for bar in bars[:source_count])
    assert candle.low == min(bar.low for bar in bars[:source_count])
    assert candle.volume == Decimal(10 * source_count)
    assert candle.taker_buy_volume == Decimal(6 * source_count)
    assert candle.close_time == bars[source_count - 1].close_time


def test_strict_aggregation_rejects_gap_in_source_data() -> None:
    bars = [_bar(0), _bar(10), _bar(15)]

    with pytest.raises(KlineValidationError):
        aggregate_klines(bars, "15m")
