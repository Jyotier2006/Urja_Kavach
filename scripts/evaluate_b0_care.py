"""B0: the static-threshold baseline, scored on real CARE events with the same CARE implementation as M2.

B0 keeps the definition the demo bundle already uses: a row is flagged when any monitored component
temperature falls outside the [0.5%, 99.5%] band of that event's own normal-operation training rows.
There is no model and nothing is learned from operating conditions - which is the point. It answers the
question a benchmark table should answer: does the LightGBM normal-behaviour model actually beat simply
watching a thermometer against its own history?

Everything else is held identical to scripts/train_care_nbm.py so the comparison is fair: the same event
set, the same not-assessable rule, the same running-hours filter, the same criticality counter, the same
smoothing window and the same CARE scoring conventions.

    python scripts/evaluate_b0_care.py A
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
from services.ml.normal_behavior import smooth_matrix
from scripts.train_care_nbm import CRITICALITY_SWEEP, MIN_OBSERVABLE_PREDICTION_ROWS, SMOOTHING_WINDOW, NotAssessable, first_crossing

BAND = (0.005, 0.995)  # the demo bundle's own B0 band


def evaluate_event(farm: str, event_id: int, schema, info_row, smoothing_window: int) -> dict:
    event = load_event(farm, event_id)
    status = event["status_type_id"].to_numpy()
    is_train_all = (event["train_test"] == "train").to_numpy()

    observable = np.flatnonzero(np.isin(status, list(NORMAL_STATUS_IDS)))
    observable_prediction = int((~is_train_all)[observable].sum()) if observable.size else 0
    if observable_prediction < MIN_OBSERVABLE_PREDICTION_ROWS:
        raise NotAssessable(f"only {observable_prediction} normal-operation rows in the prediction window")

    values = event[list(schema.targets)].to_numpy(dtype=float)[observable]
    is_prediction, is_train = (~is_train_all)[observable], is_train_all[observable]

    # The band comes only from normal-operation training rows, exactly as the alarm cut-off does for M2.
    training = values[is_train]
    if training.shape[0] < MIN_OBSERVABLE_PREDICTION_ROWS:
        raise NotAssessable(f"only {training.shape[0]} normal-operation training rows to set a band from")
    with np.errstate(all="ignore"):
        lower = np.nanquantile(training, BAND[0], axis=0)
        upper = np.nanquantile(training, BAND[1], axis=0)

    outside = (values < lower) | (values > upper)
    outside = np.where(np.isnan(values), False, outside)
    # Smoothed on the same window as M2 so persistence is treated the same way for both.
    smoothed = np.nan_to_num(smooth_matrix(outside.T.astype(float), smoothing_window), nan=0.0)
    flags = (smoothed.max(axis=0) > 0.5).tolist()

    counts = np.array(criticality_counter(flags, status=None, farm=farm, threshold=10**9)["criticality"])
    times = event["time_stamp"].iloc[observable]

    outcome = {
        "farm": farm, "event_id": int(event_id), "asset": int(event["asset_id"].iloc[0]),
        "label": info_row["event_label"], "rows": int(len(event)),
        "observable_prediction_rows": int(is_prediction.sum()),
        "targets": len(schema.targets),
        "peak_criticality_in_prediction": int(counts[is_prediction].max()),
        "event_start": info_row["event_start"].isoformat(),
        "event_end": info_row["event_end"].isoformat(),
        "alarms": {},
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

    predictions = np.asarray(flags, dtype=bool)[is_prediction]
    prediction_times = times.to_numpy()[is_prediction]
    if info_row["event_label"] == "anomaly":
        start, end = info_row["event_start"], info_row["event_end"]
        inside = (prediction_times >= np.datetime64(start)) & (prediction_times <= np.datetime64(end))
        outcome["care_components"] = {
            "coverage": round(coverage(predictions, inside), 4),
            "earliness": round(earliness(predictions, earliness_weights(prediction_times, start, end)), 4),
            "anomaly_rows_in_window": int(inside.sum()), "scored_rows": int(predictions.size),
        }
    else:
        outcome["care_components"] = {"accuracy": round(accuracy(predictions), 4), "scored_rows": int(predictions.size)}
    return outcome


def summarise(results: list[dict], farm: str, schema, runtime: float) -> dict:
    clean = [r for r in results if "alarms" in r]
    skipped = [r for r in results if "not_assessable" in r]
    anomalies = [r for r in clean if r["label"] == "anomaly"]
    normals = [r for r in clean if r["label"] == "normal"]
    sweep = {}
    for threshold in CRITICALITY_SWEEP:
        key = str(threshold)
        detected = [r for r in anomalies if r["alarms"][key]]
        warnings = [r["alarms"][key]["warning_days_before_event_end"] for r in detected]
        false_alarms = [r for r in normals if r["alarms"][key]]
        sweep[key] = {
            "detected": len(detected),
            "detection_rate": round(len(detected) / len(anomalies), 3) if anomalies else None,
            "detected_before_event_start": len([r for r in detected if r["alarms"][key]["before_event_start"]]),
            "warning_days_before_event_end_median": round(float(np.median(warnings)), 2) if warnings else None,
            "false_alarms": len(false_alarms),
            "false_alarm_rate": round(len(false_alarms) / len(normals), 3) if normals else None,
        }

    key = str(RELIABILITY_CRITICALITY_THRESHOLD)
    detected = [r for r in anomalies if r["alarms"][key]]
    coverages = [r["care_components"]["coverage"] for r in anomalies if "care_components" in r]
    earlinesses = [r["care_components"]["earliness"] for r in anomalies if "care_components" in r]
    accuracies = [r["care_components"]["accuracy"] for r in normals if "care_components" in r]
    scored = care(
        coverage_mean=float(np.mean(coverages)) if coverages else 0.0,
        earliness_mean=float(np.mean(earlinesses)) if earlinesses else 0.0,
        reliability_value=reliability(len(detected), len(anomalies) - len(detected),
                                      len([r for r in normals if r["alarms"][key]])),
        accuracy_mean=float(np.mean(accuracies)) if accuracies else 0.0,
        detected_any=bool(detected),
    )
    return {
        "farm": farm,
        "model": "B0 - static training-percentile threshold",
        "data": "real CARE To Compare v6",
        "definition": f"a row is flagged when any monitored temperature leaves the [{BAND[0]:.1%}, {BAND[1]:.1%}] band of that event's own normal-operation training rows",
        "caveat": "Baseline for comparison against M2. No model; nothing is learned from operating conditions.",
        "targets_per_event": len(schema.targets),
        "smoothing_window_rows": SMOOTHING_WINDOW,
        "events_evaluated": len(clean), "events_not_assessable": len(skipped),
        "events_errored": len(results) - len(clean) - len(skipped),
        "anomaly_events": len(anomalies), "normal_events": len(normals),
        "care_benchmark": {**scored.as_dict(), "beta": 0.5, "event_threshold": RELIABILITY_CRITICALITY_THRESHOLD,
                           "anomaly_events_scored": len(coverages), "normal_events_scored": len(accuracies),
                           "detected_events": len(detected), "missed_events": len(anomalies) - len(detected),
                           "false_alarm_events": len([r for r in normals if r["alarms"][key]])},
        "criticality_sweep": sweep,
        "runtime_seconds": round(runtime, 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("farm", choices=["A", "B", "C"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    schema = infer_schema(args.farm)
    info = load_event_info(args.farm).set_index("event_id")
    ids = event_ids(args.farm)[: args.limit]
    print(f"B0 baseline | farm {args.farm} | {len(ids)} events | {len(schema.targets)} targets", flush=True)

    results, started = [], time.time()
    for eid in ids:
        row = info.loc[eid]
        try:
            outcome = evaluate_event(args.farm, eid, schema, row, SMOOTHING_WINDOW)
            results.append(outcome)
            at72 = outcome["alarms"]["72"]
            verdict = f"alarm@72 {at72['warning_days_before_event_end']}d before end" if at72 else "no alarm@72"
            print(f"  event {eid:>3} [{outcome['label']:>7}] peak crit {outcome['peak_criticality_in_prediction']:>5} | {verdict}", flush=True)
        except NotAssessable as exc:
            results.append({"farm": args.farm, "event_id": int(eid), "label": row["event_label"], "not_assessable": str(exc)})
            print(f"  event {eid:>3} [{row['event_label']:>7}] not assessable: {exc}", flush=True)
        except Exception as exc:
            results.append({"farm": args.farm, "event_id": int(eid), "label": row["event_label"], "error": str(exc)})
            print(f"  event {eid:>3} FAILED: {exc}", flush=True)

    summary = summarise(results, args.farm, schema, time.time() - started)
    out = Path(__file__).resolve().parents[1] / "artifacts/metrics" / f"care_b0_wind_farm_{args.farm.lower()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "events": results}, indent=2, default=str))
    print(json.dumps(summary["care_benchmark"], indent=2))
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
