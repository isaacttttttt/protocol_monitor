from app.risk.portfolio import PortfolioRiskConfig, PortfolioRiskPolicy, PortfolioRiskState
from app.signals.models import Signal, SignalLevel
from app.storage.repositories import _paper_result_r


def _signal(symbol="ETHUSDT", position_r=0.5):
    return Signal(
        signal_id="s1",
        exchange="BINANCE",
        symbol=symbol,
        book="Micro",
        strategy_name="test",
        level=SignalLevel.L3,
        direction="LONG",
        status="TRIGGERED",
        trigger_price=100,
        entry=100,
        sl=90,
        tp1=115,
        tp2=125,
        tp3=140,
        rr_to_tp1=1.5,
        position_r=position_r,
        trigger_reason="test",
        invalid_condition="stop",
    )


def test_portfolio_risk_caps_correlated_crypto_exposure():
    policy = PortfolioRiskPolicy(PortfolioRiskConfig(max_position_r=0.5, max_cluster_r=0.75))

    decision = policy.assess(_signal(), [{"symbol": "BTCUSDT", "position_r": 0.5}])

    assert decision.allowed_position_r == 0.25
    assert "cluster_cap:crypto_beta" in decision.reasons


def test_portfolio_risk_blocks_at_drawdown_stop():
    decision = PortfolioRiskPolicy().assess(
        _signal(),
        [],
        PortfolioRiskState(current_drawdown_pct=10),
    )

    assert decision.blocked is True
    assert decision.allowed_position_r == 0


def test_paper_result_r_uses_tp2_distance_for_winner():
    result = _paper_result_r(
        {"result": "WIN_TP2", "direction": "LONG", "entry": 100, "sl": 90, "tp2": 125}
    )

    assert result == 2.5
