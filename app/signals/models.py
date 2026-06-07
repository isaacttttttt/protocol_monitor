from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class SignalLevel(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


@dataclass
class Signal:
    signal_id: str
    exchange: str
    symbol: str
    book: str
    strategy_name: str
    level: SignalLevel
    direction: str
    status: str
    trigger_price: Decimal
    entry: Decimal | None
    sl: Decimal | None
    tp1: Decimal | None
    tp2: Decimal | None
    tp3: Decimal | None
    rr_to_tp1: float | None
    position_r: float
    trigger_reason: str
    invalid_condition: str
    risk_flags: dict = field(default_factory=dict)
    btc_filter: dict = field(default_factory=dict)
    flow_state: dict = field(default_factory=dict)
    raw_snapshot: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    tp1_reached: bool = False
