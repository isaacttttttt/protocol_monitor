# Railway Scheduled and Manual Deployment

This project uses two short-lived Railway services from the same repository. The scheduled service runs only at configured Cron times. The manual service starts on demand, calculates and archives indicators, calls the configured LLM, sends the Feishu notification, and exits.

## Railway Settings

The scheduled service uses `railway.toml`:

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "python -m app.main scheduled-report --hours 1 --send"
cronSchedule = "0,30 2,13,14,15,16 * * 0-5"
restartPolicyType = "NEVER"
```

Railway schedules are UTC. This candidate expression covers the required UTC+8 windows, including local Monday 00:00 from a Sunday 16:00 UTC run. Because a single Cron expression cannot encode the six uneven times exactly, `scheduled-report` applies the `Asia/Shanghai` weekday/time allowlist before any market-data fetch, LLM call, or notification. Candidate times such as 10:30, 21:00, and 23:30 UTC+8 exit immediately without sending.

Actual push times, Monday through Friday in UTC+8:

- 10:00
- 21:30
- 22:00
- 22:30
- 23:00
- 00:00

The manual service uses `railway.manual.toml`:

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "python -m app.main report --hours 1 --send"
restartPolicyType = "NEVER"
```

It intentionally has no `cronSchedule`. A deploy or redeploy therefore starts an instance immediately and bypasses the scheduled-time allowlist. Disable GitHub automatic deployments for this service so a normal code push does not send an unintended report.

## Required Variables

Set these in Railway service variables:

```env
APP_ENV=railway
LOG_LEVEL=INFO
DATABASE_URL=sqlite+aiosqlite:///./spm.db
FEISHU_WEBHOOK_URL=<your-feishu-webhook>
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
LLM_API_KEY=<your-llm-api-key>
INDICATOR_ARCHIVE_PATH=data/indicator_snapshots.jsonl
CRYPTO_PROTOCOL_PATH=protocols/crypto_smartmoney_protocol_v16.md
EQUITY_PROTOCOL_PATH=protocols/equity_smartmoney_protocol_v17.md
```

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` can stay empty unless you also want Telegram.

`LLM_CONFIG` selects a YAML file under `configs/llms/`. Provider URL, model, timeout, and request parameters live there; only `LLM_API_KEY` remains external.

For OpenOX, use `LLM_CONFIG=openox`. `configs/llms/openox.yaml` persists the base URL, `gpt-5.6-sol` model, timeout, and supported request parameters. Set the secret only as `LLM_API_KEY` in Railway Variables. The retained FineRes profile remains available with `LLM_CONFIG=fineres`.

To add or remove monitored symbols, edit `WATCHLIST_CRYPTO_SYMBOLS` and `WATCHLIST_EQUITY_SYMBOLS` in Railway Variables and redeploy/restart the Cron service. No code push is needed.

Feishu reports are sent as rich text (`msg_type=post`). Each run sends a summary message plus separate per-symbol messages instead of one oversized full report.

Crypto reports try Binance USD-M first. If the Railway region receives Binance `HTTP 451`, the report automatically falls back to OKX public swap data, then Yahoo spot crypto data.

## Persistence

Railway Cron can run this workflow, but durable indicator history should not rely on the container's local filesystem. Recommended setup:

- Add Railway Postgres.
- Set `DATABASE_URL` to the Postgres connection string.
- Keep `INDICATOR_ARCHIVE_PATH` for local/dev convenience only, or attach a persistent volume if you explicitly want file archives.

Without Postgres or a volume, the Feishu report still works, but historical indicator archives may disappear between deployments or containers.

## Scheduled Service Setup

1. Keep the existing GitHub-backed service for scheduled reports.
2. In Settings, set **Railway Config File** to `/railway.toml`.
3. Confirm the service settings show:
   - Start Command: `python -m app.main scheduled-report --hours 1 --send`
   - Cron Schedule: `0,30 2,13,14,15,16 * * 0-5`
4. Add the variables above in the service Variables tab.
5. Deploy. Railway will create an instance only at a scheduled candidate time.

## Manual Service Setup

1. Add a second service from the same GitHub repository and name it `spm-manual`.
2. Copy the scheduled service variables or reference the same shared variables.
3. In Settings, set **Railway Config File** to the absolute path `/railway.manual.toml`.
4. Confirm Start Command is `python -m app.main report --hours 1 --send` and **Cron Schedule is empty**.
5. Disable GitHub automatic deployments for `spm-manual` in its Source settings.
6. Open the Command Palette and choose **Deploy Latest Commit** for the first run. For later runs, open the latest deployment's three-dot menu and choose **Redeploy**.
7. Open that deployment's logs. A successful run contains `report run started` and `report run completed`, sends the Feishu report, and ends with status `Completed`.

`No running instances` is expected after the manual command finishes because the process exits successfully. The completed deployment retains its logs. If the screen shows `No running instances` before a manual attempt and no new deployment appears, no deploy/redeploy was triggered.
