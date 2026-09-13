"""Generate ALL demonstration values in Python. Never generate CARE benchmark claims."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
from dataclasses import asdict
import time
import numpy as np
from sklearn.linear_model import Ridge
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.api.app.domain.loss import Assumptions, estimate_loss
from services.optimizer.scheduler import PROFILES, demo_jobs, optimize
from services.ml.criticality import criticality_counter

OUT = ROOT / "artifacts/demo_bundle"
PUBLIC = ROOT / "apps/web/public/demo"


def write(name: str, data: dict | list):
    encoded = json.dumps(data, separators=(",", ":"), allow_nan=False)
    for root in [OUT, PUBLIC]:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded)


def scenario(name: str, degrade: bool, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    n_train, n = 3600, 288
    def operating(size):
        t = np.arange(size)
        wind = np.clip(7 + 2.3 * np.sin(t / 24) + rng.normal(0, 0.3, size), 2.5, 12)
        power = np.minimum(2100, 4.3 * wind**3)
        ambient = 27 + 7 * np.sin(t / 144 * 2 * np.pi - 1)
        return np.column_stack([wind, power, ambient])
    x = operating(n_train)
    y = 34 + 0.008 * x[:, 1] + 0.62 * x[:, 2] + rng.normal(0, 0.5, n_train)
    model = Ridge(alpha=1.0)
    split = int(n_train * 0.8); model.fit(x[:split], y[:split])
    calibration = y[split:] - model.predict(x[split:])
    spread = max(float(np.std(calibration)), 0.25)
    test = operating(n)
    expected = model.predict(test)
    drift = np.clip((np.arange(n) - 90) / 9, 0, 20) if degrade else np.zeros(n)
    actual = 34 + .008 * test[:, 1] + .62 * test[:, 2] + rng.normal(0, .45, n) + drift
    residual = (actual - expected) / spread
    smooth = []; prev = 0.
    for r in residual: prev = .12 * *abs(float(r)) + .88 * prev; smooth.append(prev)
    lower, upper = [float(v) for v in np.quantile(y[:split], [.005, .995])]
    b0 = (actual < lower) | (actual > upper)
    m2 = np.array(smooth) > 3.5
    b0_counter = criticality_counter(b0.tolist())
    m2_counter = criticality_counter(m2.tolist())
    values = []
    for i in range(n):
        values.append({"step": i, "relative_minutes": i * 10, "wind_speed": round(float(test[i, 0]), 2), "power": round(float(test[i, 1]), 1), "ambient_temperature": round(float(test[i, 2]), 1), "gearbox_temperature": round(float(actual[i]), 2), "expected_temperature": round(float(expected[i]), 2), "residual": round(float(residual[i]), 3), "bearing_temperature": round(float(expected[i] - 10 + drift[i] * .25), 2), "generator_temperature": round(float(expected[i] - 6 + drift[i] * .08), 2), "phase_current": round(float(test[i, 1] / 1.195), 1), "vibration": round(float(1.1 + drift[i] * .1), 2), "rotor_rpm": round(float(test[i, 0] * 1.35), 1), "scores": {"B0": float(b0[i]), "M2": round(smooth[i], 3)}, "criticality": {"B0": b0_counter["criticality"][i], "M2": m2_counter["criticality"][i]}})
    contributions = np.concatenate([test[-1] * model.coef_, [model.intercept_]])
    return {"id": name, "name": "Developing gearbox deviation" if degrade else "Normal operating variation", "asset_id": "T-03", "provenance": "Simulated", "kind": "synthetic", "event_label": int(degrade), "injection_start": 90 if degrade else None, "outcome_index": 275 if degrade else None, "outcome": "Injected fault progression reaches the scenario endpoint" if degrade else "No fault was injected", "data": values, "threshold": 72, "score_threshold": 3.5, "b0_upper": round(upper, 2), "b0_lower": round(lower, 2), "alarms": {"B0": b0_counter["alarm_index"], "M2": m2_counter["alarm_index"]}, "training": {"rows": split, "calibration_rows": n_train - split, "residual_spread": round(spread, 4), "source": "Synthetic observations from independent random draws", "features": ["wind_speed", "power", "ambient_temperature"], "excluded_target": "gearbox_temperature"}, "expectation_contributors": [{"signal": signal, "value": round(float(value), 2)} for signal, value in zip(["Wind speed", "Power", "Ambient temperature", "Base value"], contributions)]}


def infrared_samples():
    samples=[]
    for i, (label, action) in enumerate([("Hot spot", "Inspect electrical connections"), ("Soiling", "Schedule cleaning"), ("No anomaly", "Continue routine monitoring"), ("Vegetation", "Clear vegetation"), ("Diode", "Inspect bypass diode"), ("Shadowing", "Check obstruction")]):
        rng=np.random.default_rng(2026+i)
        a=rng.normal(.22,.045,(40,24))
        if i == 0: a[17:23,10:15] += .65
        elif i == 1: a[20:35] += .25
        elif i == 3: a[27:,:12] += .4
        elif i == 4: a[:,16:22] += .4
        elif i == 5: a[:20,:12] += .3
        a=np.clip(a,0,1)
        rgb=np.stack([np.clip(a*2,0,1),np.clip((a-.3)*2.1,0,1),np.clip(.45-a*.4,0,1)],-1)
        for root in [OUT,PUBLIC]:
            (root/'ir').mkdir(parents=True,exist_ok=True)
            Image.fromarray(np.uint8(rgb*255)).resize((240,400),Image.Resampling.NEAREST).save(root/f'ir/sample-{i}.png')
        samples.append({"id":f"IR-{i+1:02}","label":label,"action":action,"image":f"/demo/ir/sample-{i}.png","provenance":"Simulated illustration; annotation is not a classifier prediction","confidence":None})
    return samples


def main():
    began=time.perf_counter(); OUT.mkdir(parents=True,exist_ok=True); PUBLIC.mkdir(parents=True,exist_ok=True)
    scenarios=[scenario("sim-gearbox-drift",True,41),scenario("sim-normal-weather",False,57)]
    write('scenarios.json',scenarios)
    assets=[]
    positions=[[-8,-5],[0,-7],[8,-5],[-8,2],[0,0],[8,2]]
    risks=[12,35,91,10,18,73]
    for i in range(6):
        aid=f'T-{i+1:02}'
        assets.append({"id":aid,"name":f'Turbine {i+1:02}',"type":"wind","capacity_kw":2100,"position":positions[i],"risk":risks[i],"status":"Warning" if i==2 else "Watch" if i==5 else "Healthy","subsystem":"gearbox" if i==2 else "generator" if i==5 else "nacelle","power_kw":round(735+i*94.7,1),"exposure_inr":320000 if i==2 else 188000 if i==5 else 0,"scenario_id":"sim-gearbox-drift" if i==2 else "sim-normal-weather","provenance":"Simulated"})
    for i in range(4):
        assets.append({"id":f'S-{i+1:02}',"name":f'Solar block {i+1:02}',"type":"solar","capacity_kw":800,"position":[-8+i*5.3,9],"risk":[11,64,42,57][i],"status":"Watch" if i==1 else "Healthy","subsystem":"solar","power_kw":round(420-i*22.6,1),"exposure_inr":88000 if i==1 else 0,"scenario_id":None,"provenance":"Simulated"})
    rng=np.random.default_rng(2026)
    rows=[]
    for i in range(12):
        trend=1+rng.normal(0,.02,28)
        if i in (3,4,5): trend-=np.maximum(0,np.arange(28)-9)*(.009 if i!=4 else .014)
        if i==10: trend[-3:]=[.04,.03,.06]
        rows.append({"id":f'INV-{i+1:02}',"block":f'S-{i//3+1:02}',"performance_index":round(float(trend[-1]),3),"efficiency":round(float(.967-rng.uniform(0,.025)),3),"values":[round(float(v),3) for v in trend]})
    daily_loss=185; tariff=3; cleaning_cost=2500
    solar={"provenance":"Simulated","days":list(range(1,29)),"inverters":rows,"trend":[{"day":d+1,"actual":round(float(np.mean([r['values'][d] for r in rows])),3),"expected":1.0} for d in range(28)],"cleaning":{"asset_id":"S-02","daily_loss_kwh":daily_loss,"daily_loss_inr":daily_loss*tariff,"cost_inr":cleaning_cost,"recommended_day":int(np.ceil(cleaning_cost/(daily_loss*tariff))),"formula":"Cleaning cost / (estimated daily recoverable kWh × tariff)","tariff":tariff},"validation":{"status":"not_run","note":"Real solar telemetry and independent fault-injection validation are not bundled. The displayed trends are synthetic illustrations."}}
    write('solar.json',solar)
    default=estimate_loss(Assumptions()); write('loss.json',default)
    # Compact offline scenario catalog: each supported setting has a Python-produced result.
    loss_catalog={}
    for tariff in [1,1.5,2,2.5,3,3.5,4,4.5,5,5.5,6]:
        for cf in [.2,.3,.4]:
            for hazard in [.04,.08,.12,.16]:
                a=Assumptions(tariff_inr_kwh=tariff,capacity_factor=cf,daily_hazard=hazard)
                loss_catalog[f'{tariff:g}|{cf:g}|{hazard:g}']=estimate_loss(a,draws=500)
    write('loss-catalog.json',loss_catalog)
    plans={}; timings=[]
    for count in [1,2,3,4]:
        for horizon in [3,5,7]:
            for profile in PROFILES:
                for delay in [0,1,2,3]:
                    for locked in [False,True]:
                        jobs=demo_jobs(delay,locked)
                        plan=optimize(jobs,PROFILES[profile][:count],horizon,limit_seconds=.4)
                        plans[f'{count}|{horizon}|{profile}|{delay}|{int(locked)}']=plan
                        timings.append(plan['elapsed_ms'])
        print(f'Generated plans for {count} crew(s)',flush=True)
    write('plans.json',plans)
    default_plan=plans['2|7|balanced|0|0']; write('schedule.json',default_plan)
    ir=infrared_samples()
    alerts=[{"id":"A-101","asset_id":"T-03","title":"Gearbox temperature deviation","detail":"Persistent temperature residual above expected operating behavior.","severity":"Warning","state":"Warning","subsystem":"gearbox","scenario_id":"sim-gearbox-drift","step":scenarios[0]['alarms']['M2'] or 200,"provenance":"Simulated"},{"id":"A-102","asset_id":"S-02","title":"Performance index decline","detail":"Illustrative gradual decline under comparable irradiance.","severity":"Watch","state":"Watch","subsystem":"solar","scenario_id":None,"step":170,"provenance":"Simulated"},{"id":"A-103","asset_id":"T-06","title":"Generator current drift","detail":"Illustrative current deviation requires an electrical inspection.","severity":"Watch","state":"Watch","subsystem":"generator","scenario_id":None,"step":190,"provenance":"Simulated"}]
    workorders=[{"id":"WO-101","asset_id":"T-03","title":"Inspect gearbox and oil circuit","status":"Assigned","priority":"High","crew":"Crew 1","skill":"mechanical","duration_hours":4,"checklist":[{"text":"Confirm work permit and isolation with the site supervisor","done":False},{"text":"Review gearbox temperature history and lubrication records","done":False},{"text":"Inspect for leaks and record oil condition","done":False},{"text":"Upload findings and request supervisor review","done":False}],"notes":"","provenance":"Simulated"},{"id":"WO-102","asset_id":"S-02","title":"Inspect soiling before cleaning","status":"Assigned","priority":"Medium","crew":"Crew 2","skill":"solar","duration_hours":3,"checklist":[{"text":"Confirm weather and site-approved cleaning procedure","done":False},{"text":"Document module soiling and visible obstructions","done":False},{"text":"Record before/after performance and images","done":False}],"notes":"","provenance":"Simulated"}]
    metrics={"status":"not_evaluated","source":"No CARE/solar/IR training datasets were supplied","care":{"models":[{"id":id,"name":name,"care":None,"coverage":None,"accuracy":None,"reliability":None,"earliness":None,"events_detected":None,"test_events":None,"false_alarms":None,"lead_time_hours":None,"status":"Awaiting held-out evaluation"} for id,name in [('B0','Static threshold'),('M1','Autoencoder'),('M2','Regime-aware'),('M3','Fusion')]]},"ir":{"macro_f1":None,"status":"Trained classifier weights are not bundled"},"solar":{"status":"Awaiting real telemetry and injection validation"},"synthetic_check":{"model":"Ridge normal-behavior prototype","anomaly_alarm_step":scenarios[0]['alarms']['M2'],"normal_alarm_step":scenarios[1]['alarms']['M2'],"threshold":72,"source":"scenarios.json","note":"Designed synthetic scenarios; not empirical benchmark evidence"}}
    bundle={"schema_version":"1.0","id":"urja-sim-2026-v1","provenance":"Simulated","site":{"id":"kutch-demo","name":"Kutch renewable park","location":"Gujarat, India · schematic demonstration","latitude":23.73,"longitude":69.85},"assets":assets,"alerts":alerts,"workorders":workorders,"ir_samples":ir,"metrics":metrics,"default_step":210,"total_capacity_kw":sum(a['capacity_kw'] for a in assets),"snapshot_power_kw":round(sum(a['power_kw'] for a in assets),1),"exposure_inr":sum(a['exposure_inr'] for a in assets),"crew_count":2,"sources":[{"name":"Synthetic demonstration","license":"CC0-1.0","included":True,"description":"Generated with deterministic Python scripts for interface and workflow validation."},{"name":"CARE to Compare v6","license":"CC BY-SA 4.0","included":False,"url":"https://doi.org/10.5281/zenodo.15846963"},{"name":"Solar Power Generation Data · anikannal","license":"Verify dataset terms before ingestion","included":False,"url":"https://www.kaggle.com/datasets/anikannal/solar-power-generation-data"},{"name":"Raptor Maps Infrared Solar Modules","license":"Verify upstream dataset terms before redistribution","included":False,"url":"https://github.com/RaptorMaps/InfraredSolarModules"}]}
    write('bundle.json',bundle); write('metrics.json',metrics)
    manifest={"schema_version":"1.0","provenance":"Simulated","seed":2026,"files":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.json')},"generation_seconds":round(time.perf_counter()-began,2),"planner_cases":len(plans),"planner_max_ms":max(timings),"planner_median_ms":round(float(np.median(timings)),2)}
    write('manifest.json',manifest)
    target=ROOT/'artifacts/metrics'; target.mkdir(parents=True,exist_ok=True)
    (target/'models.json').write_text(json.dumps(metrics,indent=2))
    print(json.dumps(manifest | {'files':len(manifest['files'])}),flush=True)

if __name__=='__main__': main()
