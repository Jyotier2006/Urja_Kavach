# Build progress

## Scope
Complete website with all product screens and working demo interactions, Python services shipped separately, deployed to Vercel. Absent datasets must never produce invented results.

## Done
- Selected the problem statement and guidelines from the HackOut'26 brief.
- Created fleet and maintenance design concepts.
- Defined a static-export web delivery and optional Python API integration.

## In progress
- Application, reproducible synthetic demo bundle, Python domains and source documentation.

## CARE ingestion and first real M2 (2026-09-12)
- CARE To Compare v6 downloaded and MD5-verified against the provider checksum, extracted to `data/raw/CARE_To_Compare/` (95 events, 3 farms, ~19 GB, gitignored).
- LightGBM 4.7.0 installed and pinned in `requirements.lock`, replacing the Ridge stand-in for M2 (see D010).
- `services/ml/care_loader.py` infers column roles per farm from that farm's own feature description; `services/ml/normal_behavior.py` fits one temperature model per component; `scripts/train_care_nbm.py` evaluates against the dataset's own labels.
- Thermal-inertia predictors and trend scoring added (D013), and common-mode rejection tried then rejected on measurement (D014).
- **All three farms evaluated at one deployable threshold (D015): 14 of 37 anomaly events detected, 4 of 50 healthy events raising a false alarm, 26.3-day median warning on the strongest farm.** Per farm: 0 of 4 on A, 5 of 6 on B, 9 of 27 on C. Farm C was never tuned against and is the honest test; the farm B figure must not be quoted alone.
- Farm A contributes little: 8 of its 12 anomaly events have no running hours in the prediction window and are reported as not assessable rather than as misses. That is how farm A's windows were cut, not a tuning failure.
- Results published to the Performance page via `scripts/publish_care_evaluation.py`, sitting above the official CARE benchmark table, which stays empty because those quantities have not been computed.
- Results in `artifacts/metrics/care_m2_wind_farm_{a,b,c}.json`, ablation in `care_m2_wind_farm_b_cmr.json`.
- All five handoff issues fixed and verified: 3D rendering, mobile overflow, journey wording, offline/RSC prefetch 404s, accessibility. See docs/HANDOFF.md.
- Verification: 30 Python tests, 3 Playwright journey tests, production build, typecheck and lint all pass.

## Infrastructure (2026-09-12)
- Container images for the API and web app, both building and running. Compose brings up web, API and PostgreSQL with all three healthy and the API actually on Postgres rather than the SQLite fallback.
- Kubernetes base manifests with dev and prod overlays: probes, resource bounds, non-root and read-only-root security contexts, HPA, PodDisruptionBudget and ingress. Both overlays render. Never applied to a live cluster. (Corrected 2026-09-13: the `kubectl apply --dry-run=client` check recorded here only passed because a local cluster happened to be running — it downloads its schema from an API server. Replaced with offline kubeconform validation.)
- CI workflow covering Python tests, typecheck/lint/build, the browser journey, both image builds and manifest validation. (Corrected 2026-09-13: the workflow file was invalid YAML and GitHub never scheduled a single job, so nothing here had ever run in CI.)
- Fixed along the way: `scripts/build_offline.py` failed on a fresh clone because `artifacts/metrics/` did not exist, and `/health` hardcoded `care_evaluated: false`, which had gone stale.

## Infrared classifier (2026-09-12)
- Raptor Maps Infrared Solar Modules downloaded (15 MB, 20,000 crops, 12 classes, MIT). IRNet CNN trained from scratch with class-balanced loss on a stratified 70/15/15 split (D016).
- Held-out test: macro-F1 0.608, accuracy 75.2%, anomaly-vs-normal recall 96.1% at 85% precision. Rare classes (Soiling, Hot-Spot, Hot-Spot-Multi) are the weak point and are reported as such.
- Screening endpoint now returns class probabilities and genuine Grad-CAM when weights are present, and degrades to the labelled contrast check otherwise. Technician and Performance pages read the real state instead of hardcoding "not available".
- Weights (1.17 MB) committed under the amended rule in CONTRIBUTING.md; torch pinned to the CPU build so venv, Docker and CI resolve the same wheel.
- Python suite: 35 passed.

## Problem-statement gaps and integration verification (2026-09-13)
- **Vibration and phase current were never rendered.** Both channels existed in the generated scenarios, the feature map and the TypeScript types, but no screen drew them, leaving two of the four sensor modalities named in the brief invisible. Now charted on the investigation screen, each on its own axis, with rotor speed alongside current. Vibration climbs 1.10 to 3.10 mm/s across the fault scenario and stays flat at 1.10 in the normal one; it remains labelled Simulated and excluded from every reported model metric.
- **The MQTT hop had never been executed.** Publisher and subscriber logic is now importable rather than buried in `main()`, and `tests/test_mqtt_integration.py` runs the whole chain — real publisher payload, real broker, real subscriber callback, real HTTP, a real API process — asserting the alert at the far end. A second test confirms a forged reading off the wire cannot inject telemetry, since only scenario id and step cross the boundary.
- The broker is amqtt in-process, pinned in a separate `requirements-dev.lock` so it never reaches the API image. Docker was tried first; the local engine hangs on container start, and the in-process broker is the better answer anyway because it needs no Docker in CI.
- **Outbound alert delivery had never been exercised.** `tests/test_alert_delivery.py` asserts the webhook contract against a live local receiver across all four paths: configured, unconfigured, HTTP 500 and unreachable host. Slack itself is still never contacted.
- **CI had never run.** `--only-binary=:all:` puts a colon-space inside a plain YAML scalar, so GitHub rejected the workflow outright — fifteen runs across both repositories, none of which scheduled a job. Once fixed, two further failures surfaced that had always been latent: `setup-python`'s pip cache could not resolve a dependency file because this project pins through `.lock` files, and the manifest job used a validator that requires a live cluster.
- `docs/TRACEABILITY.md` was rewritten against the problem statement clause by clause; it had still recorded "CARE metrics unavailable", contradicting the published results.
- Verification: 48 Python tests (was 35), 3 Playwright tests, typecheck, lint, production build, offline service worker, and both overlays valid under kubeconform — 8 resources in dev, 10 in prod.

## Outstanding evidence
- M1 (EnergyFaultDetector autoencoder), M3 and the M1/M2 fusion are still untrained; SHAP explanations are still absent. M2 and the infrared CNN are the real models.
- The fleet, solar and planner screens still run on the synthetic demo bundle and remain labelled Simulated. The measured CARE results appear only on the Performance page, and are kept visibly separate from that simulated fleet.
- Reported CARE numbers come from our own normal-behavior approach on real CARE labels. They are not a published CARE benchmark score and must never be presented as one.
- No cloud deployment will be claimed. The container images build and Compose runs the full stack, but nothing has been deployed to a hosted environment or a live Kubernetes cluster.
- No Sentry DSN and no real Slack webhook are supplied, so neither has been contacted. The webhook contract is nonetheless verified against a local receiver. (Corrected 2026-09-13: this line previously also listed trained IR weights as missing, which contradicted the infrared section above — the weights are committed at `artifacts/models/ir_classifier.pt`.)

## Next
- Independent event-level CARE evaluation against a frozen split, then the official benchmark scoring.
- M1, M3 and the M1/M2 fusion; SHAP or ARCANA explanations to replace the current association panel.
- Real solar telemetry, so the solar screens stop running entirely on the synthetic bundle.
- Stronger rare-class infrared performance and a model card.
- Production authentication, shared event delivery and a durable escalation worker before any multi-replica deployment.
