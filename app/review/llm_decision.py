from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)


DecisionStatus = Literal["TRADE", "NO_TRADE", "DATA_ERROR"]
DecisionTimeframe = Literal["1H", "4H", "DAY"]
TradeDirection = Literal["LONG", "SHORT"]
ExecutionMode = Literal["NOW", "LIMIT", "STOP"]

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

_JSON_FENCE = re.compile(
    r"\A\s*```(?:json)?[ \t]*(?:\r?\n)?(?P<body>.*?)(?:\r?\n)?```\s*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_AMBIGUOUS_NO_TRADE_TERMS = (
    "等待确认",
    "等待",
    "关注",
    "观察",
    "wait for confirmation",
    "waiting",
    "wait and see",
    "watch",
    "monitor",
)
_TRADE_ONLY_FIELDS = (
    "timeframe",
    "direction",
    "execution_mode",
    "execution_condition",
    "entry",
    "entry_zone_low",
    "entry_zone_high",
    "stop_loss",
    "tp1",
    "tp2",
    "time_stop",
    "position_r",
    "protocol_setup",
    "score",
    "evidence",
    "invalidation",
    "risk_reward",
)


class DecisionValidationError(ValueError):
    """A contract failure with issues suitable for an LLM repair prompt."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        detail = "\n".join(f"- {error}" for error in errors)
        super().__init__(f"LLM decision validation failed:\n{detail}")


class LLMDecision(BaseModel):
    """Strict public contract for one protocol decision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: DecisionStatus
    timeframe: DecisionTimeframe | None = None
    direction: TradeDirection | None = None
    execution_mode: ExecutionMode | None = None
    execution_condition: NonEmptyString | None = None
    entry: PositiveFiniteFloat | None = None
    entry_zone_low: PositiveFiniteFloat | None = None
    entry_zone_high: PositiveFiniteFloat | None = None
    stop_loss: PositiveFiniteFloat | None = None
    tp1: PositiveFiniteFloat | None = None
    tp2: PositiveFiniteFloat | None = None
    time_stop: NonEmptyString | None = None
    position_r: Annotated[float, Field(gt=0, le=1.5, allow_inf_nan=False)] | None = None
    protocol_setup: NonEmptyString | None = None
    score: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    evidence: list[NonEmptyString] = Field(default_factory=list, max_length=3)
    invalidation: NonEmptyString | None = None
    summary: NonEmptyString | None = None
    rejection_reasons: list[NonEmptyString] = Field(default_factory=list, max_length=3)
    risk_reward: FiniteFloat | None = None

    @model_validator(mode="after")
    def validate_status_contract(self) -> LLMDecision:
        if self.status == "TRADE":
            required = (
                "timeframe",
                "direction",
                "execution_mode",
                "execution_condition",
                "entry",
                "entry_zone_low",
                "entry_zone_high",
                "stop_loss",
                "tp1",
                "time_stop",
                "position_r",
                "protocol_setup",
                "score",
                "invalidation",
                "summary",
            )
            missing = [name for name in required if getattr(self, name) is None]
            if missing:
                raise ValueError(f"TRADE requires fields: {', '.join(missing)}")
            if len(self.evidence) != 3:
                raise ValueError("TRADE requires exactly 3 concrete evidence items")
            if len(set(self.evidence)) != 3:
                raise ValueError("TRADE evidence items must be distinct")
            if any(not any(char.isdigit() for char in item) for item in self.evidence):
                raise ValueError("TRADE evidence items must each contain numeric evidence")
            numeric_contract_fields = {
                "execution_condition": self.execution_condition,
                "time_stop": self.time_stop,
                "invalidation": self.invalidation,
            }
            missing_numbers = [
                name
                for name, value in numeric_contract_fields.items()
                if value is not None and not any(char.isdigit() for char in value)
            ]
            if missing_numbers:
                raise ValueError(
                    "TRADE execution fields must contain explicit numbers: "
                    + ", ".join(missing_numbers)
                )
            if self.rejection_reasons:
                raise ValueError("TRADE rejection_reasons must be empty")
            return self

        populated = [
            name
            for name in _TRADE_ONLY_FIELDS
            if getattr(self, name) not in (None, [])
        ]
        if populated:
            raise ValueError(
                f"{self.status} trade fields must be empty: {', '.join(populated)}"
            )
        if self.summary is None:
            raise ValueError(f"{self.status} requires a non-empty summary")
        if not self.rejection_reasons:
            raise ValueError(f"{self.status} requires non-empty rejection_reasons")

        if self.status == "NO_TRADE":
            text = " ".join([*self.rejection_reasons, self.summary or ""]).casefold()
            ambiguous = [
                term for term in _AMBIGUOUS_NO_TRADE_TERMS if term.casefold() in text
            ]
            if ambiguous:
                raise ValueError(
                    "NO_TRADE must state definitive rejection reasons; "
                    f"ambiguous wording found: {', '.join(ambiguous)}"
                )
        return self


def normalize_decision(payload: str | Mapping[str, Any]) -> dict[str, Any]:
    """Parse and normalize an LLM decision without market-price validation."""

    data = _parse_payload(payload)
    for field in ("status", "timeframe", "direction", "execution_mode"):
        value = data.get(field)
        if isinstance(value, str):
            data[field] = value.strip().upper()

    try:
        decision = LLMDecision.model_validate(data)
    except ValidationError as exc:
        raise DecisionValidationError(_format_pydantic_errors(exc)) from exc
    return decision.model_dump(mode="json")


def validate_decision(
    payload: str | Mapping[str, Any],
    *,
    current_price: float,
    atr14: float | Mapping[str, float] | None = None,
    min_rr: float = 1.5,
    max_position_r: float = 1.5,
) -> dict[str, Any]:
    """Return a normalized decision after deterministic execution checks."""

    normalized = normalize_decision(payload)
    if normalized["status"] != "TRADE":
        return normalized

    errors: list[str] = []
    current = _positive_finite("current_price", current_price, errors)
    minimum_rr = _positive_finite("min_rr", min_rr, errors)
    maximum_position = _positive_finite("max_position_r", max_position_r, errors)
    # ATR remains an input for backward compatibility and LLM evidence, but the
    # complete protocols do not define a universal 0.3-3 ATR stop-distance gate.
    _ = atr14

    direction = normalized["direction"]
    mode = normalized["execution_mode"]
    entry = normalized["entry"]
    zone_low = normalized["entry_zone_low"]
    zone_high = normalized["entry_zone_high"]
    stop = normalized["stop_loss"]
    tp1 = normalized["tp1"]
    tp2 = normalized["tp2"]
    position_r = normalized["position_r"]

    if zone_low > zone_high:
        errors.append("entry_zone_low must be <= entry_zone_high")
    if not zone_low <= entry <= zone_high:
        errors.append("entry must be inside [entry_zone_low, entry_zone_high]")

    if direction == "LONG":
        if not stop < zone_low <= entry <= zone_high < tp1:
            errors.append(
                "LONG prices must satisfy "
                "stop_loss < entry_zone_low <= entry <= entry_zone_high < tp1"
            )
        if tp2 is not None and not tp1 < tp2:
            errors.append("LONG tp2 must be > tp1")
    else:
        if not stop > zone_high >= entry >= zone_low > tp1:
            errors.append(
                "SHORT prices must satisfy "
                "stop_loss > entry_zone_high >= entry >= entry_zone_low > tp1"
            )
        if tp2 is not None and not tp2 < tp1:
            errors.append("SHORT tp2 must be < tp1")

    if maximum_position is not None and position_r > maximum_position:
        errors.append(
            f"position_r {position_r:.4f} exceeds max_position_r {maximum_position:.4f}"
        )

    execution_price: float | None = None
    if current is not None:
        if mode == "NOW":
            execution_price = current
            if not zone_low <= current <= zone_high:
                errors.append("NOW requires current_price inside the entry zone")
        elif mode == "LIMIT":
            execution_price = zone_high if direction == "LONG" else zone_low
            if direction == "LONG" and not zone_high < current:
                errors.append(
                    "LONG LIMIT entry zone must be entirely below current_price"
                )
            if direction == "SHORT" and not zone_low > current:
                errors.append(
                    "SHORT LIMIT entry zone must be entirely above current_price"
                )
        elif mode == "STOP":
            execution_price = zone_high if direction == "LONG" else zone_low
            if direction == "LONG" and not zone_low > current:
                errors.append(
                    "LONG STOP entry zone must be entirely above current_price"
                )
            if direction == "SHORT" and not zone_high < current:
                errors.append(
                    "SHORT STOP entry zone must be entirely below current_price"
                )
        if execution_price is not None and not math.isclose(
            entry,
            execution_price,
            rel_tol=1e-9,
            abs_tol=1e-8,
        ):
            errors.append(
                f"entry {entry:.8g} must equal conservative executable price "
                f"{execution_price:.8g} for {mode}"
            )

    computed_rr: float | None = None
    if execution_price is not None:
        if direction == "LONG":
            risk_distance = execution_price - stop
            reward_distance = tp1 - execution_price
            valid_execution_order = stop < execution_price < tp1
        else:
            risk_distance = stop - execution_price
            reward_distance = execution_price - tp1
            valid_execution_order = stop > execution_price > tp1

        if not valid_execution_order or risk_distance <= 0 or reward_distance <= 0:
            errors.append(
                f"{direction} actual executable price must remain between stop_loss and tp1"
            )
        else:
            computed_rr = reward_distance / risk_distance
            if not math.isfinite(computed_rr):
                errors.append(
                    "actual executable price and stop_loss must define finite risk"
                )
            elif minimum_rr is not None and computed_rr < minimum_rr:
                errors.append(
                    f"TP1 risk/reward {computed_rr:.4f} is below min_rr "
                    f"{minimum_rr:.4f} at actual executable price "
                    f"{execution_price:.8g}"
                )

    if errors:
        raise DecisionValidationError(errors)

    normalized["risk_reward"] = round(computed_rr, 4)
    return normalized


def normalize_llm_decision(payload: str | Mapping[str, Any]) -> dict[str, Any]:
    """Descriptive alias for callers outside the review package."""

    return normalize_decision(payload)


def validate_llm_decision(
    payload: str | Mapping[str, Any],
    *,
    current_price: float,
    atr14: float | Mapping[str, float] | None = None,
    min_rr: float = 1.5,
    max_position_r: float = 1.5,
) -> dict[str, Any]:
    """Descriptive alias for callers outside the review package."""

    return validate_decision(
        payload,
        current_price=current_price,
        atr14=atr14,
        min_rr=min_rr,
        max_position_r=max_position_r,
    )


def _parse_payload(payload: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    if not isinstance(payload, str):
        raise DecisionValidationError(
            [f"payload must be a JSON string or mapping, got {type(payload).__name__}"]
        )

    text = payload.strip()
    fence_match = _JSON_FENCE.fullmatch(text)
    if fence_match:
        text = fence_match.group("body").strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecisionValidationError(
            [f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"]
        ) from exc
    if not isinstance(decoded, dict):
        raise DecisionValidationError(["decision JSON must be an object"])
    return decoded


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    formatted: list[str] = []
    for issue in exc.errors(include_url=False):
        location = ".".join(str(part) for part in issue["loc"])
        message = issue["msg"]
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        formatted.append(f"{location}: {message}" if location else message)
    return formatted


def _positive_finite(name: str, value: Any, errors: list[str]) -> float | None:
    if isinstance(value, bool):
        errors.append(f"{name} must be a finite number > 0")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a finite number > 0")
        return None
    if not math.isfinite(number) or number <= 0:
        errors.append(f"{name} must be a finite number > 0")
        return None
    return number


__all__ = [
    "DecisionValidationError",
    "LLMDecision",
    "normalize_decision",
    "normalize_llm_decision",
    "validate_decision",
    "validate_llm_decision",
]
