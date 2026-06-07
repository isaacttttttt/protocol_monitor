---
name: smartmoney-protocol-monitor
description: Operate and modify the local SmartMoney Protocol Monitor Python project. Use when Codex needs to run or inspect the SPM monitor, adjust YAML strategy rules, verify token-safe configuration, debug Binance/Telegram/Feishu monitoring, run tests, prepare Docker Compose deployment, or create recurring local monitoring automation for this repository.
---

# SmartMoney Protocol Monitor

Use this skill for the local SPM repository.

## Core Rules

- Preserve monitor-only behavior. Do not add automatic trading or private exchange account operations.
- Keep all secrets in `.env` or configuration files. Never hard-code Binance, OKX, Telegram, Feishu, or other tokens.
- Keep strategy conditions in `configs/strategies/*.yaml` plus strategy classes under `app/strategies`; do not bury them in `app/main.py`.
- Prefer small, readable Python modules over framework-heavy abstractions.
- Run `pytest` after behavior changes.

## Standard Workflow

1. Inspect `README.md`, `configs/system.yaml`, `configs/symbols.yaml`, and the relevant strategy YAML.
2. Inspect the matching code under `app/strategies`, `app/risk`, `app/indicators`, `app/signals`, and `app/notifications`.
3. Make focused changes.
4. Verify with:

```powershell
pytest
python -m app.main --run-once
```

On Windows, `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_monitor.ps1 -RunOnce` is the preferred initialization smoke test.

5. For Docker changes, also run:

```powershell
docker compose config
```

## Health Check

For a recurring Codex automation, perform:

- Run `python -m app.main report --hours <X> --send` using the configured interval.
- On Windows, prefer `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_periodic_report.ps1 -Hours <X>` because it checks `.venv` and can run tests after the report.
- Confirm `.env.example` and config files contain no real tokens.
- Run `pytest`.
- Run `python -m app.main --run-once`.
- Inspect recent logs or database rows if available.
- Report signal count, failing tests, missing configuration, and recommended next action.

## Common Files

- App entry: `app/main.py`
- Settings: `app/config/settings.py`
- YAML loader: `app/config/loader.py`
- Binance websocket: `app/connectors/binance_futures.py`
- Kline cache: `app/market/kline_store.py`
- Storage: `app/storage`
- Strategies: `app/strategies`
- Risk: `app/risk`
- Notifications: `app/notifications`
- Strategy configs: `configs/strategies`

## Strategy Safety Checklist

- L3 must pass R/R unless the user explicitly changes the protocol.
- BTC strong bullish should block or downgrade ETH short signals.
- BTC strong bearish should block or downgrade ETH long signals.
- L4 risk/invalid signals should not be suppressed by ordinary cooldown.
- Micro signals must remain Micro and must not be upgraded to Macro by code.
