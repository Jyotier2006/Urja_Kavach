"""Guards for the CARE score implementation in services/ml/care_score.py.

These pin the published definition rather than our own behaviour, so a drift in the formulas is
caught before any number reaches the Performance page.
"""
import numpy as np
import pandas as pd
import pytest

from services.ml import care_score as cs


def test_fbeta_matches_the_published_formula():
    assert cs.fbeta(tp=1, fp=0, fn=0) == pytest.approx(1.0)
    assert cs.fbeta(tp=0, fp=1, fn=0) == pytest.approx(0.0)
    assert cs.fbeta(tp=0, fp=0, fn=0) == 0.0, 'an empty event must not divide by zero'
    # 1.25*1 / (1.25*1 + 0.25*0 + 1)
    assert cs.fbeta(tp=1, fp=1, fn=0) == pytest.approx(1.25 / 2.25)


def test_beta_of_one_half_punishes_a_false_alarm_harder_than_a_miss():
    """The paper chooses beta=1/2 to weight precision above recall; this is what that means."""
    one_false_positive = cs.fbeta(tp=4, fp=1, fn=0)
    one_false_negative = cs.fbeta(tp=4, fp=0, fn=1)
    assert one_false_positive < one_false_negative


def test_coverage_rewards_flagging_the_anomaly_window():
    truth = np.array([False, False, True, True])
    assert cs.coverage(np.array([False, False, True, True]), truth) == pytest.approx(1.0)
    assert cs.coverage(np.array([False, False, False, False]), truth) == pytest.approx(0.0)
    # Flagging everything is punished, because the two quiet rows are false positives.
    assert cs.coverage(np.array([True, True, True, True]), truth) < 1.0


def test_accuracy_is_the_unflagged_share_of_a_normal_event():
    assert cs.accuracy(np.zeros(10, dtype=bool)) == pytest.approx(1.0)
    assert cs.accuracy(np.ones(10, dtype=bool)) == pytest.approx(0.0)
    predictions = np.array([True, True] + [False] * 8)
    assert cs.accuracy(predictions) == pytest.approx(0.8)


def test_earliness_weights_hold_at_one_then_fall_to_zero():
    start, end = pd.Timestamp('2020-01-01'), pd.Timestamp('2020-01-11')  # ten days
    # Start, halfway, three quarters through (7.5 days in), and the very end.
    times = np.array(['2020-01-01T00:00', '2020-01-06T00:00', '2020-01-08T12:00', '2020-01-11T00:00'],
                     dtype='datetime64[ns]')
    weights = cs.earliness_weights(times, start, end)
    assert weights[0] == pytest.approx(1.0), 'start of the window'
    assert weights[1] == pytest.approx(1.0), 'halfway is still full weight'
    assert weights[2] == pytest.approx(0.5, abs=1e-2), 'three quarters through'
    assert weights[3] == pytest.approx(0.0), 'no credit for flagging only at the end'


def test_earliness_prefers_an_early_flag_to_a_late_one():
    start, end = pd.Timestamp('2020-01-01'), pd.Timestamp('2020-01-11')
    times = pd.date_range(start, end, periods=11).to_numpy()
    weights = cs.earliness_weights(times, start, end)
    early = np.array([i < 5 for i in range(11)])
    late = np.array([i >= 6 for i in range(11)])
    assert cs.earliness(early, weights) > cs.earliness(late, weights)
    assert cs.earliness(np.zeros(11, dtype=bool), weights) == pytest.approx(0.0)


def test_reliability_is_event_level_fbeta():
    assert cs.reliability(detected=6, missed=0, false_alarm_events=0) == pytest.approx(1.0)
    assert cs.reliability(detected=0, missed=4, false_alarm_events=0) == pytest.approx(0.0)


def test_care_weights_accuracy_double():
    perfect = cs.care(1.0, 1.0, 1.0, 1.0, detected_any=True)
    assert perfect.care == pytest.approx(1.0)
    # (1*0 + 1*0 + 1*0 + 2*1) / 5
    accuracy_only = cs.care(0.0, 0.0, 0.0, 1.0, detected_any=True)
    assert accuracy_only.care == pytest.approx(0.4)


def test_the_papers_two_overriding_rules():
    nothing_found = cs.care(0.9, 0.9, 0.9, 0.9, detected_any=False)
    assert nothing_found.care == 0.0 and 'fixes the score at 0' in nothing_found.rule

    worse_than_random = cs.care(1.0, 1.0, 1.0, 0.4, detected_any=True)
    assert worse_than_random.care == pytest.approx(0.4), 'the score collapses to accuracy'
    assert 'worse than random' in worse_than_random.rule
