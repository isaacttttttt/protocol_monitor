# SmartMoney Protocol Monitor

SmartMoney Protocol Monitor (SPM) is a monitor-only Python service that turns the Smart Money Protocol into an indicator archive plus an OpenAI-compatible LLM protocol report.

SPM fetches external market data, calculates protocol indicators, archives the indicator snapshot, sends each target's compact snapshot plus protocol text to the configured LLM, receives the model's single-symbol protocol report, and pushes each result through Feishu or Telegram as it is produced.

It does not place orders, manage exchange accounts, or require trading permissions.

## Feature Scope

- Binance USD-M futures WebSocket kline monitoring.
- First symbols: `ETHUSDT` and `BTCUSDT`.
- Intervals: `1m`, `5m`, `15m`, `1h`, `4h`.
- Kline cache and persistent storage.
- ATR, MACD, CVD proxy, VWAP, simplified market structure.
- Protocol report chain: external market data -> indicator snapshot -> archive -> per-symbol LLM analysis -> streaming Feishu report.
- Crypto protocol v16 and Equity protocol v17 are versioned under `protocols/`.
- Config-file driven OpenAI-compatible chat completions adapter.
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
WATCHLIST_EQUITY_SYMBOLS=SOXL,MU,CRCL,WDC,ARM,INTU,INFQ
EQUITY_CONTEXT_SYMBOLS=SPY,QQQ,IWM,DIA,XLK,SMH,SOXX,^VIX,^TNX,DX-Y.NYB
LLM_CONFIG=openox
LLM_CONFIG_DIR=configs/llms
LLM_API_KEY=
INDICATOR_ARCHIVE_PATH=data/indicator_snapshots.jsonl
CRYPTO_PROTOCOL_PATH=protocols/crypto_smartmoney_protocol_v16.md
EQUITY_PROTOCOL_PATH=protocols/equity_smartmoney_protocol_v17.md
```

All tokens and webhook secrets live in this configuration layer. Do not edit them into Python files.

LLM provider details live under `configs/llms/`. Use `LLM_CONFIG=openox` to select `configs/llms/openox.yaml`; switch providers by changing `LLM_CONFIG` and `LLM_API_KEY`. URL, model, timeout, and provider-specific request parameters belong in the YAML file, not in `.env`.

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

Feishu reports are sent as rich text (`msg_type=post`). LLM report mode pushes one message per analyzed symbol as soon as that symbol's report is ready, with the keyword included in every title.

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
python -m app.main scheduled-report --hours 1 --send
```

with a UTC candidate schedule for the configured UTC+8 weekday push windows:

```text
0,30 2,13,14,15,16 * * 0-5
```

Railway schedules are UTC. The candidate schedule includes a few no-op runs because one Cron expression cannot represent the six uneven local times exactly. `scheduled-report` checks `Asia/Shanghai`, weekdays, and the configured local-time allowlist before fetching data or sending anything. The actual push times are 10:00, 21:30, 22:00, 22:30, 23:00, and 00:00 UTC+8, Monday through Friday.

Railway does not start a Cron service when its deployment is manually opened outside the schedule. On-demand debugging therefore uses a second, non-Cron service configured by `railway.manual.toml`. Its start command is `python -m app.main report --hours 1 --send`; each deploy or redeploy runs immediately, sends once, and exits. Disable automatic GitHub deployments for this manual service.

See `RAILWAY.md` for the full deployment steps and required Railway variables.

On hosted environments where Binance returns `HTTP 451`, crypto reports automatically fall back to OKX public swap data and then Yahoo spot crypto data.

For durable indicator archives on Railway, use Railway Postgres for `DATABASE_URL`. The local JSONL archive is useful during development, but a Cron container filesystem is not a reliable long-term archive unless you attach persistent storage.

## Strategy Configuration

Strategies are YAML files under `configs/strategies`.

Current strategies:

- `eth_cm2_pullback_fail_short.yaml`
- `eth_stand_above_1605.yaml`
- `eth_cm3_liquidity_sweep_long.yaml`

Important: C-M2 uses the active pressure-zone invalidation plus the configured ATR buffer. Live L3 still obeys the R/R filter; if `TP1 R/R < 1.5`, the system downgrades to L2.

US-equity sector metadata lives in `configs/equity_sectors.yaml`. SOXL is evaluated as a daily-reset 3x semiconductor ETF against SOXX/SMH; MU is evaluated as a semiconductor memory stock against SOXX/SMH plus configured peers. Python computes a deterministic ORB-retest execution gate from pre-market RVOL, gap, completed opening range, session VWAP, volume confirmation and sector alignment. The LLM explains and scores the evidence but cannot upgrade an untriggered ORB candidate to a trade.

Live crypto strategies use dynamic causal reference levels when enough history is available: C-M2 derives a 15m VWAP/ATR pullback zone, and C-M3 uses the prior 20-bar low plus an ATR reclaim. YAML price levels remain fallback references for startup or insufficient data.

L3 signals are paper trades. Subsequent closed 1m/5m candles update TP1, breakeven-after-TP1, TP2, stop and time-stop outcomes. Portfolio risk caps correlated crypto/semiconductor exposure and can downgrade an L3 signal to L2 when no cluster risk budget remains.

## Backtest Core

`app/backtest` provides a causal bar-by-bar simulator. A signal produced from a closed bar fills at the next bar open, includes configurable fees/slippage, treats a same-bar stop/target collision conservatively, and reports net return, maximum drawdown, Calmar, per-trade Sharpe, win rate, profit factor and exposure. `run_walk_forward` selects parameters only on a training window and freezes them for the following test window.

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

The first command prints a local summary. The second command streams one LLM report per symbol through enabled Telegram/Feishu channels. The default interval lives in `configs/system.yaml`:

```yaml
automation:
  report_interval_hours: 1
  report_schedule:
    enabled: true
    timezone: Asia/Shanghai
    weekdays: [1, 2, 3, 4, 5]
    times: ["00:00", "10:00", "21:30", "22:00", "22:30", "23:00"]
    grace_minutes: 10

report:
  use_llm_analysis: true
  crypto_symbols: ["ETHUSDT", "BTCUSDT"]
  equity_symbols: ["SOXL", "MU", "CRCL", "WDC", "ARM", "INTU", "INFQ"]
  equity_context_symbols: ["SPY", "QQQ", "IWM", "DIA", "XLK", "SMH", "SOXX", "^VIX", "^TNX", "DX-Y.NYB"]
```

On Railway, prefer changing `WATCHLIST_CRYPTO_SYMBOLS` and `WATCHLIST_EQUITY_SYMBOLS` in Variables instead of editing this YAML.

If `LLM_CONFIG` or the selected config's API key is missing, the report command still fetches market data, calculates indicators, archives the snapshot, and prints a configuration warning. Once the config and key are present, the same command calls the configured LLM separately for each symbol and sends each protocol report as soon as it is generated.

OpenOX uses the OpenAI-compatible Chat Completions endpoint configured in `configs/llms/openox.yaml`. The API key is read only from `LLM_API_KEY`; never put it in YAML. The retained FineRes profile remains available by setting `LLM_CONFIG=fineres`.

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
