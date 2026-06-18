"""Tests for evals.stats.wilson_interval.

Run from the project root: python -m pytest tests/test_stats.py
"""

import pytest

from evals.stats import mcnemar_exact_p, wilson_interval


def test_half_is_symmetric():
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=0.005)
    assert high == pytest.approx(0.5962, abs=0.005)
    # symmetric around 0.5
    assert (low + high) == pytest.approx(1.0, abs=1e-6)


def test_zero_successes():
    low, high = wilson_interval(0, 10)
    assert 0.0 <= low < 0.01
    assert high == pytest.approx(0.2775, abs=0.005)


def test_all_successes():
    low, high = wilson_interval(10, 10)
    assert low == pytest.approx(0.7224, abs=0.01)
    assert high > 0.99  # upper bound hugs 1 but need not equal it


def test_total_zero_returns_none():
    assert wilson_interval(0, 0) is None


def test_interior_intervals_contain_point_estimate():
    for passed, total in [(1, 3), (7, 9), (30, 50), (3, 10)]:
        low, high = wilson_interval(passed, total)
        rate = passed / total
        assert 0.0 <= low <= rate <= high <= 1.0


def test_intervals_stay_within_unit():
    for passed, total in [(0, 1), (1, 1), (0, 5), (5, 5), (1, 7), (99, 100)]:
        low, high = wilson_interval(passed, total)
        assert 0.0 <= low <= high <= 1.0


def test_larger_n_gives_narrower_interval():
    small = wilson_interval(5, 10)
    large = wilson_interval(50, 100)  # same rate, more data
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_invalid_passed_raises():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)


def test_mcnemar_no_discordant_is_one():
    assert mcnemar_exact_p(0, 0) == 1.0
    assert mcnemar_exact_p(7, 0) == pytest.approx(2 * 0.5**7)  # all one-directional


def test_mcnemar_symmetric_is_nonsignificant():
    assert mcnemar_exact_p(5, 5) == 1.0
    assert mcnemar_exact_p(3, 4) == mcnemar_exact_p(4, 3)  # symmetric in b, c


def test_mcnemar_lopsided_is_small():
    p = mcnemar_exact_p(0, 10)
    assert p == pytest.approx(2 * 0.5**10, abs=1e-6)
    assert p < 0.05


def test_mcnemar_in_unit_interval():
    for b, c in [(0, 0), (1, 1), (0, 1), (10, 2), (3, 3), (20, 0)]:
        assert 0.0 <= mcnemar_exact_p(b, c) <= 1.0
