# Strategy Expansion Progress

- [x] Repository and protocol exploration completed.
- [x] Requirements and architecture documented.
- [x] Tests written and RED state verified.
- [x] Sector configuration and loader implemented.
- [x] Indicator/factor expansion implemented.
- [x] Equity hierarchy and relative context implemented.
- [x] LLM conversation contract updated.
- [x] ETH runtime gaps reduced.
- [x] Full validation completed.

## Setup

- Mode: auto
- Repository: SmartMoney Protocol Monitor
- Documentation: `.agents/scratchpad/smartmoney-strategy-expansion`
- No `CODEASSIST.md` was present.

## TDD Cycles

### RED

- Added sector-registry, factor-pack, relative-market, LLM-contract, ATR-stop and time-stop tests.
- Initial collection failed because `equity_sectors`, `stops`, and expanded factor functions did not exist.
- Output: `logs/red-tests.log`.

### GREEN

- Added configuration-driven sector metadata and benchmark discovery.
- Added trend, volatility, price/volume, relative-market, peer-breadth and leveraged-ETF factors.
- Added Crypto and Equity protocol candidate evidence without final trade judgments.
- Added ATR-buffered stops, secondary C-M2 zone handling, and Micro time-stop expiry.
- Updated the LLM contract to require independent Micro/Macro judgments.

### REFACTOR

- Filtered per-symbol equity context so peer payloads do not inflate LLM requests.
- Added market-specific volatility annualization assumptions.
- Reduced duplicated flow evidence in protocol candidate payloads.

## Validation

- `pytest`: 59 passed.
- `compileall`: passed.
- `python -m app.main --run-once`: passed.
- Remaining warnings are existing naive-UTC deprecation warnings.
