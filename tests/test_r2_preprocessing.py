from qtrad.application.r2_preprocessing import TrainingRow, select_chronological_alpha
from qtrad.domain.r2_models import FitDisposition


def _rows(
    validation_target: float = 4.0, validation_feature: float = 4.0
) -> tuple[TrainingRow, ...]:
    return (
        *(
            TrainingRow(
                f"r{index}",
                (float(index), None if index == 2 else float(index)),
                float(index),
            )
            for index in range(4)
        ),
        TrainingRow("r4", (validation_feature, 99.0), validation_target),
        TrainingRow("r5", (validation_feature + 1, 100.0), validation_target + 1),
    )


def _select(rows: tuple[TrainingRow, ...]):
    return select_chronological_alpha(
        rows,
        feature_names=("signal", "missing"),
        alpha_grid=(0.1, 1.0, 10.0),
        minimum_training_rows=3,
        minimum_inner_validation_rows=2,
    )


def test_preprocessing_and_alpha_use_inner_training_only() -> None:
    baseline = _select(_rows())
    mutated = _select(_rows(validation_target=400.0, validation_feature=400.0))
    assert baseline.disposition is FitDisposition.READY
    assert baseline.inner_preprocessing == mutated.inner_preprocessing
    assert baseline.outer_preprocessing != mutated.outer_preprocessing
    assert baseline.inner_validation_row_ids == ("r4", "r5")


def test_alpha_selection_is_deterministic_and_larger_alpha_breaks_ties() -> None:
    rows = tuple(TrainingRow(f"r{index}", (1.0,), 1.0 + index % 2) for index in range(6))
    result = select_chronological_alpha(
        rows,
        feature_names=("constant",),
        alpha_grid=(0.1, 1.0),
        minimum_training_rows=3,
        minimum_inner_validation_rows=2,
    )
    assert result.disposition is FitDisposition.DEGENERATE_FEATURE_MATRIX
    selected = _select(tuple(reversed(_rows())))
    assert selected == _select(_rows())


def test_insufficient_and_constant_target_fail_closed() -> None:
    insufficient = select_chronological_alpha(
        _rows()[:3],
        feature_names=("a", "b"),
        alpha_grid=(1.0,),
        minimum_training_rows=3,
        minimum_inner_validation_rows=2,
    )
    constant = select_chronological_alpha(
        tuple(TrainingRow(f"r{index}", (float(index),), 1.0) for index in range(6)),
        feature_names=("a",),
        alpha_grid=(1.0,),
        minimum_training_rows=3,
        minimum_inner_validation_rows=2,
    )
    assert insufficient.disposition is FitDisposition.INSUFFICIENT_TRAINING
    assert constant.disposition is FitDisposition.DEGENERATE_TARGET


def test_equal_inner_losses_choose_larger_alpha() -> None:
    rows = (
        TrainingRow("r0", (1.0,), 0.0),
        TrainingRow("r1", (2.0,), 1.0),
        TrainingRow("r2", (3.0,), 2.0),
        TrainingRow("r3", (4.0,), 3.0),
        TrainingRow("r4", (0.0,), 1.5),
        TrainingRow("r5", (0.0,), 1.5),
    )
    result = select_chronological_alpha(
        rows,
        feature_names=("signal",),
        alpha_grid=(0.1, 1.0, 10.0),
        minimum_training_rows=3,
        minimum_inner_validation_rows=2,
    )
    assert result.disposition is FitDisposition.READY
    assert result.selected_alpha == 10.0
