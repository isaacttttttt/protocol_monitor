from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.signals.models import Signal


@dataclass(frozen=True)
class PortfolioRiskConfig:
    """Portfolio-level limits expressed in R units."""

    max_position_r: float = 0.5
    max_cluster_r: float = 0.75
    max_daily_loss_r: float = 1.0
    drawdown_throttle_start_pct: float = 5.0
    drawdown_stop_pct: float = 10.0
    consecutive_loss_throttle: int = 3


@dataclass(frozen=True)
class PortfolioRiskState:
    daily_realized_r: float = 0.0
    current_drawdown_pct: float = 0.0
    consecutive_losses: int = 0


@dataclass(frozen=True)
class RiskDecision:
    allowed_position_r: float
    blocked: bool
    reasons: tuple[str, ...]


class PortfolioRiskPolicy:
    """Cap correlated exposure and throttle size during adverse runs."""

    DEFAULT_CLUSTERS = {
        "crypto_beta": {"BTCUSDT", "ETHUSDT"},
        "semiconductors": {"SOXL", "SOXX", "SMH", "MU", "ARM", "NVDA", "AMD"},
        "digital_assets": {"BTCUSDT", "ETHUSDT", "CRCL", "COIN", "HOOD"},
    }

    def __init__(
        self,
        config: PortfolioRiskConfig | None = None,
        clusters: dict[str, set[str]] | None = None,
    ) -> None:
        self.config = config or PortfolioRiskConfig()
        self.clusters = clusters or self.DEFAULT_CLUSTERS

    def assess(
        self,
        signal: Signal,
        active_signals: list[dict[str, Any]],
        state: PortfolioRiskState | None = None,
    ) -> RiskDecision:
        state = state or PortfolioRiskState()
        reasons: list[str] = []
        requested = min(max(signal.position_r, 0.0), self.config.max_position_r)
        if state.daily_realized_r <= -self.config.max_daily_loss_r:
            return RiskDecision(0.0, True, ("daily_loss_limit",))
        if state.current_drawdown_pct >= self.config.drawdown_stop_pct:
            return RiskDecision(0.0, True, ("drawdown_stop",))
        if state.current_drawdown_pct >= self.config.drawdown_throttle_start_pct:
            requested *= 0.5
            reasons.append("drawdown_throttle")
        if state.consecutive_losses >= self.config.consecutive_loss_throttle:
            requested *= 0.5
            reasons.append("consecutive_loss_throttle")

        for cluster in sorted(self._clusters_for(signal.symbol)):
            active_r = sum(
                float(item.get("position_r") or 0.0)
                for item in active_signals
                if cluster in self._clusters_for(str(item.get("symbol") or ""))
            )
            available = max(0.0, self.config.max_cluster_r - active_r)
            if available <= requested and active_r > 0:
                requested = min(requested, available)
                reasons.append(f"cluster_cap:{cluster}")
        return RiskDecision(requested, requested <= 0.0, tuple(reasons))

    def _clusters_for(self, symbol: str) -> set[str]:
        normalized = symbol.upper()
        return {name for name, members in self.clusters.items() if normalized in members}
