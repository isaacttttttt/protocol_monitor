from enum import Enum


class StrategyStateEnum(str, Enum):
    IDLE = "IDLE"
    WATCHING = "WATCHING"
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    MANAGING = "MANAGING"
    INVALID = "INVALID"
    COOLDOWN = "COOLDOWN"
    EXPIRED = "EXPIRED"
