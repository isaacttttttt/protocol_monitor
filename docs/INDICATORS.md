# Indicator Inventory

SPM calculates indicators first, archives the full snapshot, then sends the snapshot plus protocol text to DeepSeek. The code should not turn these indicators into final protocol conclusions; that judgment belongs to the LLM report step.

## Global Snapshot Metadata

- `schema_version`
- `run_id`
- `generated_at`
- data source per symbol
- data timestamp and Asia/Shanghai display time
- recent signal count and recent signal sample
- kline record count and strategy state count
- data-quality notes and unavailable data warnings

## Crypto Symbols

Current watchlist: `ETHUSDT`, `BTCUSDT`.

Timeframes:

- `5m`
- `15m`
- `4h`
- `1d`

Market data:

- last price
- 24h change percentage
- 24h high / low
- source fallback notes: Binance USD-M -> OKX Swap -> Yahoo spot

Derivatives:

- mark price
- funding rate
- funding rate percentage
- open interest
- open interest short text
- basis percentage proxy

Per-timeframe indicators:

- last OHLCV bar
- 1 / 3 / 10 / 20 bar returns
- SMC structure proxy: last swing high, last swing low, BOS up, BOS down, trend
- ATR14
- ATR14 percentage
- MACD, MACD signal, MACD histogram
- RSI14
- squeeze state, Bollinger width percentage, Keltner width percentage, squeeze momentum
- VWAP
- anchored VWAP from recent low
- anchored VWAP from recent high
- last volume
- volume SMA20
- relative volume
- CVD proxy: cumulative proxy, last delta, new high/low flags, trend
- OBV value / 5-bar slope / trend
- A/D line value / 5-bar slope / trend
- NVI value / 10-bar slope / trend
- liquidity sweep proxy: swept recent high/low and closed back inside, previous range high/low, upper/lower wick ratio
- volume profile proxy: POC, HVN, LVN, value-area proxy, volume bins

Unavailable or proxy-only crypto fields:

- real CVD
- cluster delta
- liquidation heatmap
- long/short ratio
- true order-book imbalance

## Equity Symbols

Current watchlist: `CRCL`, `WDC`, `ARM`, `INTU`, `INFQ`.

Timeframes:

- `15m`
- `60m`
- `1d`
- `1wk`

Market data:

- last intraday price
- change percentage vs previous daily close
- last daily close
- gap percentage
- data timestamp
- opening 30m range high / low
- relative strength vs SPY over 20 daily bars

Per-timeframe indicators:

- last OHLCV bar
- 1 / 3 / 10 / 20 bar returns
- SMC structure proxy: last swing high, last swing low, BOS up, BOS down, trend
- ATR14
- ATR14 percentage
- MACD, MACD signal, MACD histogram
- RSI14
- squeeze state, Bollinger width percentage, Keltner width percentage, squeeze momentum
- VWAP
- anchored VWAP from recent low
- anchored VWAP from recent high
- last volume
- volume SMA20
- relative volume
- CVD proxy: cumulative proxy, last delta, new high/low flags, trend
- OBV value / 5-bar slope / trend
- A/D line value / 5-bar slope / trend
- NVI value / 10-bar slope / trend
- liquidity sweep proxy: swept recent high/low and closed back inside, previous range high/low, upper/lower wick ratio
- volume profile proxy: POC, HVN, LVN, value-area proxy, volume bins

Equity context symbols:

- `SPY`
- `QQQ`
- `IWM`
- `XLK`
- `SMH`

Context indicators:

- price
- daily change percentage
- daily indicator pack
- 60m indicator pack

Unavailable or proxy-only equity fields:

- real CVD
- options flow
- gamma exposure
- dark-pool prints
- true order-book imbalance
