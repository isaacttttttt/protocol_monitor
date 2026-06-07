# SmartMoney Protocol Monitor

SmartMoney Protocol Monitor (SPM) is a monitor-only Python service that turns the Smart Money Protocol rules in `SmartMoney Protocol Monitor.md` into a local signal engine.

SPM watches Binance USD-M futures market data, calculates protocol indicators, runs ETH/BTC strategy state machines, writes signals to a database, and pushes L2/L3/L4 notifications through Telegram or Feishu.

It does not place orders, manage exchange accounts, or require trading permissions.

## Feature Scope

- Binance USD-M futures WebSocket kline monitoring.
- First symbols: `ETHUSDT` and `BTCUSDT`.
- Intervals: `1m`, `5m`, `15m`, `1h`, `4h`.
- Kline cache and persistent storage.
- ATR, MACD, CVD proxy, VWAP, simplified market structure.
- BTC strong bullish / strong bearish filter.
- R/R filter, duplicate cooldown, 48H Micro time-stop helper.
- ETH C-M2 pullback-fail short strategy.
- ETH 1605 stand-above monitor.
- ETH C-M3 liquidity sweep long strategy.
- SQLite for local development and PostgreSQL for Docker Compose.
- Telegram and Feishu notification adapters.
- Codex Skill in `codex_skills/smartmoney-protocol-monitor`.

## Not Included

- No automatic order placement.
- No exchange account trading permissions.
- No complex Web UI.
- No ML prediction.
- No private API keys in source code.

## Environment Variables

Copy `.env.example` to `.env` and fill only what you need:

```env
APP_ENV=dev
LOG_LEVEL=INFO
DATABASE_URL=sqlite+aiosqlite:///./spm.db
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
FEISHU_WEBHOOK_URL=
BINANCE_WS_BASE=wss://fstream.binance.com/ws
BINANCE_REST_BASE=https://fapi.binance.com
```

All tokens and webhook secrets live in this configuration layer. Do not edit them into Python files.

## Telegram Setup

1. Create a bot with BotFather and put the token in `TELEGRAM_BOT_TOKEN`.
2. Send a message to the bot.
3. Resolve your chat id and put it in `TELEGRAM_CHAT_ID`.
4. Enable Telegram in `configs/system.yaml`.

## Feishu Setup

1. Create a Feishu group bot.
2. Copy the webhook URL into `FEISHU_WEBHOOK_URL`.
3. Set `notification.channels.feishu.enabled: true` in `configs/system.yaml`.

## Local Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main --run-once
python -m app.main
```

`--run-once` initializes configuration and database tables, then exits before opening the websocket.

Windows wrapper scripts:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_monitor.ps1 -RunOnce
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_monitor.ps1
```

## Docker Compose Start

```powershell
docker compose build
docker compose up
```

Docker Compose starts:

- `postgres`
- `spm-app`

The app reads tokens from your shell environment or `.env`.

## Railway Cron Deployment

For hosted 2H Feishu reports, use Railway Cron. The repo includes `railway.toml`, which runs:

```powershell
python -m app.main report --hours 2 --send
```

with cron schedule:

```text
0 */2 * * *
```

See `RAILWAY.md` for the full deployment steps and required Railway variables.

## Strategy Configuration

Strategies are YAML files under `configs/strategies`.

Current strategies:

- `eth_cm2_pullback_fail_short.yaml`
- `eth_stand_above_1605.yaml`
- `eth_cm3_liquidity_sweep_long.yaml`

Important: C-M2 keeps the document's default stop at `1625`, so live L3 triggering still obeys the R/R filter. If `TP1 R/R < 1.5`, the system downgrades to L2 instead of pushing L3.

## Add A Strategy

1. Add a YAML config under `configs/strategies`.
2. Implement a class in `app/strategies`.
3. Register it in `STRATEGY_TYPES` in `app/main.py`.
4. Add unit tests for L2, L3, L4, R/R, BTC filter behavior, and cooldown behavior.

Do not put strategy conditions in `app/main.py`.

## Run Tests

```powershell
pytest
```

## Periodic Reports

SPM includes a local report command for Codex automation or any system scheduler:

```powershell
python -m app.main report --hours 2
python -m app.main report --hours 2 --send
```

On Windows, the wrapper script is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_periodic_report.ps1 -Hours 2
```

The first command prints a local summary. The second command pushes the same report through enabled Telegram/Feishu channels. The default interval lives in `configs/system.yaml`:

```yaml
automation:
  report_interval_hours: 2

report:
  crypto_symbols: ["ETHUSDT", "BTCUSDT"]
  equity_symbols: ["CRCL", "WDC", "ARM"]
```

## Codex Automation

This repository includes a local Skill at:

```text
codex_skills/smartmoney-protocol-monitor/SKILL.md
```

Use it when asking Codex to inspect, run, adjust, or deploy this monitor.

For a recurring Codex automation, provide the interval, for example:

```text
Every 2 hours, in C:\Users\16225\Documents\SmartMoney Protocol Monitor, use the smartmoney-protocol-monitor skill to run python -m app.main report --hours 2 --send, then run pytest and summarize any errors.
```

Change `4` to your preferred `X`.

## FAQ

### Does SPM trade automatically?

No. It is monitor-only.

### Where do I configure Binance, OKX, Telegram, or Feishu tokens?

Use `.env` and configuration files. The current MVP only uses public Binance market data plus Telegram/Feishu notification secrets.

### Can I use SQLite?

Yes. Local development defaults to `sqlite+aiosqlite:///./spm.db`.

### Can I use PostgreSQL?

Yes. Docker Compose uses `postgresql+asyncpg://spm:spm_password@postgres:5432/spm`.

### Why did a strategy not send L3?

Check `rr_to_tp1`, BTC filter state, cooldown, and the strategy's YAML levels. R/R below `1.5` blocks L3 by design.
