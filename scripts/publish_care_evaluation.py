"""R2/D010-D015: publish the measured M2 CARE results as a web artifact.

Reads the per-farm evaluation files written by train_care_nbm.py and emits one compact artifact the site can
render. Nothing is computed here beyond aggregation; this only reshapes measured numbers.

One threshold is chosen for all farms, not a per-farm best. A per-farm tuned threshold flatters the result,
because in service you deploy a single alarm level and discover the rest. The threshold is picked on the
healthy events alone, which is how an alarm level is set on a known-good fleet; the fault labels never vote.

The CARE score itself is computed separately and reported alongside, under the benchmark's own definition
at its own event threshold of 72, rather than at the operating point chosen above. Components are pooled
over every scored event rather than averaged per farm, because the farms hold very different event counts.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ml.care_score import RELIABILITY_CRITICALITY_THRESHOLD, care, reliability

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "artifacts/metrics"
TARGETS = [ROOT / "artifacts/demo_bundle", ROOT / "apps/web/public/demo"]
FARMS = ("a", "b", "c")
FALSE_ALARM_BUDGET = 0.10


def load_farms() -> list[dict]:
    farms = []
    for farm in FARMS:
        path = METRICS / f"care_m2_wind_farm_{farm}.json"
        if not path.exists(): continue
        summary = json.loads(path.read_text())["summary"]
        farms.append(summary)
    return farms


def load_events() -> list[dict]:
    events = []
    for farm in FARMS:
        path = METRICS / f"care_m2_wind_farm_{farm}.json"
        if not path.exists(): continue
        events.extend(json.loads(path.read_text())["events"])
    return events


def mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def care_benchmark(farms: list[dict], events: list[dict]) -> dict:
    """The CARE score over every scored event, pooled rather than averaged per farm."""
    coverages = [e["care_components"]["coverage"] for e in events
                 if e.get("care_components") and e["label"] == "anomaly"]
    earlinesses = [e["care_components"]["earliness"] for e in events
                   if e.get("care_components") and e["label"] == "anomaly"]
    accuracies = [e["care_components"]["accuracy"] for e in events
                  if e.get("care_components") and e["label"] == "normal"]
    if not coverages and not accuracies:
        return {"computed": False, "reason": "No per-event CARE components found. Re-run scripts/train_care_nbm.py."}

    key = str(RELIABILITY_CRITICALITY_THRESHOLD)
    detected = sum(f["criticality_sweep"][key]["detected"] for f in farms)
    anomalies = sum(f["anomaly_events"] for f in farms)
    false_alarm_events = sum(f["criticality_sweep"][key]["false_alarms"] for f in farms)
    scored = care(
        coverage_mean=mean(coverages), earliness_mean=mean(earlinesses),
        reliability_value=reliability(detected, anomalies - detected, false_alarm_events),
        accuracy_mean=mean(accuracies), detected_any=detected > 0,
    )
    # An anomaly event whose turbine never runs inside its own labelled window offers no positive
    # datapoint to find, so its coverage and earliness are structurally zero. Reported, not hidden.
    empty_windows = len([e for e in events if e.get("care_components")
                         and e["label"] == "anomaly" and e["care_components"].get("anomaly_rows_in_window") == 0])
    return {
        "computed": True, **scored.as_dict(),
        "beta": 0.5, "event_threshold": RELIABILITY_CRITICALITY_THRESHOLD,
        "anomaly_events_scored": len(coverages), "normal_events_scored": len(accuracies),
        "detected_events": detected, "missed_events": anomalies - detected,
        "false_alarm_events": false_alarm_events,
        "anomaly_events_with_no_running_rows_in_window": empty_windows,
        "definition": "Guck, Roelofs and Faulstich, Data 2024, 9(12), 138; doi:10.3390/data9120138",
        "conventions": [
            "Only prediction-window rows are scored; training rows are the model's own fitting data.",
            "Rows outside normal operating status are excluded, as the benchmark requires.",
            "Within an anomaly event a row is positive when it falls inside [event_start, event_end].",
            f"Reliability counts an event detected when criticality crosses {RELIABILITY_CRITICALITY_THRESHOLD}, the paper's own threshold.",
        ],
        "caveat": ("Our implementation of the published definition, over the events this project could assess. "
                   "Not a score returned by the benchmark's own harness and not a leaderboard entry."),
    }


def choose_threshold(farms: list[dict]) -> str | None:
    """Most detections among thresholds whose false-alarm rate over all healthy events stays inside the budget."""
    thresholds = farms[0]["criticality_sweep"].keys()
    viable = []
    for key in thresholds:
        false_alarms = sum(f["criticality_sweep"][key]["false_alarms"] for f in farms)
        normals = sum(f["normal_events"] for f in farms)
        if normals and false_alarms / normals > FALSE_ALARM_BUDGET: continue
        detected = sum(f["criticality_sweep"][key]["detected"] for f in farms)
        viable.append((detected, -int(key), key))
    return max(viable)[2] if viable else None


def main():
    farms = load_farms()
    if not farms: raise SystemExit("No per-farm evaluation files found. Run scripts/train_care_nbm.py first.")
    key = choose_threshold(farms)
    if key is None: raise SystemExit("No threshold in the swept range meets the false-alarm budget.")

    per_farm, warnings = [], []
    for f in farms:
        point = f["criticality_sweep"][key]
        if point["warning_days_before_event_end_median"] is not None:
            warnings.append(point["warning_days_before_event_end_median"])
        per_farm.append({
            "farm": f["farm"],
            "anomaly_events": f["anomaly_events"],
            "normal_events": f["normal_events"],
            "events_not_assessable": f["events_not_assessable"],
            "targets_per_event": f["targets_per_event"],
            "at_operating_point": point,
            "sweep": f["criticality_sweep"],
        })

    detected = sum(f["criticality_sweep"][key]["detected"] for f in farms)
    anomalies = sum(f["anomaly_events"] for f in farms)
    false_alarms = sum(f["criticality_sweep"][key]["false_alarms"] for f in farms)
    normals = sum(f["normal_events"] for f in farms)
    artifact = {
        "model": "M2 - LightGBM temperature normal-behavior models",
        "data": "CARE To Compare v6 (real wind turbine SCADA)",
        "provenance": "Measured",
        "caveat": "Our own normal-behavior evaluation on real CARE labels, reported at the operating point below. The CARE score itself is computed separately, under the benchmark's definition.",
        "care_benchmark": care_benchmark(farms, load_events()),
        "operating_point": {
            "criticality_threshold": int(key),
            "selection": f"one threshold for every farm, the most sensitive whose false-alarm rate over all healthy events stays under {int(FALSE_ALARM_BUDGET * 100)}%. Chosen on healthy events only; fault labels never vote.",
        },
        "method": [
            "One model per monitored component temperature, predicting it from operating conditions alone.",
            "Fit only on normal-operation training rows, with the predicted signal held out of its own predictors.",
            "Alarm cut-off calibrated on held-out normal training rows; no event label takes part.",
            "Evidence accumulates only while the turbine is running, so an outage cannot stand in for a detection.",
        ],
        "farms": per_farm,
        "totals": {
            "farms_evaluated": [f["farm"] for f in farms],
            "anomaly_events": anomalies,
            "normal_events": normals,
            "events_not_assessable": sum(f["events_not_assessable"] for f in farms),
            "detected": detected,
            "detection_rate": round(detected / anomalies, 3) if anomalies else None,
            "false_alarms": false_alarms,
            "false_alarm_rate": round(false_alarms / normals, 3) if normals else None,
            "warning_days_median_best_farm": max(warnings) if warnings else None,
        },
    }

    encoded = json.dumps(artifact, separators=(",", ":"), allow_nan=False)
    for target in TARGETS:
        target.mkdir(parents=True, exist_ok=True)
        (target / "care-evaluation.json").write_text(encoded)
    print(json.dumps(artifact["totals"] | {"threshold": int(key)}, indent=2))


if __name__ == "__main__":
    main()
