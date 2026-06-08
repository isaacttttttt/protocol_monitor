import argparse
import asyncio
from datetime import datetime

from loguru import logger

from app.config.loader import load_strategy_configs, load_symbols_config, load_system_config
from app.config.settings import get_settings
from app.connectors.binance_futures import BinanceFuturesConnector
from app.market.data_bus import MarketDataBus
from app.market.kline_store import KlineStore
from app.review.reporter import PeriodicReporter
from app.signals.router import SignalRouter
from app.storage.db import create_engine, create_session_factory, init_db
from app.storage.repositories import IndicatorArchiveRepository, IndicatorRepository, KlineRepository, SignalRepository
from app.strategies.base import StrategyContext
from app.strategies.eth_cm2_pullback_fail_short import EthCm2PullbackFailShort
from app.strategies.eth_cm3_liquidity_sweep_long import EthCm3LiquiditySweepLong
from app.strategies.eth_stand_above_level import EthStandAboveLevel
from app.strategies.state_machine import StrategyStateEnum
from app.strategies.base import StrategyState
from app.indicators.atr import calculate_atr
from app.indicators.cvd import calculate_cvd_proxy
from app.indicators.macd import calculate_macd
from app.indicators.structure import detect_structure
from app.indicators.vwap import calculate_vwap


STRATEGY_TYPES = {
    "ETH_C_M2_PULLBACK_FAIL_SHORT_V1": EthCm2PullbackFailShort,
    "ETH_STAND_ABOVE_1605_V1": EthStandAboveLevel,
    "ETH_C_M3_LIQUIDITY_SWEEP_LONG_V1": EthCm3LiquiditySweepLong,
}


async def run_monitor(run_once: bool = False) -> None:
    settings = get_settings()
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level=settings.log_level)
    system_config = load_system_config()
    symbols = load_symbols_config()
    strategy_configs = load_strategy_configs()
    engine = create_engine(settings)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    store = KlineStore(KlineRepository(session_factory), cache_limit=int(system_config.get("storage", {}).get("kline_cache_limit", 1000)))
    await store.load_from_db(symbols)
    signal_repo = SignalRepository(session_factory)
    indicator_repo = IndicatorRepository(session_factory)
    router = SignalRouter(settings, signal_repo, system_config)
    strategies = [STRATEGY_TYPES[cfg["id"]](cfg) for cfg in strategy_configs if cfg["id"] in STRATEGY_TYPES]
    persisted_states = await signal_repo.get_strategy_states()
    for strategy in strategies:
        record = persisted_states.get(strategy.id)
        if record:
            strategy.state = StrategyState(
                state=StrategyStateEnum(record["state"]),
                context=record.get("context") or {},
                entered_state_at=record["entered_state_at"] or datetime.utcnow(),
            )
    bus = MarketDataBus()
    connector = BinanceFuturesConnector(settings.binance_ws_base, symbols, bus)

    async def consume() -> None:
        async for event in bus.subscribe():
            await store.upsert_kline(event)
            if not event.is_closed:
                continue
            candles = store.get_recent(event.exchange, event.symbol, event.interval, 100)
            macd = calculate_macd(candles)
            cvd = calculate_cvd_proxy(candles)
            structure = detect_structure(candles)
            await indicator_repo.save_snapshot(
                {
                    "exchange": event.exchange,
                    "symbol": event.symbol,
                    "interval": event.interval,
                    "ts": event.close_time,
                    "atr": calculate_atr(candles),
                    "macd": macd.macd,
                    "macd_signal": macd.signal,
                    "macd_hist": macd.histogram,
                    "vwap": calculate_vwap(candles),
                    "cvd": cvd.cvd,
                    "delta": cvd.delta,
                    "structure_state": {
                        "trend": structure.trend,
                        "bos_up": structure.bos_up,
                        "bos_down": structure.bos_down,
                        "choch_up": structure.choch_up,
                        "choch_down": structure.choch_down,
                        "last_swing_high": structure.last_swing_high,
                        "last_swing_low": structure.last_swing_low,
                    },
                }
            )
            context = StrategyContext(event=event, store=store, now=datetime.utcnow())
            for strategy in strategies:
                for signal in await strategy.on_market_update(context):
                    await router.route(signal, strategy.id)
                await signal_repo.save_strategy_state(strategy.state_record())
            if run_once:
                break

    if run_once:
        logger.info("database initialized; run-once exits before websocket subscription")
        return
    await asyncio.gather(connector.run(), consume())


async def run_report(hours: int | None = None, send: bool = False) -> None:
    settings = get_settings()
    logger.remove()
    logger.add(lambda msg: print(msg, end=""), level=settings.log_level)
    system_config = load_system_config()
    configured_hours = int(system_config.get("automation", {}).get("report_interval_hours", 4))
    report_hours = hours or configured_hours
    engine = create_engine(settings)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    kline_repo = KlineRepository(session_factory)
    signal_repo = SignalRepository(session_factory)
    archive_repo = IndicatorArchiveRepository(session_factory)
    reporter = PeriodicReporter(settings, system_config, kline_repo, signal_repo, archive_repo)
    try:
        if send:
            await reporter.send(report_hours)
            return
        title, body = await reporter.build(report_hours)
        print(f"{title}\n\n{body}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartMoney Protocol Monitor")
    parser.add_argument("command", nargs="?", choices=["monitor", "report"], default="monitor")
    parser.add_argument("--run-once", action="store_true", help="Initialize config/database and exit.")
    parser.add_argument("--hours", type=int, default=None, help="Report lookback window in hours.")
    parser.add_argument("--send", action="store_true", help="Send report through enabled notification channels.")
    args = parser.parse_args()
    if args.command == "report":
        asyncio.run(run_report(hours=args.hours, send=args.send))
    else:
        asyncio.run(run_monitor(run_once=args.run_once))


if __name__ == "__main__":
    main()
