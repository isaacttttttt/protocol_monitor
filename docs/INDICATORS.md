# Indicator Inventory

SPM calculates indicators first, archives the full snapshot, then sends one compact target-symbol snapshot plus protocol text to the configured LLM at a time. The code should not turn these indicators into final protocol conclusions; that judgment belongs to the LLM report step.

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

Configured by `WATCHLIST_CRYPTO_SYMBOLS` in env, with `configs/system.yaml` as fallback.

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
- Delta Flow proxy:
  - source and quality: Binance taker delta when available, otherwise OHLCV proxy
  - taker buy volume / taker sell volume for Binance USD-M crypto candles
  - close-location-value delta
  - candle-body delta
  - signed-volume delta
  - hybrid delta
  - buy/sell volume proxy
  - buy ratio
  - imbalance ratio
  - delta SMA5 / SMA20
  - delta z-score
  - positive/negative delta sum over 20 bars
  - net delta percentage over 20 bars
  - cumulative delta 20 / 50
  - normalized cumulative delta
  - CVD slope 5 / 20
  - CVD acceleration
  - stacked delta direction/count
  - bullish/bearish regular CVD divergence
  - buy/sell absorption proxy
  - effort-no-result, stopping volume, climax volume
- OBV value / 5-bar slope / trend
- A/D line value / 5-bar slope / trend
- NVI value / 10-bar slope / trend
- liquidity sweep proxy: swept recent high/low and closed back inside, previous range high/low, upper/lower wick ratio
- Smart Money proxy:
  - displacement count/recent/last event
  - bullish/bearish fair value gaps and mitigation state
  - bullish/bearish order block zones and mitigation state
  - equal highs / equal lows liquidity pools
  - 20/50 bar range high/low
  - premium / discount / equilibrium position
- volume profile proxy: POC, HVN, LVN, value-area proxy, volume bins
- volume profile method: candle volume is distributed across overlapped price bins; value area expands from POC until about 70% volume coverage
- volume delta profile proxy: delta POC, positive-delta POC, negative-delta POC, net delta, dominant delta zones
- confluence pack: price vs VWAP, price vs POC, ATR distance from POC, structure-flow alignment, AI attention flags
- factor pack:
  - EMA 8 / 20 / 50 / 200, alignment and normalized slopes
  - annualized realized volatility and Parkinson high-low volatility
  - short/medium range expansion, rolling drawdown and range location
  - Kaufman-style efficiency ratio
  - gap, dollar-volume, volume-trend and up-volume factors
  - explicit data-readiness and proxy-quality fields
- setup candidates: liquidity sweep, trend pullback, breakout/breakdown and volatility-transition evidence; these are observations only, not trade decisions
- protocol candidate evidence for C-M1/C-M2/C-M3/C-M4 and C-W1/C-W2/C-W3/C-W4; unavailable OI history, ETH/BTC and BTC.D inputs are explicitly marked

Unavailable or proxy-only crypto fields:

- real CVD
- cluster delta
- liquidation heatmap
- long/short ratio
- true order-book imbalance

## Equity Symbols

Current watchlist: `CRCL`, `WDC`, `ARM`, `INTU`, `INFQ`.

Configured by `WATCHLIST_EQUITY_SYMBOLS` in env, with `configs/system.yaml` as fallback.

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
- Delta Flow proxy:
  - source and quality: Binance taker delta when available, otherwise OHLCV proxy
  - close-location-value delta
  - candle-body delta
  - signed-volume delta
  - hybrid delta
  - buy/sell volume proxy
  - buy ratio
  - imbalance ratio
  - delta SMA5 / SMA20
  - delta z-score
  - positive/negative delta sum over 20 bars
  - net delta percentage over 20 bars
  - cumulative delta 20 / 50
  - normalized cumulative delta
  - CVD slope 5 / 20
  - CVD acceleration
  - stacked delta direction/count
  - bullish/bearish regular CVD divergence
  - buy/sell absorption proxy
  - effort-no-result, stopping volume, climax volume
- OBV value / 5-bar slope / trend
- A/D line value / 5-bar slope / trend
- NVI value / 10-bar slope / trend
- liquidity sweep proxy: swept recent high/low and closed back inside, previous range high/low, upper/lower wick ratio
- Smart Money proxy:
  - displacement count/recent/last event
  - bullish/bearish fair value gaps and mitigation state
  - bullish/bearish order block zones and mitigation state
  - equal highs / equal lows liquidity pools
  - 20/50 bar range high/low
  - premium / discount / equilibrium position
- volume profile proxy: POC, HVN, LVN, value-area proxy, volume bins
- volume profile method: candle volume is distributed across overlapped price bins; value area expands from POC until about 70% volume coverage
- volume delta profile proxy: delta POC, positive-delta POC, negative-delta POC, net delta, dominant delta zones
- confluence pack: price vs VWAP, price vs POC, ATR distance from POC, structure-flow alignment, AI attention flags

Equity context symbols:

- `SPY`
- `QQQ`
- `IWM`
- `XLK`
- `SMH`
- `SOXX`
- `DIA`
- `^VIX`
- `^TNX`
- `DX-Y.NYB`

Configured by `EQUITY_CONTEXT_SYMBOLS` in env, with `configs/system.yaml` as fallback.

Context indicators:

- price
- daily change percentage
- daily indicator pack
- 60m indicator pack
- configuration-driven sector and industry classification
- primary/secondary sector benchmarks and peer group
- 5/20/60-bar relative return versus index and sector
- 60-bar beta and correlation
- peer breadth and median peer return
- leveraged-ETF path-dependency/tracking-gap factors for SOXL-style products
- protocol candidate evidence for M-E1/M-E2/M-E3/M-E4 and W-E1/W-E2/W-E3/W-E4

The factor contract is deliberately split: code computes observations, comparisons and candidate conditions; the LLM produces final protocol scores, independent Micro/Macro judgments, conflicts and trade decisions.

Unavailable or proxy-only equity fields:

- real CVD
- real footprint/cluster delta
- real bid/ask aggressive delta
- options flow
- gamma exposure
- dark-pool prints
- true order-book imbalance
