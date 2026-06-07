# Railway Cron Deployment

This project can run on Railway as a short-lived Cron Job. Railway starts the container every 2 hours, executes the report command, sends the Feishu notification, and exits.

## Railway Settings

The repository includes `railway.toml`:

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "python -m app.main report --hours 2 --send"
cronSchedule = "0 */2 * * *"
restartPolicyType = "NEVER"
```

Railway schedules are UTC. `0 */2 * * *` means every 2 hours at minute 0.

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
```

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` can stay empty unless you also want Telegram.

Crypto reports try Binance USD-M first. If the Railway region receives Binance `HTTP 451`, the report automatically falls back to OKX public swap data, then Yahoo spot crypto data.

## Dashboard Steps

1. Push this repository to GitHub.
2. In Railway, create a new project from the GitHub repo.
3. Keep the generated service as a Cron Job service.
4. Confirm the service settings show:
   - Start Command: `python -m app.main report --hours 2 --send`
   - Cron Schedule: `0 */2 * * *`
5. Add the variables above in the service Variables tab.
6. Deploy and open Logs to confirm the report prints and Feishu receives `SPM 2H 周期监控报告`.

## Manual Test

In Railway, use a manual deploy/run to confirm the start command exits after sending the report. If a previous cron run remains `Active`, future runs can be skipped.
