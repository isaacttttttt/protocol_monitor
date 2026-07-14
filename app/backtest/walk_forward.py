from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Sequence, TypeVar

Parameter = TypeVar("Parameter")
Evaluation = TypeVar("Evaluation")


@dataclass(frozen=True)
class WalkForwardFold(Generic[Parameter, Evaluation]):
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    selected_parameter: Parameter
    train_score: float
    test_evaluation: Evaluation


def run_walk_forward(
    observations: Sequence[object],
    parameters: Sequence[Parameter],
    *,
    train_size: int,
    test_size: int,
    score: Callable[[Sequence[object], Parameter], float],
    evaluate: Callable[[Sequence[object], Parameter], Evaluation],
) -> list[WalkForwardFold[Parameter, Evaluation]]:
    """Select parameters only on each training window, then freeze for test."""
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    if not parameters:
        raise ValueError("at least one parameter set is required")
    folds: list[WalkForwardFold[Parameter, Evaluation]] = []
    test_start = train_size
    while test_start + test_size <= len(observations):
        train_start = test_start - train_size
        train = observations[train_start:test_start]
        ranked = [(score(train, parameter), parameter) for parameter in parameters]
        train_score, selected = max(ranked, key=lambda item: item[0])
        test_end = test_start + test_size
        folds.append(
            WalkForwardFold(
                train_start=train_start,
                train_end=test_start,
                test_start=test_start,
                test_end=test_end,
                selected_parameter=selected,
                train_score=train_score,
                test_evaluation=evaluate(observations[test_start:test_end], selected),
            )
        )
        test_start = test_end
    return folds
