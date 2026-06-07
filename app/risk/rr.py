def calc_rr(direction: str, entry: float, stop: float, target: float) -> float:
    if direction == "LONG":
        risk = entry - stop
        reward = target - entry
    elif direction == "SHORT":
        risk = stop - entry
        reward = entry - target
    else:
        return 0.0
    if risk <= 0:
        return 0.0
    return reward / risk
