"""The CARE score, implemented from the benchmark paper's definitions.

Gück, Roelofs and Faulstich, "CARE to Compare: A Real-World Benchmark Dataset for Early Fault
Detection in Wind Turbine Data", Data 2024, 9(12), 138. doi:10.3390/data9120138

Four components, combined as a weighted average:

    Coverage    F_0.5 over datapoints of each anomaly event.
    Accuracy    tn / (fp + tn) over datapoints of each normal event.
    Reliability F_0.5 over events, an event counting as detected when the criticality counter
                crosses 72 - twelve hours of accumulated anomalies at 10-minute resolution.
    Earliness   Weighted share of the anomaly window that was flagged, under a weight of 1 across
                the first half of the window falling linearly to 0 at its end.

    WA = (1*Coverage + 1*Earliness + 1*Reliability + 2*Accuracy) / 5

with two overrides from the paper: a run that detects nothing scores 0, and a run whose mean
Accuracy falls below 0.5 scores that Accuracy, because it is worse than random.

beta is 1/2 throughout, which weights precision above recall.

Two conventions the paper leaves to the implementer are fixed here and recorded in the published
artifact, because they move the numbers:
  * Only prediction-window rows are scored. Training rows are the model's own fitting data.
  * Within an anomaly event, a datapoint is positive when its timestamp falls inside
    [event_start, event_end]; prediction rows outside that window are negatives.
Rows whose status id is not normal operation are excluded before any of this, as the paper requires.

This is our implementation of the published definition, not a score returned by the benchmark's
own harness, and not a leaderboard submission.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BETA = 0.5
RELIABILITY_CRITICALITY_THRESHOLD = 72  # 12 h at 10-minute resolution, per the paper
WEIGHTS = {"coverage": 1.0, "earliness": 1.0, "reliability": 1.0, "accuracy": 2.0}


def fbeta(tp: int, fp: int, fn: int, beta: float = BETA) -> float:
    """F_beta as written in the paper: (1+b^2)tp / ((1+b^2)tp + b^2 fn + fp)."""
    b2 = beta * beta
    denominator = (1 + b2) * tp + b2 * fn + fp
    return float((1 + b2) * tp / denominator) if denominator else 0.0


def coverage(predictions: np.ndarray, truth: np.ndarray) -> float:
    """Datapoint-level F_0.5 for a single anomaly event."""
    predictions, truth = predictions.astype(bool), truth.astype(bool)
    tp = int(np.count_nonzero(predictions & truth))
    fp = int(np.count_nonzero(predictions & ~truth))
    fn = int(np.count_nonzero(~predictions & truth))
    return fbeta(tp, fp, fn)


def accuracy(predictions: np.ndarray) -> float:
    """Datapoint-level tn/(fp+tn) for a single normal event, where every row is truly negative."""
    predictions = predictions.astype(bool)
    fp = int(np.count_nonzero(predictions))
    tn = int(predictions.size - fp)
    return float(tn / (fp + tn)) if predictions.size else 0.0


def earliness_weights(times, event_start, event_end) -> np.ndarray:
    """1.0 across the first half of the anomaly window, falling linearly to 0.0 at its end.

    `times` is expected already typed as datetime64 (what the pipeline hands over); no string
    format is inferred here, so a mixed-format guess can never silently reorder the window.
    """
    span = (event_end - event_start).total_seconds()
    stamps = np.asarray(times, dtype="datetime64[ns]")
    if span <= 0:
        return np.zeros(stamps.size, dtype=float)
    elapsed = (stamps - np.datetime64(pd.Timestamp(event_start), "ns")).astype("timedelta64[s]").astype(float)
    position = np.clip(elapsed / span, 0.0, 1.0)
    return np.where(position <= 0.5, 1.0, np.clip(2.0 * (1.0 - position), 0.0, 1.0))


def earliness(predictions: np.ndarray, weights: np.ndarray) -> float:
    """Weighted share of the anomaly window that was flagged: sum(w*p)/sum(w)."""
    total = float(weights.sum())
    return float((weights * predictions.astype(float)).sum() / total) if total > 0 else 0.0


def reliability(detected: int, missed: int, false_alarm_events: int) -> float:
    """Event-level F_0.5: anomaly events detected against those missed and normal events alarmed."""
    return fbeta(detected, false_alarm_events, missed)


@dataclass(frozen=True)
class CareResult:
    care: float
    coverage: float
    accuracy: float
    reliability: float
    earliness: float
    rule: str

    def as_dict(self) -> dict:
        return {"care": round(self.care, 4), "coverage": round(self.coverage, 4),
                "accuracy": round(self.accuracy, 4), "reliability": round(self.reliability, 4),
                "earliness": round(self.earliness, 4), "rule": self.rule}


def care(coverage_mean: float, earliness_mean: float, reliability_value: float,
         accuracy_mean: float, detected_any: bool) -> CareResult:
    """Combine the four components, applying the paper's two overriding rules."""
    weighted = (WEIGHTS["coverage"] * coverage_mean + WEIGHTS["earliness"] * earliness_mean
                + WEIGHTS["reliability"] * reliability_value + WEIGHTS["accuracy"] * accuracy_mean) / sum(WEIGHTS.values())
    if not detected_any:
        return CareResult(0.0, coverage_mean, accuracy_mean, reliability_value, earliness_mean,
                          "no anomaly event detected, so the paper fixes the score at 0")
    if accuracy_mean < 0.5:
        return CareResult(accuracy_mean, coverage_mean, accuracy_mean, reliability_value, earliness_mean,
                          "mean accuracy below 0.5 is worse than random, so the paper returns accuracy")
    return CareResult(weighted, coverage_mean, accuracy_mean, reliability_value, earliness_mean,
                      "weighted average of the four components")
