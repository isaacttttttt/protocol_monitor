# Protocol Summary

SPM is a monitor-only ETH/BTC strategy engine.

P0 behavior:

- Receive Binance USD-M futures klines.
- Persist closed klines.
- Calculate ATR, MACD, CVD proxy, VWAP, and simplified structure.
- Apply BTC filter and R/R filter.
- Generate L2/L3/L4 signals.
- Persist signals and notifications.
- Push through Telegram and Feishu when configured.

Never add automatic order placement in the first phase.
