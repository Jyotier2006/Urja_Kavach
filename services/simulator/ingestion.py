"""MQTT subscriber forwards scenario/step to API for validated aligned-score ingestion.

Only the scenario id and step cross this boundary. The API re-reads the point from its own
bundle rather than trusting values off the wire, so a malformed or hostile message cannot
inject telemetry.
"""
import json
import os
import httpx
from paho.mqtt import client as mqtt

TOPIC_FILTER = 'site/+/asset/+/telemetry'


def api_url() -> str:
    return os.getenv('API_URL', 'http://localhost:8000')


def forward(payload: dict, url: str | None = None, timeout: float = 8) -> dict:
    """Post scenario/step to the API. Returns a result record rather than raising, so a single
    bad message never kills the subscriber loop."""
    try:
        body = {'scenario_id': payload['scenario_id'], 'step': payload['step']}
    except (KeyError, TypeError) as exc:
        return {'forwarded': False, 'error': 'Malformed payload', 'type': type(exc).__name__}
    try:
        with httpx.Client(timeout=timeout) as api:
            response = api.post((url or api_url()) + '/ingest/replay', json=body)
            response.raise_for_status()
        return {'forwarded': True, **body}
    except httpx.HTTPError as exc:
        return {'forwarded': False, 'error': 'Replay ingestion failed', 'type': type(exc).__name__}


def handle_message(raw: bytes, url: str | None = None) -> dict:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return {'forwarded': False, 'error': 'Undecodable payload', 'type': type(exc).__name__}
    return forward(payload, url)


def on_message(client, userdata, message):
    result = handle_message(message.payload)
    if not result['forwarded']: print(json.dumps(result), flush=True)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.on_connect = lambda c, u, f, reason, properties: c.subscribe(TOPIC_FILTER, qos=1)
    client.connect(os.getenv('MQTT_HOST', 'localhost'), int(os.getenv('MQTT_PORT', '1883')))
    client.loop_forever()


if __name__ == '__main__': main()
