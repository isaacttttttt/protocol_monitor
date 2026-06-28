from app.review.equity_sectors import (
    load_equity_sector_map,
    required_equity_context_symbols,
)


def test_soxl_and_mu_have_semiconductor_sector_context():
    sectors = load_equity_sector_map()

    assert sectors["SOXL"]["asset_type"] == "leveraged_etf"
    assert sectors["SOXL"]["sector"] == "Semiconductors"
    assert sectors["SOXL"]["primary_benchmark"] == "SOXX"
    assert sectors["SOXL"]["secondary_benchmarks"] == ["SMH"]
    assert sectors["SOXL"]["leverage_multiple"] == 3

    assert sectors["MU"]["sector"] == "Semiconductors"
    assert sectors["MU"]["industry"] == "Memory"
    assert "WDC" in sectors["MU"]["peers"]


def test_required_equity_context_includes_sector_benchmarks_and_peers():
    symbols = required_equity_context_symbols(
        target_symbols=["SOXL", "MU"],
        base_symbols=["SPY", "QQQ", "SMH"],
    )

    assert symbols[:3] == ["SPY", "QQQ", "SMH"]
    assert "SOXX" in symbols
    assert "WDC" in symbols
    assert len(symbols) == len(set(symbols))

