def atr_buffered_stop(direction: str, structure_stop: float, atr: float, multiplier: float) -> float:
    buffer = max(0.0, float(atr)) * max(0.0, float(multiplier))
    if direction == "SHORT":
        return float(structure_stop) + buffer
    if direction == "LONG":
        return float(structure_stop) - buffer
    return float(structure_stop)

