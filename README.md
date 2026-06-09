# SmartMoney Protocol Monitor

SmartMoney Protocol Monitor (SPM) is a monitor-only Python service that turns the Smart Money Protocol into an indicator archive plus DeepSeek-driven protocol report.

SPM fetches external market data, calculates protocol indicators, archives the indicator snapshot, sends the snapshot plus protocol text to DeepSeek, receives the model's protocol report, and pushes the result through Feishu or Telegram.

It does not place orders, manage exchange accounts, or require trading permissions.

## Feature Scope

- Binance USD-M futures WebSocket kline monitoring.
- First symbols: `ETHUSDT` and `BTCUSDT`.
- Intervals: `1m`, `5m`, `15m`, `1h`, `4h`.
- Kline cache and persistent storage.
- ATR, MACD, CVD proxy, VWAP, simplified market structure.
- Protocol report chain: external market data -> indicator snapshot -> archive -> DeepSeek -> Feishu report.
- Crypto protocol v16 and Equity protocol v17 are versioned under `protocols/`.
- DeepSeek API adapter using an OpenAI-compatible chat completions endpoint.
- Indicator archive table plus local JSONL archive.
- Indicator inventory in `docs/INDICATORS.md`.
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
FEISHU_KEYWORD=监控报告
BINANCE_WS_BASE=wss://fstream.binance.com/ws
BINANCE_REST_BASE=https://fapi.binance.com
OKX_REST_BASE=https://www.okx.com
YAHOO_CHART_BASE=https://query1.finance.yahoo.com/v8/finance/chart
WATCHLIST_CRYPTO_SYMBOLS=ETHUSDT,BTCUSDT
WATCHLIST_EQUITY_SYMBOLS=CRCL,WDC,ARM,INTU,INFQ
EQUITY_CONTEXT_SYMBOLS=SPY,QQQ,IWM,XLK,SMH
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING=enabled
DEEPSEEK_REASONING_EFFORT=max
DEEPSEEK_TEMPERATURE=0.2
DEEPSEEK_MAX_TOKENS=6000
DEEPSEEK_TIMEOUT_SECONDS=300
INDICATOR_ARCHIVE_PATH=data/indicator_snapshots.jsonl
CRYPTO_PROTOCOL_PATH=protocols/crypto_smartmoney_protocol_v16.md
EQUITY_PROTOCOL_PATH=protocols/equity_smartmoney_protocol_v17.md
```

All tokens and webhook secrets live in this configuration layer. Do not edit them into Python files.

Watchlist variables override `configs/system.yaml`. Use comma, semicolon, newline, or spaces as separators. For example:

```env
WATCHLIST_EQUITY_SYMBOLS=CRCL,WDC,ARM,NVDA,TSLA
```

## Telegram Setup

1. Create a bot with BotFather and put the token in `TELEGRAM_BOT_TOKEN`.
2. Send a message to the bot.
3. Resolve your chat id and put it in `TELEGRAM_CHAT_ID`.
4. Enable Telegram in `configs/system.yaml`.

## Feishu Setup

1. Create a Feishu group bot.
2. Copy the webhook URL into `FEISHU_WEBHOOK_URL`.
3. Set `notification.channels.feishu.enabled: true` in `configs/system.yaml`.

Feishu reports are sent as rich text (`msg_type=post`). The report is split into a summary message plus one message per symbol section, with the keyword included in every title.

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

For scheduled Feishu reports, Railway Cron can run this workflow because each run is a short-lived outbound HTTPS job. The repo includes `railway.toml`, which runs:

```powershell
python -m app.main report --hours 1 --send
```

with cron schedule:

```text
0 2,6,9,12,14,18 * * *
```

Railway schedules are UTC. This maps to UTC+8 report times: 10:00, 14:00, 17:00, 20:00, 22:00, and 02:00.

See `RAILWAY.md` for the full deployment steps and required Railway variables.

On hosted environments where Binance returns `HTTP 451`, crypto reports automatically fall back to OKX public swap data and then Yahoo spot crypto data.

For durable indicator archives on Railway, use Railway Postgres for `DATABASE_URL`. The local JSONL archive is useful during development, but a Cron container filesystem is not a reliable long-term archive unless you attach persistent storage.

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
python -m app.main report --hours 1
python -m app.main report --hours 1 --send
```

On Windows, the wrapper script is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_periodic_report.ps1 -Hours 1
```

The first command prints a local summary. The second command pushes the same report through enabled Telegram/Feishu channels. The default interval lives in `configs/system.yaml`:

```yaml
automation:
  report_interval_hours: 1

report:
  use_deepseek_analysis: true
  crypto_symbols: ["ETHUSDT", "BTCUSDT"]
  equity_symbols: ["CRCL", "WDC", "ARM", "INTU", "INFQ"]
  equity_context_symbols: ["SPY", "QQQ", "IWM", "XLK", "SMH"]
```

On Railway, prefer changing `WATCHLIST_CRYPTO_SYMBOLS` and `WATCHLIST_EQUITY_SYMBOLS` in Variables instead of editing this YAML.

If `DEEPSEEK_API_KEY` is empty, the report command still fetches market data, calculates indicators, archives the snapshot, and prints a configuration warning. Once the key is present, the same command calls DeepSeek and sends the full protocol report.

## Codex Automation

This repository includes a local Skill at:

```text
codex_skills/smartmoney-protocol-monitor/SKILL.md
```

Use it when asking Codex to inspect, run, adjust, or deploy this monitor.

For a recurring Codex automation, provide the interval, for example:

```text
Every 1 hour, in C:\Users\16225\Documents\SmartMoney Protocol Monitor, use the smartmoney-protocol-monitor skill to run python -m app.main report --hours 1 --send, then run pytest and summarize any errors.
```

Change `1` to your preferred `X`.

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
