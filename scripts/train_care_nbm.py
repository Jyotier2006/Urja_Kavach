"""R2/D004/D009: train and evaluate the M2 temperature normal-behavior model on real CARE To Compare events.

Each event file is trained and scored independently; anonymized timestamps are never joined across events.

Method, stated plainly so the numbers can be read correctly:
  * One LightGBM model per monitored component temperature, fit only on normal-operation training rows.
  * The per-event alarm cut-off is calibrated on held-out normal training rows to a fixed baseline flag rate.
    No event label is used to choose it.
  * Flags feed the project's existing criticality counter. Because the counter series does not depend on the
    alarm threshold, the whole detection/false-alarm trade-off is reported as a sweep rather than a single
    hand-picked operating point.

This is our own normal-behavior approach evaluated on real CARE labels. It is not a reproduction of any
published CARE leaderboard score and must not be reported as one.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ml.care_loader import NORMAL_STATUS_IDS, event_ids, infer_schema, load_event, load_event_info
from services.ml.care_score import RELIABILITY_CRITICALITY_THRESHOLD, accuracy, care, coverage, earliness, earliness_weights, reliability
from services.ml.criticality import criticality_counter
from services.ml.normal_behavior import calibrate_threshold, reject_common_mode, score_event, score_matrix, smooth_matrix

BASELINE_FLAG_RATE = 0.01
# Smoothing makes flags far more persistent, so the counter now peaks in the hundreds rather than the tens and
# the sweep has to span that range to show where the trade-off actually turns. The whole curve is always
# published; the operating point is never chosen by looking at which threshold flatters the anomaly labels.
CRITICALITY_SWEEP = (12, 24, 48, 72, 144, 288, 432, 576, 864, 1152)
MIN_OBSERVABLE_PREDICTION_ROWS = 144  # one day of running hours at 10-minute resolution
SMOOTHING_WINDOW = 72  # twelve hours of running hours; a fault holds its drift, noise does not


def first_crossing(counts: np.ndarray, mask: np.ndarray, threshold: int) -> int | None:
    """First index inside the mask where the criticality counter rises through the threshold."""
    rising = (counts >= threshold) & (np.concatenate(([0], counts[:-1])) < threshold) & mask
    hits = np.flatnonzero(rising)
    return int(hits[0]) if hits.size else None


class NotAssessable(Exception):
    """The event cannot be judged for early detection, e.g. the turbine is down for the whole prediction window."""


def evaluate_event(farm: str, event_id: int, schema, info_row, baseline_flag_rate: float, smoothing_window: int, common_mode_rejection: bool = False) -> dict:
    event = load_event(farm, event_id)
    status = event["status_type_id"].to_numpy()
    is_train = (event["train_test"] == "train").to_numpy()
    is_prediction = ~is_train

    # Checked before any model is fit: with no running hours in the prediction window there is nothing to
    # detect from. Scoring downtime rows would only reveal that the turbine had already stopped.
    observable = np.flatnonzero(np.isin(status, list(NORMAL_STATUS_IDS)))
    observable_prediction = int(is_prediction[observable].sum()) if observable.size else 0
    if observable_prediction < MIN_OBSERVABLE_PREDICTION_ROWS:
        raise NotAssessable(f"only {observable_prediction} normal-operation rows in the prediction window")

    scores, meta = score_event(event, schema)

    # Everything below works in "running hours" space: downtime is dropped first so it can neither smear the
    # trend nor drain the counter through an outage.
    # Common-mode drift is removed before the trend is taken, so a warm season cannot masquerade as a slow fault.
    # Rows before the first full window have no trend yet, so they are made unflaggable rather than guessed at.
    matrix = reject_common_mode(score_matrix(scores, observable)) if common_mode_rejection else score_matrix(scores, observable)
    smoothed = np.nan_to_num(smooth_matrix(matrix, smoothing_window), nan=-np.inf)
    worst = smoothed.max(axis=0)
    is_prediction, is_train = is_prediction[observable], is_train[observable]
    z_threshold = calibrate_threshold(worst, is_train, baseline_flag_rate)
    flags = (worst > z_threshold).tolist()
    counts = np.array(criticality_counter(flags, status=None, farm=farm, threshold=10**9)["criticality"])
    times = event["time_stamp"].iloc[observable]
    hottest = scores[int(np.argmax(smoothed[:, is_prediction].max(axis=1)))]
    description = info_row.get("event_description")
    outcome = {
        "farm": farm, "event_id": int(event_id), "asset": int(event["asset_id"].iloc[0]),
        "label": info_row["event_label"], "description": description if pd.notna(description) else None,
        "rows": int(len(event)), "observable_prediction_rows": int(is_prediction.sum()), **meta,
        "z_threshold": round(z_threshold, 2),
        "peak_z_in_prediction": round(float(worst[is_prediction].max()), 2),
        "hottest_target": hottest.target,
        "peak_criticality_in_prediction": int(counts[is_prediction].max()),
        "event_start": info_row["event_start"].isoformat(),
        "event_end": info_row["event_end"].isoformat(),
        "alarms": {},
    }

    # CARE components for this event, from the same label-free flags the counter already uses.
    # Scored over prediction-window rows only; training rows are the model's own fitting data.
    predictions = np.asarray(flags, dtype=bool)[is_prediction]
    prediction_times = times.to_numpy()[is_prediction]
    if info_row["event_label"] == "anomaly":
        start, end = info_row["event_start"], info_row["event_end"]
        inside = (prediction_times >= np.datetime64(start)) & (prediction_times <= np.datetime64(end))
        outcome["care_components"] = {
            "coverage": round(coverage(predictions, inside), 4),
            "earliness": round(earliness(predictions, earliness_weights(prediction_times, start, end)), 4),
            "anomaly_rows_in_window": int(inside.sum()),
            "scored_rows": int(predictions.size),
        }
    else:
        outcome["care_components"] = {
            "accuracy": round(accuracy(predictions), 4),
            "scored_rows": int(predictions.size),
        }
    for threshold in CRITICALITY_SWEEP:
        index = first_crossing(counts, is_prediction, threshold)
        if index is None:
            outcome["alarms"][str(threshold)] = None
            continue
        alarm_time = times.iloc[index]
        outcome["alarms"][str(threshold)] = {
            "alarm_time": alarm_time.isoformat(),
            "before_event_start": bool(alarm_time <= info_row["event_start"]),
            "warning_days_before_event_end": round((info_row["event_end"] - alarm_time).total_seconds() / 86400, 2),
        }
    return outcome


def summarise(results: list[dict], farm: str, schema, baseline_flag_rate: float, smoothing_window: int, common_mode_rejection: bool, runtime: float) -> dict:
    clean = [r for r in results if "alarms" in r]
    skipped = [r for r in results if "not_assessable" in r]
    anomalies = [r for r in clean if r["label"] == "anomaly"]
    normals = [r for r in clean if r["label"] == "normal"]
    sweep = {}
    for threshold in CRITICALITY_SWEEP:
        key = str(threshold)
        detected = [r for r in anomalies if r["alarms"][key]]
        early = [r for r in detected if r["alarms"][key]["before_event_start"]]
        warnings = [r["alarms"][key]["warning_days_before_event_end"] for r in detected]
        false_alarms = [r for r in normals if r["alarms"][key]]
        sweep[key] = {
            "detected": len(detected),
            "detection_rate": round(len(detected) / len(anomalies), 3) if anomalies else None,
            "detected_before_event_start": len(early),
            "warning_days_before_event_end_median": round(float(np.median(warnings)), 2) if warnings else None,
            "false_alarms": len(false_alarms),
            "false_alarm_rate": round(len(false_alarms) / len(normals), 3) if normals else None,
        }
    # The CARE score, at the paper's own event threshold of 72 rather than our published operating point.
    key = str(RELIABILITY_CRITICALITY_THRESHOLD)
    detected = [r for r in anomalies if r["alarms"][key]]
    false_alarm_events = len([r for r in normals if r["alarms"][key]])
    coverages = [r["care_components"]["coverage"] for r in anomalies if "care_components" in r]
    earlinesses = [r["care_components"]["earliness"] for r in anomalies if "care_components" in r]
    accuracies = [r["care_components"]["accuracy"] for r in normals if "care_components" in r]
    scored = care(
        coverage_mean=float(np.mean(coverages)) if coverages else 0.0,
        earliness_mean=float(np.mean(earlinesses)) if earlinesses else 0.0,
        reliability_value=reliability(len(detected), len(anomalies) - len(detected), false_alarm_events),
        accuracy_mean=float(np.mean(accuracies)) if accuracies else 0.0,
        detected_any=bool(detected),
    )
    care_benchmark = {
        **scored.as_dict(),
        "beta": 0.5,
        "event_threshold": RELIABILITY_CRITICALITY_THRESHOLD,
        "anomaly_events_scored": len(coverages),
        "normal_events_scored": len(accuracies),
        "detected_events": len(detected),
        "missed_events": len(anomalies) - len(detected),
        "false_alarm_events": false_alarm_events,
        "definition": "Guck, Roelofs and Faulstich, Data 2024, 9(12), 138; doi:10.3390/data9120138",
        "caveat": ("Our implementation of the published definition, computed on the events this project could "
                   "assess. Not a score returned by the benchmark's own harness and not a leaderboard entry."),
    }

    return {
        "farm": farm,
        "model": "M2 - LightGBM temperature normal-behavior models",
        "data": "real CARE To Compare v6",
        "care_benchmark": care_benchmark,
        "caveat": "Own normal-behavior approach evaluated on real CARE labels; not a published CARE benchmark score.",
        "targets_per_event": len(schema.targets),
        "baseline_flag_rate": baseline_flag_rate,
        "smoothing_window_rows": smoothing_window,
        "common_mode_rejection": common_mode_rejection,
        "threshold_selection": "z cut-off calibrated per event on held-out normal training rows; no event labels used",
        "events_evaluated": len(clean),
        "events_not_assessable": len(skipped),
        "not_assessable_reason": "turbine not in normal operation during the prediction window, so no early-detection signal exists to measure",
        "events_not_assessable_anomaly": len([r for r in skipped if r["label"] == "anomaly"]),
        "events_errored": len(results) - len(clean) - len(skipped),
        "anomaly_events": len(anomalies),
        "normal_events": len(normals),
        "criticality_sweep": sweep,
        "runtime_seconds": round(runtime, 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("farm", choices=["A", "B", "C"])
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N events")
    parser.add_argument("--baseline-flag-rate", type=float, default=BASELINE_FLAG_RATE)
    parser.add_argument("--smoothing-window", type=int, default=SMOOTHING_WINDOW)
    parser.add_argument("--common-mode-rejection", action="store_true", help="ablation: subtract the across-sensor median (measured worse, see D014)")
    args = parser.parse_args()

    schema = infer_schema(args.farm)
    info = load_event_info(args.farm).set_index("event_id")
    ids = event_ids(args.farm)[: args.limit]
    print(f"{schema.describe()} | {len(ids)} events | baseline flag rate {args.baseline_flag_rate} | smoothing {args.smoothing_window}", flush=True)

    results, started = [], time.time()
    for eid in ids:
        row = info.loc[eid]
        try:
            outcome = evaluate_event(args.farm, eid, schema, row, args.baseline_flag_rate, args.smoothing_window, args.common_mode_rejection)
            results.append(outcome)
            at72 = outcome["alarms"]["72"]
            verdict = f"alarm@72 {at72['warning_days_before_event_end']}d before end" if at72 else "no alarm@72"
            print(f"  event {eid:>3} [{outcome['label']:>7}] peak crit {outcome['peak_criticality_in_prediction']:>4} | {verdict} | {outcome['hottest_target']}", flush=True)
        except NotAssessable as exc:
            results.append({"farm": args.farm, "event_id": int(eid), "label": row["event_label"], "not_assessable": str(exc)})
            print(f"  event {eid:>3} [{row['event_label']:>7}] not assessable: {exc}", flush=True)
        except Exception as exc:
            results.append({"farm": args.farm, "event_id": int(eid), "label": row["event_label"], "error": str(exc)})
            print(f"  event {eid:>3} FAILED: {exc}", flush=True)

    summary = summarise(results, args.farm, schema, args.baseline_flag_rate, args.smoothing_window, args.common_mode_rejection, time.time() - started)
    out_dir = Path(__file__).resolve().parents[1] / "artifacts/metrics"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_cmr" if args.common_mode_rejection else ""
    out_path = out_dir / f"care_m2_wind_farm_{args.farm.lower()}{suffix}.json"
    out_path.write_text(json.dumps({"summary": summary, "events": results}, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
