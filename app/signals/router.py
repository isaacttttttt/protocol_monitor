from dataclasses import asdict
from datetime import datetime

from loguru import logger

from app.config.settings import Settings
from app.notifications.base import NotificationMessage
from app.notifications.feishu import FeishuNotifier
from app.notifications.telegram import TelegramNotifier
from app.risk.cooldown import SignalCooldown
from app.risk.portfolio import PortfolioRiskConfig, PortfolioRiskPolicy, PortfolioRiskState
from app.signals.models import Signal, SignalLevel
from app.signals.templates import render_signal
from app.storage.repositories import SignalRepository


class SignalRouter:
    def __init__(self, settings: Settings, repository: SignalRepository, notification_config: dict) -> None:
        self.settings = settings
        self.repository = repository
        self.notification_config = notification_config
        self.cooldown = SignalCooldown()
        risk_config = notification_config.get("risk", {})
        self.portfolio_risk = PortfolioRiskPolicy(
            PortfolioRiskConfig(
                max_position_r=float(risk_config.get("max_micro_position_r", 0.5)),
                max_cluster_r=float(risk_config.get("max_cluster_position_r", 0.75)),
                max_daily_loss_r=float(risk_config.get("max_daily_loss_r", 1.0)),
                drawdown_throttle_start_pct=float(risk_config.get("drawdown_throttle_start_pct", 5.0)),
                drawdown_stop_pct=float(risk_config.get("drawdown_stop_pct", 10.0)),
                consecutive_loss_throttle=int(risk_config.get("consecutive_loss_throttle", 3)),
            )
        )
        self.risk_per_1r_pct = float(risk_config.get("paper_risk_per_1r_pct", 1.0))
        self.notifiers = {
            "telegram": TelegramNotifier(settings),
            "feishu": FeishuNotifier(settings),
        }

    async def route(self, signal: Signal, strategy_id: str) -> None:
        if signal.level == SignalLevel.L3 and signal.position_r > 0:
            risk_state = PortfolioRiskState(
                **await self.repository.get_portfolio_risk_state(risk_per_1r_pct=self.risk_per_1r_pct)
            )
            decision = self.portfolio_risk.assess(
                signal,
                await self.repository.get_manageable_signals(),
                risk_state,
            )
            signal.position_r = decision.allowed_position_r
            if decision.reasons:
                signal.risk_flags["portfolio_risk"] = list(decision.reasons)
            if decision.blocked:
                signal.level = SignalLevel.L2
                signal.status = "WATCHING"
                signal.risk_flags["portfolio_blocked"] = True
        await self.repository.save_signal(signal)
        ordinary = int(self.notification_config.get("risk", {}).get("duplicate_signal_cooldown_minutes", 30))
        l4 = int(self.notification_config.get("risk", {}).get("l4_duplicate_cooldown_minutes", 10))
        minutes = l4 if signal.level == SignalLevel.L4 else ordinary
        if not self.cooldown.allowed(signal.symbol, strategy_id, signal.level.value, signal.trigger_reason, datetime.utcnow(), minutes):
            logger.info("signal skipped by cooldown: {}", signal.signal_id)
            return
        notif_cfg = self.notification_config.get("notification", {})
        if not notif_cfg.get("enabled", True) or signal.level.value not in set(notif_cfg.get("levels", [])):
            return
        title, body = render_signal(signal)
        for channel, config in notif_cfg.get("channels", {}).items():
            if not config.get("enabled", False):
                continue
            message = NotificationMessage(channel, title, body, signal.level.value, signal.signal_id, signal.symbol, signal.created_at)
            result = await self.notifiers[channel].send(message)
            await self.repository.save_notification(
                {
                    "signal_id": signal.signal_id,
                    "channel": channel,
                    "target": channel,
                    "title": title,
                    "body": body,
                    "status": "SENT" if result.ok else "FAILED",
                    "error_message": result.error,
                    "sent_at": datetime.utcnow() if result.ok else None,
                }
            )
            if not result.ok:
                logger.warning("{} notification failed: {}", channel, result.error)
