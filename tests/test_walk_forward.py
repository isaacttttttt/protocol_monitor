from app.backtest.walk_forward import run_walk_forward


def test_walk_forward_never_selects_parameters_on_test_data():
    observations = [1, 1, 1, -10, -10]
    folds = run_walk_forward(
        observations,
        [1, -1],
        train_size=3,
        test_size=2,
        score=lambda rows, parameter: sum(rows) * parameter,
        evaluate=lambda rows, parameter: sum(rows) * parameter,
    )

    assert folds[0].selected_parameter == 1
    assert folds[0].test_evaluation == -20
