# Strategy Expansion Context

## Objective

Expand the indicator and factor layer, add explicit US-equity sector context with SOXL and MU as reference implementations, and reduce gaps between the written protocols and runtime behavior while leaving final scoring and trade judgment to the LLM.

## Existing Architecture

- `app/review/indicator_snapshot.py` builds market-data and factor snapshots.
- `app/review/llm_protocol_report.py` sends one target plus required context and the full market protocol to the selected LLM.
- `app/strategies` contains narrow deterministic ETH alert rules.
- `protocols` defines broad Crypto Micro/Macro and Equity Micro/Macro decision frameworks.
- Notifications render LLM text into Feishu cards or Telegram messages.

## Design Direction

- Code produces observations, normalized factors, benchmark comparisons, candidate levels, and protocol setup evidence.
- Code does not assign final protocol scores, probabilities, directions, or TRADE/ARMED/WATCH decisions.
- Equity context becomes explicit: external proxies, indices, sector ETFs, industry/peer context, then target execution.
- Sector metadata is configuration-driven so symbols can be added without changing indicator code.
- Existing output and archive schemas remain backward compatible; new fields are additive.

## Implementation Paths

- Add `configs/equity_sectors.yaml` for target-to-sector mappings.
- Add `app/review/equity_sectors.py` for validated configuration and benchmark collection.
- Extend `app/review/indicator_snapshot.py` with trend, volatility, volume/flow, regime, relative-strength, beta/correlation, and setup-candidate factors.
- Update watchlists to include SOXL and MU.
- Update the LLM prompt to require independent Micro/Macro judgments based on supplied factors without inventing missing regime data.
- Improve ETH runtime risk wiring for ATR stops and configured secondary zones.

## Existing Documentation

`README.md` defines the service as monitor-only and requires deterministic alert conditions to stay in strategy classes/configuration while LLM protocol judgment stays in the report path. No `CODEASSIST.md` exists; repository-specific constraints are therefore taken from README and the local SmartMoney skill.

## Constraints

- No order placement or private exchange operations.
- No hard-coded secrets.
- Missing data must be explicit.
- Proxy indicators must retain quality labels.
- Full tests must remain green.

