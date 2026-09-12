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
import hashlib
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


def load_farms(prefix: str = "care_m2_wind_farm") -> list[dict]:
    farms = []
    for farm in FARMS:
        path = METRICS / f"{prefix}_{farm}.json"
        if not path.exists(): continue
        farms.append(json.loads(path.read_text())["summary"])
    return farms


def load_events(prefix: str = "care_m2_wind_farm") -> list[dict]:
    events = []
    for farm in FARMS:
        path = METRICS / f"{prefix}_{farm}.json"
        if not path.exists(): continue
        events.extend(json.loads(path.read_text())["events"])
    return events


def mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def care_benchmark(farms: list[dict], events: list[dict]) -> dict:
    """The CARE score over every scored event, pooled rather than averaged per farm.

    Shared by M2 and the B0 baseline so both are scored by identical code."""
    if not farms:
        return {"computed": False, "reason": "No per-farm evaluation files found for this model."}
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
        # Kept per farm as well as pooled: farm A scores zero under the paper's own no-detection rule,
        # and a single pooled figure would bury that.
        "per_farm": [{"farm": f["farm"], **{k: f["care_benchmark"][k] for k in
                      ("care", "coverage", "accuracy", "reliability", "earliness")}}
                     for f in farms if "care_benchmark" in f],
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


def baseline_at(farms: list[dict], key: str) -> dict:
    """B0's detections and false alarms at the same operating point M2 is published on."""
    if not farms: return {"computed": False}
    return {
        "computed": True, "model": "B0 - static training-percentile threshold",
        "criticality_threshold": int(key),
        "detected": sum(f["criticality_sweep"][key]["detected"] for f in farms),
        "anomaly_events": sum(f["anomaly_events"] for f in farms),
        "false_alarms": sum(f["criticality_sweep"][key]["false_alarms"] for f in farms),
        "normal_events": sum(f["normal_events"] for f in farms),
    }


def set_m2_status(target: Path, status: str) -> None:
    """Keep the model table's own status line in step with what was actually computed."""
    for name in ("metrics.json", "bundle.json"):
        path = target / name
        if not path.exists(): continue
        data = json.loads(path.read_text())
        block = data if name == "metrics.json" else data.get("metrics", {})
        for model in block.get("care", {}).get("models", []):
            if model.get("id") == "M2": model["status"] = status
        path.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))


def refresh_manifest(target: Path) -> None:
    """care-evaluation.json is hash-tracked, so rewriting it without this leaves the published
    hashes wrong - and the Performance page invites the reader to check them."""
    path = target / "manifest.json"
    if not path.exists(): return
    manifest = json.loads(path.read_text())
    for tracked in manifest.get("files", {}):
        f = target / tracked
        if f.exists():
            manifest["files"][tracked] = hashlib.sha256(f.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest, separators=(",", ":"), allow_nan=False))


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
        # The static-threshold baseline, scored identically. Without it the M2 row has nothing to beat.
        "care_benchmark_b0": care_benchmark(load_farms("care_b0_wind_farm"), load_events("care_b0_wind_farm")),
        # The same baseline at the deployable operating point. The CARE score is taken at the paper's
        # threshold of 72, where both models alarm on over half the healthy events and the gap between
        # them compresses; the separation shows at the threshold an operator would actually run.
        "baseline_at_operating_point": baseline_at(load_farms("care_b0_wind_farm"), key),
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
    benchmark = artifact["care_benchmark"]
    status = ("Measured on real CARE events; CARE score computed from the published definition"
              if benchmark.get("computed") else
              "Measured on real CARE events (see above); benchmark quantities not computed")
    for target in TARGETS:
        target.mkdir(parents=True, exist_ok=True)
        (target / "care-evaluation.json").write_text(encoded)
        set_m2_status(target, status)
        refresh_manifest(target)

    models_path = METRICS / "models.json"
    if models_path.exists():
        models = json.loads(models_path.read_text())
        for model in models.get("care", {}).get("models", []):
            if model.get("id") == "M2": model["status"] = status
        models_path.write_text(json.dumps(models, indent=2))

    print(json.dumps(artifact["totals"] | {"threshold": int(key)}, indent=2))
    print(json.dumps(benchmark, indent=2))


if __name__ == "__main__":
    main()
