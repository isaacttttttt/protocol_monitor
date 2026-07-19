# Railway Scheduled Reports and Manual Run

This branch uses one short-lived Railway Cron service for both automatic reports and manual debugging. Automatic Cron candidates are filtered through the configured New York schedule and XNYS calendar. Clicking Railway's **Run** button outside a Cron candidate window is classified as manual, sends one report immediately, and exits.

No permanent HTTP service, second Railway service, deploy, or redeploy is required.

## Railway Settings

The service uses `railway.toml`:

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "python -m app.main scheduled-report --hours 1 --send"
cronSchedule = "30 14-20 * * 1-5"
restartPolicyType = "NEVER"
```

Railway schedules are UTC. The candidate expression covers both EDT and EST. For automatic candidates, `scheduled-report` converts the current time to `America/New_York` and checks:

- Monday through Friday
- XNYS trading holidays and half days
- The live XNYS market session
- 10:30, 11:30, 12:30, 13:30, 14:30, or 15:30 New York time

There is no automatic 16:00 close or after-hours push.

## How the Run Button Is Detected

Railway does not provide the process with a reliable flag that distinguishes Cron from a click on **Run**. The application therefore mirrors the Cron candidates in `configs/system.yaml`:

```yaml
automation:
  report_schedule:
    enabled: true
    timezone: America/New_York
    weekdays: [1, 2, 3, 4, 5]
    times: ["10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
    candidate_timezone: UTC
    candidate_weekdays: [1, 2, 3, 4, 5]
    candidate_times: ["14:30", "15:30", "16:30", "17:30", "18:30", "19:30", "20:30"]
    manual_run_outside_candidates: true
    grace_minutes: 10
```

The behavior is:

- A weekday start from 14:30 through 20:30 UTC, within the 10-minute grace period, is treated as an automatic Cron candidate and must pass all market checks.
- Any other start is treated as a manual Run and sends immediately, including weekends, holidays, pre-market, and after-hours.
- Set `manual_run_outside_candidates: false` to disable manual acceptance.

A manual click during the first 10 minutes after one of the UTC candidate times cannot be distinguished from Cron and is handled as an automatic candidate. Click outside those windows for an unambiguous manual run. The logs then contain:

```text
manual report run accepted
report run started
report run completed
```

If Railway starts an automatic candidate more than 10 minutes late, the same time-based inference can classify it as manual. Check the `local_time` log when diagnosing a delayed deployment; this is an inherent limitation because Railway does not provide a Cron-versus-Run marker to the process.

## Required Variables

Set these in the Railway service Variables tab:

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

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` can stay empty unless Telegram is enabled.

`LLM_CONFIG=openox` selects `configs/llms/openox.yaml`. Keep `LLM_API_KEY` only in Railway Variables.

## Railway Setup

1. Open the GitHub-backed Railway service.
2. Set its source branch to `codex/manual-trigger`.
3. In Settings, set **Railway Config File** to `/railway.toml`.
4. Remove any Start Command or Cron override left from the old `railway.manual.toml` setup.
5. Confirm:
   - Start Command: `python -m app.main scheduled-report --hours 1 --send`
   - Cron Schedule: `30 14-20 * * 1-5`
6. Add the required variables and deploy the branch once.

After that initial deployment:

- Automatic runs are started by Cron.
- For an immediate manual report, open the same service and click **Run**.
- Do not use **Deploy** or **Redeploy** merely to trigger a report.
- The process exits after each report, so `No running instances` between runs is normal.

## Persistence

The report works with SQLite, but a short-lived Railway container does not provide durable local storage. For persistent indicator history, use Railway Postgres for `DATABASE_URL` or attach a volume.
