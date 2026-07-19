from __future__ import annotations

import json

import pytest

from app.review.llm_decision import (
    DecisionValidationError,
    normalize_decision,
    validate_decision,
)


def _long_trade(**overrides):
    decision = {
        "status": "TRADE",
        "timeframe": "4H",
        "direction": "LONG",
        "execution_mode": "NOW",
        "execution_condition": "4H close holds above 100 and current price remains in 99-101",
        "entry": 100.0,
        "entry_zone_low": 99.0,
        "entry_zone_high": 101.0,
        "stop_loss": 96.0,
        "tp1": 108.0,
        "tp2": 112.0,
        "time_stop": "Exit after 2 closed 4H candles without reaching +0.5R",
        "position_r": 0.25,
        "protocol_setup": "4H liquidity reclaim with DAY trend alignment",
        "score": 84,
        "evidence": [
            "DAY close is above the 20-day EMA at 94.2",
            "4H reclaim closed at 100.4 above the 99.6 pivot",
            "4H volume is 1.7x its 20-bar median",
        ],
        "invalidation": "A 4H close below 96 invalidates the reclaim",
        "summary": "Take the long now inside 99-101 with defined 4H risk",
        "rejection_reasons": [],
        "risk_reward": 999,
    }
    decision.update(overrides)
    return decision


def _short_trade(**overrides):
    decision = {
        "status": "trade",
        "timeframe": "1h",
        "direction": "short",
        "execution_mode": "limit",
        "execution_condition": "Sell a retest of the 201-203 breakdown zone",
        "entry": 201.0,
        "entry_zone_low": 201.0,
        "entry_zone_high": 203.0,
        "stop_loss": 206.0,
        "tp1": 194.0,
        "tp2": 190.0,
        "time_stop": "Exit after 3 closed 1H candles if price has not moved +0.5R",
        "position_r": 0.2,
        "protocol_setup": "1H failed reclaim aligned with 4H bearish structure",
        "score": 79,
        "evidence": [
            "4H structure printed a lower high at 208.4",
            "1H breakdown closed at 198.8 below support 200",
            "1H sell volume reached 1.6x its 20-bar median",
        ],
        "invalidation": "A 1H close above 206 invalidates the failed reclaim",
        "summary": "Place the short limit at 202 with risk above 206",
        "rejection_reasons": [],
    }
    decision.update(overrides)
    return decision


def test_valid_long_trade_computes_rr_and_overrides_model_value():
    result = validate_decision(
        _long_trade(),
        current_price=100,
        atr14={"1H": 1.2, "4h": 5.0, "DAY": 12.0},
        min_rr=1.5,
    )

    assert result["status"] == "TRADE"
    assert result["timeframe"] == "4H"
    assert result["risk_reward"] == 2.0


def test_now_uses_current_price_for_entry_and_rr():
    result = validate_decision(
        _long_trade(
            entry=105,
            entry_zone_high=105,
            tp1=120,
            tp2=125,
        ),
        current_price=105,
        atr14=5,
    )

    assert result["entry"] == 105
    assert result["risk_reward"] == 1.6667


def test_entry_must_match_conservative_executable_price():
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _long_trade(
                entry=100,
                entry_zone_high=105,
                tp1=120,
                tp2=125,
            ),
            current_price=105,
            atr14=5,
        )

    assert any(
        "entry 100 must equal conservative executable price 105 for NOW" in error
        for error in captured.value.errors
    )


def test_now_rejects_rr_that_only_looks_valid_at_model_entry():
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _long_trade(entry_zone_high=105),
            current_price=105,
            atr14=5,
        )

    assert any(
        "risk/reward 0.3333 is below min_rr 1.5000 at actual executable price 105"
        in error
        for error in captured.value.errors
    )


def test_json_fence_is_parsed_and_short_enums_are_normalized():
    payload = f"```json\n{json.dumps(_short_trade())}\n```"

    result = validate_decision(
        payload,
        current_price=198,
        atr14=4,
        min_rr=1.3,
    )

    assert result["direction"] == "SHORT"
    assert result["execution_mode"] == "LIMIT"
    assert result["entry"] == 201
    assert result["risk_reward"] == 1.4


def test_valid_long_stop_entry_is_above_current_price():
    result = validate_decision(
        _long_trade(execution_mode="STOP", entry=101),
        current_price=98,
        atr14=5,
        min_rr=1.3,
    )

    assert result["execution_mode"] == "STOP"
    assert result["entry"] == 101
    assert result["risk_reward"] == 1.4


@pytest.mark.parametrize(
    ("payload", "current_price", "expected_entry", "expected_rr"),
    [
        (
            _long_trade(execution_mode="LIMIT", entry=101, tp1=109),
            105,
            101,
            1.6,
        ),
        (
            _long_trade(execution_mode="STOP", entry=101, tp1=109),
            98,
            101,
            1.6,
        ),
        (
            _short_trade(execution_mode="LIMIT", entry=201, tp1=191, tp2=187),
            198,
            201,
            2.0,
        ),
        (
            _short_trade(execution_mode="STOP", entry=201, tp1=191, tp2=187),
            205,
            201,
            2.0,
        ),
    ],
)
def test_pending_orders_use_directional_worst_fill(
    payload,
    current_price,
    expected_entry,
    expected_rr,
):
    result = validate_decision(
        payload,
        current_price=current_price,
        atr14=5,
    )

    assert result["entry"] == expected_entry
    assert result["risk_reward"] == expected_rr


@pytest.mark.parametrize("timeframe", ["5M", "15M", "1D", "WEEK"])
def test_only_macro_contract_timeframes_are_accepted(timeframe):
    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(_long_trade(timeframe=timeframe))

    assert any("timeframe" in error for error in captured.value.errors)


def test_trade_requires_exactly_three_distinct_evidence_items():
    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(_long_trade(evidence=["one", "two"]))

    assert captured.value.errors == ["TRADE requires exactly 3 concrete evidence items"]


def test_trade_requires_numeric_evidence_and_execution_conditions():
    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(
            _long_trade(
                evidence=["DAY trend is bullish", "4H structure broke up", "Volume expanded"],
                execution_condition="Enter in the active zone",
                time_stop="Exit after several bars",
                invalidation="Structure failure",
            )
        )

    assert any("numeric evidence" in error for error in captured.value.errors)


def test_position_size_cannot_exceed_configured_risk_cap():
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _long_trade(position_r=0.75),
            current_price=100,
            atr14=5,
            max_position_r=0.5,
        )

    assert any("exceeds max_position_r" in error for error in captured.value.errors)


def test_score_is_protocol_output_not_a_generic_algorithmic_gate():
    result = validate_decision(
        _long_trade(score=55),
        current_price=100,
        atr14=5,
    )

    assert result["status"] == "TRADE"
    assert result["score"] == 55


def test_macro_position_can_use_full_protocol_one_point_five_r_cap():
    result = validate_decision(
        _long_trade(position_r=1.5),
        current_price=100,
        atr14=5,
    )

    assert result["position_r"] == 1.5


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"stop_loss": 100}, "LONG prices must satisfy"),
        ({"entry": 102}, "entry must be inside"),
        ({"tp1": 100.5}, "LONG prices must satisfy"),
        ({"tp2": 107}, "LONG tp2 must be > tp1"),
    ],
)
def test_long_price_relationships_are_enforced(overrides, expected_error):
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _long_trade(**overrides),
            current_price=100,
            atr14=5,
        )

    assert any(expected_error in error for error in captured.value.errors)


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"stop_loss": 202}, "SHORT prices must satisfy"),
        ({"entry": 200}, "entry must be inside"),
        ({"tp1": 202}, "SHORT prices must satisfy"),
        ({"tp2": 195}, "SHORT tp2 must be < tp1"),
    ],
)
def test_short_price_relationships_are_enforced(overrides, expected_error):
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _short_trade(**overrides),
            current_price=198,
            atr14=4,
        )

    assert any(expected_error in error for error in captured.value.errors)


def test_rr_below_minimum_is_rejected():
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _long_trade(tp1=105),
            current_price=100,
            atr14=5,
            min_rr=1.5,
        )

    assert any(
        "risk/reward 1.2500 is below min_rr 1.5000" in error
        for error in captured.value.errors
    )


@pytest.mark.parametrize(
    ("stop_loss", "tp1", "tp2"),
    [
        (98.9, 108, 112),
        (80, 140, 150),
    ],
)
def test_atr_does_not_add_an_unwritten_universal_stop_distance_gate(
    stop_loss,
    tp1,
    tp2,
):
    result = validate_decision(
        _long_trade(stop_loss=stop_loss, tp1=tp1, tp2=tp2),
        current_price=100,
        atr14=5,
    )

    assert result["status"] == "TRADE"


@pytest.mark.parametrize("wording", ["等待确认后再说", "继续关注走势", "观察下一根K线"])
def test_no_trade_rejects_ambiguous_waiting_language(wording):
    payload = {
        "status": "NO_TRADE",
        "timeframe": None,
        "summary": "No executable setup",
        "rejection_reasons": [wording],
    }

    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(payload)

    assert any("ambiguous wording" in error for error in captured.value.errors)


def test_no_trade_requires_definitive_reasons_and_empty_trade_fields():
    payload = {
        "status": "NO_TRADE",
        "timeframe": "4H",
        "direction": "LONG",
        "summary": "No trade because reward is insufficient",
        "rejection_reasons": ["Maximum available TP1 reward is only 0.9R"],
    }

    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(payload)

    assert any(
        "trade fields must be empty: timeframe, direction" in error
        for error in captured.value.errors
    )


def test_valid_no_trade_has_a_definitive_rejection_reason():
    result = normalize_decision(
        {
            "status": "NO_TRADE",
            "timeframe": None,
            "summary": "Do not take this trade",
            "rejection_reasons": [
                "TP1 before DAY resistance provides only 0.8R, below the 1.5R minimum"
            ],
        }
    )

    assert result["status"] == "NO_TRADE"
    assert result["entry"] is None
    assert result["rejection_reasons"][0].startswith("TP1 before DAY resistance")


def test_no_trade_requires_timeframe_to_be_null():
    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(
            {
                "status": "NO_TRADE",
                "timeframe": "DAY",
                "summary": "Do not take this trade",
                "rejection_reasons": ["DAY structure is invalid below 95"],
            }
        )

    assert any(
        "trade fields must be empty: timeframe" in error
        for error in captured.value.errors
    )


@pytest.mark.parametrize(
    ("direction", "mode", "entry", "current_price", "expected_error"),
    [
        ("LONG", "NOW", 100, 102, "NOW requires current_price inside"),
        ("LONG", "LIMIT", 100, 99, "LONG LIMIT entry zone must be entirely below"),
        ("SHORT", "LIMIT", 202, 203, "SHORT LIMIT entry zone must be entirely above"),
        ("LONG", "STOP", 100, 101, "LONG STOP entry zone must be entirely above"),
        ("SHORT", "STOP", 202, 201, "SHORT STOP entry zone must be entirely below"),
    ],
)
def test_execution_mode_must_match_current_price(
    direction,
    mode,
    entry,
    current_price,
    expected_error,
):
    payload = (
        _long_trade(direction=direction, execution_mode=mode, entry=entry)
        if direction == "LONG"
        else _short_trade(direction=direction, execution_mode=mode, entry=entry)
    )

    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(payload, current_price=current_price, atr14=5)

    assert any(expected_error in error for error in captured.value.errors)


def test_data_error_has_only_reasons_and_does_not_require_market_values():
    result = validate_decision(
        {
            "status": "DATA_ERROR",
            "summary": "4H ATR is unavailable",
            "rejection_reasons": ["4H atr14 is null in the supplied snapshot"],
        },
        current_price=float("nan"),
        atr14=float("nan"),
    )

    assert result["status"] == "DATA_ERROR"
    assert result["direction"] is None
    assert result["rejection_reasons"] == ["4H atr14 is null in the supplied snapshot"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entry", float("nan")),
        ("stop_loss", float("inf")),
        ("tp1", -1),
    ],
)
def test_trade_prices_must_be_finite_and_positive(field, value):
    with pytest.raises(DecisionValidationError) as captured:
        normalize_decision(_long_trade(**{field: value}))

    assert any(field in error for error in captured.value.errors)


def test_errors_are_accumulated_for_llm_repair():
    with pytest.raises(DecisionValidationError) as captured:
        validate_decision(
            _long_trade(
                execution_mode="LIMIT",
                entry=102,
                stop_loss=102,
                tp1=101,
            ),
            current_price=100,
            atr14=0,
            min_rr=0,
        )

    errors = captured.value.errors
    assert len(errors) >= 4
    assert any("min_rr" in error for error in errors)
    assert any("entry must be inside" in error for error in errors)
    assert any("LONG LIMIT" in error for error in errors)
