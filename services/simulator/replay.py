"""R7: MQTT replay of bundled scenarios. No real measurements are synthesized silently.

The payload/topic helpers are importable so the transport can be exercised by tests without
launching the CLI. See tests/test_mqtt_integration.py for the broker round trip.
"""
import argparse
import json
from pathlib import Path
import time
from paho.mqtt import client as mqtt

DEFAULT_SITE = 'kutch-demo'
BUNDLE = Path(__file__).resolve().parents[2] / 'artifacts/demo_bundle/scenarios.json'


def load_scenarios(bundle: Path = BUNDLE) -> list[dict]:
    return json.loads(bundle.read_text())


def find_scenario(scenario_id: str, bundle: Path = BUNDLE) -> dict | None:
    return next((s for s in load_scenarios(bundle) if s['id'] == scenario_id), None)


def topic_for(asset_id: str, site: str = DEFAULT_SITE) -> str:
    """Topic layout the subscriber's wildcard `site/+/asset/+/telemetry` must keep matching."""
    return f'site/{site}/asset/{asset_id}/telemetry'


def build_payload(scenario: dict, point: dict) -> dict:
    """Provenance travels with every message so a consumer can never mistake replay for measurement."""
    return {'scenario_id': scenario['id'], 'asset_id': scenario['asset_id'], 'step': point['step'],
            'provenance': scenario['provenance'], 'point': point}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--broker', default='localhost')
    parser.add_argument('--port', type=int, default=1883)
    parser.add_argument('--scenario', default='sim-gearbox-drift')
    parser.add_argument('--speed', type=float, default=1200, help='Historical seconds per wall second')
    parser.add_argument('--start-step', type=int, default=170)
    parser.add_argument('--site', default=DEFAULT_SITE)
    args = parser.parse_args()
    if args.speed <= 0: parser.error('Speed must be positive')
    scenario = find_scenario(args.scenario)
    if scenario is None: parser.error('Unknown scenario')
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.port); client.loop_start()
    topic = topic_for(scenario['asset_id'], args.site)
    try:
        for point in scenario['data'][args.start_step:]:
            result = client.publish(topic, json.dumps(build_payload(scenario, point)), qos=1)
            result.wait_for_publish(timeout=5)
            if point['step'] % 24 == 0: print(json.dumps({'published_step': point['step'], 'provenance': scenario['provenance']}), flush=True)
            time.sleep(600 / args.speed)
    finally: client.loop_stop(); client.disconnect()


if __name__ == '__main__': main()
