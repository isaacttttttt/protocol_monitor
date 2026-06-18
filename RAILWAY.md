# Railway Cron Deployment

This project can run on Railway as a short-lived Cron Job. Railway starts the container at the configured report times, executes the report command, calculates and archives indicators, calls the configured LLM, sends the Feishu notification, and exits.

## Railway Settings

The repository includes `railway.toml`:

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "python -m app.main report --hours 1 --send"
cronSchedule = "0 2,10,16,22 * * *"
restartPolicyType = "NEVER"
```

Railway schedules are UTC. The Cron service uses `0 2,10,16,22 * * *`, which triggers at 02:00, 10:00, 16:00, and 22:00 UTC.

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
WATCHLIST_EQUITY_SYMBOLS=CRCL,WDC,ARM,INTU,INFQ
EQUITY_CONTEXT_SYMBOLS=SPY,QQQ,IWM,XLK,SMH
LLM_CONFIG=fineres
LLM_CONFIG_DIR=configs/llms
LLM_API_KEY=<your-llm-api-key>
INDICATOR_ARCHIVE_PATH=data/indicator_snapshots.jsonl
CRYPTO_PROTOCOL_PATH=protocols/crypto_smartmoney_protocol_v16.md
EQUITY_PROTOCOL_PATH=protocols/equity_smartmoney_protocol_v17.md
```

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` can stay empty unless you also want Telegram.

`LLM_CONFIG` selects a YAML file under `configs/llms/`. Provider URL, model, timeout, and request parameters live there; only `LLM_API_KEY` remains external.

For FineRes, use `LLM_CONFIG=fineres`. `configs/llms/fineres.yaml` follows the native Chat Completions request body and does not send the non-standard `thinking` parameter. `reasoning_effort`, if added to that YAML, must be `low`, `medium`, or `high`.

To add or remove monitored symbols, edit `WATCHLIST_CRYPTO_SYMBOLS` and `WATCHLIST_EQUITY_SYMBOLS` in Railway Variables and redeploy/restart the Cron service. No code push is needed.

Feishu reports are sent as rich text (`msg_type=post`). Each run sends a summary message plus separate per-symbol messages instead of one oversized full report.

Crypto reports try Binance USD-M first. If the Railway region receives Binance `HTTP 451`, the report automatically falls back to OKX public swap data, then Yahoo spot crypto data.

## Persistence

Railway Cron can run this workflow, but durable indicator history should not rely on the container's local filesystem. Recommended setup:

- Add Railway Postgres.
- Set `DATABASE_URL` to the Postgres connection string.
- Keep `INDICATOR_ARCHIVE_PATH` for local/dev convenience only, or attach a persistent volume if you explicitly want file archives.

Without Postgres or a volume, the Feishu report still works, but historical indicator archives may disappear between deployments or containers.

## Dashboard Steps

1. Push this repository to GitHub.
2. In Railway, create a new project from the GitHub repo.
3. Keep the generated service as a Cron Job service.
4. Confirm the service settings show:
   - Start Command: `python -m app.main report --hours 1 --send`
   - Cron Schedule: `0 2,10,16,22 * * *`
5. Add the variables above in the service Variables tab.
6. Deploy and open Logs to confirm the report prints and Feishu receives `SPM 1H FineRes 协议监控报告` or the provider name configured in `configs/llms/<LLM_CONFIG>.yaml`.

## Manual Test

In Railway, use a manual deploy/run to confirm the start command exits after sending the report. If a previous cron run remains `Active`, future runs can be skipped.
