from app.services.decision_service import DecisionService


def test_below_unknown_threshold_is_unknown() -> None:
    assert DecisionService(0.60, 0.08).decide([0.599]).decision == "unknown"


def test_equal_unknown_threshold_is_not_unknown() -> None:
    assert DecisionService(0.60, 0.08).decide([0.60]).decision == "matched"


def test_margin_below_threshold_is_ambiguous() -> None:
    assert DecisionService(0.60, 0.08).decide([0.90, 0.821]).decision == "ambiguous"


def test_margin_equal_threshold_is_matched() -> None:
    result = DecisionService(0.60, 0.08).decide([0.90, 0.82])
    assert result.decision == "matched"
    assert abs(result.margin - 0.08) < 1e-7


def test_clear_match() -> None:
    assert DecisionService(0.60, 0.08).decide([0.91, 0.63]).decision == "matched"


def test_empty_index() -> None:
    result = DecisionService(0.60, 0.08).decide([])
    assert result.decision == "empty_index"
    assert result.margin is None


def test_single_candidate_has_no_margin() -> None:
    result = DecisionService(0.60, 0.08).decide([0.80])
    assert result.decision == "matched"
    assert result.top2_score is None
    assert result.margin is None

