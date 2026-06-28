# Strategy Expansion Plan

## Acceptance Criteria

1. Equity sector mappings identify SOXL and MU as semiconductor exposure with SOXX/SMH benchmarks and relevant peers.
2. Snapshot watchlist/context automatically includes required sector benchmarks.
3. Indicator packs expose trend, volatility, price/volume, risk, and setup-candidate factors without producing a final trade verdict.
4. Equity targets expose relative strength, beta/correlation, sector breadth, and target-vs-sector evidence.
5. LLM payload clearly separates observed factors from LLM-owned Micro/Macro judgment.
6. LLM output requires independent Micro and Macro conclusions and explicit missing-data discounts.
7. ETH C-M2/C-M3 use configured ATR buffers and secondary configured levels are represented.
8. Binance interval configuration uses valid interval names.
9. Existing behavior remains covered and all tests pass.

## Test Scenarios

- Loading sector metadata for SOXL yields leveraged ETF, semiconductor sector, SOXX primary benchmark, and SMH secondary benchmark.
- Loading MU yields semiconductor/memory classification and peer list.
- Benchmark collection is de-duplicated and includes configured indices and sector/peer symbols.
- Factor pack returns EMA trend structure, realized volatility, efficiency, range position, gap/volume factors, and candidate setups.
- Relative context computes target returns versus SPY and sector, plus beta/correlation when aligned data exists.
- LLM prompt explicitly assigns final judgment to the model and asks for separate Micro/Macro conclusions.
- ATR-buffered short stop is above the structure stop; ATR-buffered long stop is below the sweep low.
- Secondary ETH levels appear in strategy observations.

## Implementation Checklist

- [x] Tests first and expected RED run.
- [x] Sector configuration and loader.
- [x] Expanded factor calculations.
- [x] Equity hierarchy and relative context.
- [x] LLM contract update.
- [x] ETH runtime risk/config updates.
- [x] Documentation inventory update.
- [x] Full test/build validation.
