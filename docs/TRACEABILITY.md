# Requirement traceability

Maps each clause of the problem statement to what is implemented and how strong the evidence is.
Status is deliberately conservative: **Measured** means a number read from a committed evaluation
artifact, **Working** means exercised by an automated test, **Partial** means the code path exists
but has not been run end to end.

## Problem statement clauses

| Clause | Implementation | Status |
|---|---|---|
| Use sensor data — **temperature** | Component temperatures are the modelled target. M2 LightGBM normal-behaviour models on real CARE SCADA; measured-vs-expected, residual and criticality on the investigation screen | **Measured** |
| Use sensor data — **vibration** | `vibration` channel (mm/s, main bearing) generated per scenario and charted on the investigation screen. Rises 1.10 → 3.10 in the fault scenario, flat at 1.10 in the normal one | Simulated, and excluded from every reported model metric |
| Use sensor data — **current** | `phase_current` (A, grid subsystem) charted against `rotor_rpm` on the investigation screen | Simulated |
| Use sensor data — **panel soiling** | `Soiling` is one of 12 classes in the trained infrared classifier; solar screen adds an inverter heatmap and cleaning economics | **Measured** (classifier); solar series simulated |
| Detect early signs of degradation or failure | M2 temperature normal-behaviour models plus a persistence-based criticality counter; IRNet CNN for module defects | **Measured** — 14/37 CARE anomaly events at 4/50 false alarms; IR macro-F1 0.6075 |
| Flag at-risk assets | Fleet health states, attention list, alert centre, acknowledgement and unacknowledged-warning escalation | **Working** |
| Prioritize maintenance visits | OR-Tools CP-SAT scheduler over skills, shifts, parts readiness, access windows, job locks and inter-group travel | **Working** — 384 computed cases, 8.39 ms median solve |
| Estimate energy/revenue loss of inaction | Monte Carlo loss domain comparing repair now / delay / no action, over editable assumptions with P10–P90 bands | **Working** — bands describe assumption uncertainty, not predictive confidence |

## Users

| User | Surface | Status |
|---|---|---|
| Solar/wind farm operators | Fleet overview, investigation, maintenance planner, solar operations | **Working** |
| Maintenance technicians | Technician workspace: assigned jobs, validated checklist lifecycle, field notes, infrared inspection, field-report export | **Working** |
| Asset management companies | Asset manager portfolio view, risk concentration, editable assumptions, performance evidence | **Working** |

Role switching changes the workspace view. It is a demonstration control, not authentication.

## Named technologies

| Technology | Implementation | Status |
|---|---|---|
| IoT sensor simulation | Scenario generator produces multi-channel 10-minute observations; replay publisher streams them over MQTT | **Working** |
| IoT integration | `services/simulator/replay.py` publishes to MQTT; `ingestion.py` subscribes and forwards to `POST /ingest/replay` | **Working** — `tests/test_mqtt_integration.py` runs the whole chain: real publisher payload, real broker, real subscriber callback, real HTTP, real API process, alert asserted at the far end |
| AI/ML anomaly detection | LightGBM normal-behaviour models, scikit-learn preprocessing, PyTorch IRNet CNN with Grad-CAM | **Measured** |
| Mobile/web technician dashboards | Responsive Next.js screens, technician workspace, offline service worker and precomputed bundle | **Working** — phone-width layout and offline navigation covered by browser tests |
| Cloud-based alerting | WebSocket alert stream with acknowledgement and escalation timer; optional Slack incoming webhook | **Working** — `tests/test_alert_delivery.py` asserts the outbound POST shape against a live local receiver, plus the unconfigured, failing and unreachable paths |

## Deliberate gaps

These are stated rather than hidden, and each has a reason.

- **TensorFlow is not used.** The infrared model is PyTorch and the wind models are LightGBM. The problem statement lists technologies that *can* be used; these were chosen for CPU inference size and tabular performance.
- **The CARE result is not an official benchmark score.** It uses real CARE v6 data and real event labels, but computes project-defined detection and false-alarm measures rather than the benchmark's Coverage, Accuracy, Reliability and Earliness components.
- **M1 and M3 are unavailable.** Their endpoints reject requests with 409 rather than returning a fabricated score.
- **Kubernetes manifests have not been applied to a live cluster.** They render through Kustomize and pass client-side validation.
- **Real solar telemetry has not been acquired.** The solar operating series are simulated; the infrared classifier is the measured solar capability.

The About page repeats this mapping in the application, so the same distinctions are visible to a user who never opens the repository.
