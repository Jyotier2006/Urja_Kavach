"""R7: the IoT ingestion chain, end to end, over a real MQTT broker.

The MQTT hop used to be the one link nothing had ever executed. These tests run the real
publisher payload through a real broker, into the real subscriber callback, over real HTTP,
into the real API process, and assert an alert comes out the far end.

The broker is amqtt running in-process rather than a container, so the chain is exercised on
any machine and in CI without Docker. amqtt is a development dependency only; it is not
installed into the API image, and these tests skip if it is absent.
"""
import asyncio
from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
from paho.mqtt import client as mqtt
import pytest

from services.simulator.ingestion import TOPIC_FILTER, handle_message
from services.simulator.replay import build_payload, find_scenario, topic_for

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


@contextmanager
def broker():
    """A real MQTT broker in a background event loop."""
    amqtt_broker = pytest.importorskip('amqtt.broker', reason='amqtt is needed for the broker round trip')
    for noisy in ('amqtt', 'transitions'): logging.getLogger(noisy).setLevel(logging.WARNING)

    port = _free_port()
    config = {'listeners': {'default': {'type': 'tcp', 'bind': f'127.0.0.1:{port}', 'max_connections': 20}},
              'sys_interval': 0, 'auth': {'allow-anonymous': True}, 'topic_check': {'enabled': False}}
    loop = asyncio.new_event_loop()
    instance = amqtt_broker.Broker(config, loop)
    started, failure = threading.Event(), []

    def run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(instance.start()); started.set(); loop.run_forever()
        except Exception as exc:  # noqa: BLE001 - surfaced as a skip below
            failure.append(exc); started.set()

    thread = threading.Thread(target=run, daemon=True); thread.start()
    if not started.wait(40) or failure:
        pytest.skip(f'the in-process MQTT broker did not start: {failure[:1]}')
    try:
        yield port
    finally:
        try: asyncio.run_coroutine_threadsafe(instance.shutdown(), loop).result(timeout=15)
        except Exception: pass  # noqa: BLE001 - teardown must not mask a test failure
        loop.call_soon_threadsafe(loop.stop); thread.join(timeout=15)


@contextmanager
def api_server(tmp_path):
    """The real FastAPI app in its own process, on an isolated database."""
    port = _free_port()
    env = {**os.environ, 'DATABASE_URL': f'sqlite:///{tmp_path / "mqtt.db"}', 'ALERT_ESCALATION_SECONDS': '600'}
    env.pop('SLACK_WEBHOOK_URL', None)
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'services.api.app.main:app',
                                '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'],
                               cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if process.poll() is not None: pytest.skip('the API process exited before becoming ready')
            try:
                if httpx.get(base + '/health', timeout=2).status_code == 200: break
            except httpx.HTTPError: time.sleep(0.4)
        else:
            pytest.skip('the API did not become ready in time')
        yield base
    finally:
        process.terminate()
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: process.kill()


@contextmanager
def subscriber(port: int, api_base: str, results: list):
    """The shipped subscriber callback, wired to the real broker."""
    subscribed = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, u, flags, reason, props: c.subscribe(TOPIC_FILTER, qos=1)
    client.on_subscribe = lambda *args, **kwargs: subscribed.set()
    client.on_message = lambda c, u, message: results.append(handle_message(message.payload, url=api_base))
    client.connect('127.0.0.1', port); client.loop_start()
    try:
        assert subscribed.wait(30), 'the subscriber never completed its subscription'
        yield client
    finally:
        client.loop_stop(); client.disconnect()


def _publish(port: int, topic: str, payload: dict):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect('127.0.0.1', port); client.loop_start()
    try:
        info = client.publish(topic, json.dumps(payload), qos=1)
        info.wait_for_publish(timeout=20)  # returns None; is_published() carries the outcome
        assert info.is_published(), 'the broker never acknowledged the publish'
    finally:
        client.loop_stop(); client.disconnect()


def _await(predicate, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = predicate()
        if value: return value
        time.sleep(0.25)
    return None


def _alarm_point(scenario: dict) -> dict:
    return next(p for p in scenario['data'] if p['step'] == scenario['alarms']['M2'])


# --------------------------------------------------------------------------------------
# Message contract. No broker, no API: these always run.
# --------------------------------------------------------------------------------------

def test_the_published_topic_matches_the_subscriber_wildcard():
    """`site/+/asset/+/telemetry` has to keep matching whatever the publisher emits."""
    assert topic_for('T-03') == 'site/kutch-demo/asset/T-03/telemetry'
    assert mqtt.topic_matches_sub(TOPIC_FILTER, topic_for('T-03'))
    assert mqtt.topic_matches_sub(TOPIC_FILTER, topic_for('S-02', site='other-park'))


def test_every_message_carries_its_provenance():
    scenario = find_scenario('sim-gearbox-drift')
    payload = build_payload(scenario, scenario['data'][10])
    assert payload['provenance'] == scenario['provenance']
    assert payload['scenario_id'] == 'sim-gearbox-drift' and payload['asset_id'] == 'T-03'


def test_malformed_traffic_is_reported_rather_than_raised():
    """One bad message must never take down the subscriber loop."""
    assert handle_message(b'not json at all')['error'] == 'Undecodable payload'
    assert handle_message(json.dumps({'nothing': 'useful'}).encode())['error'] == 'Malformed payload'


# --------------------------------------------------------------------------------------
# The real chain.
# --------------------------------------------------------------------------------------

def test_a_published_reading_travels_the_whole_chain_and_raises_an_alert(tmp_path):
    scenario = find_scenario('sim-gearbox-drift')
    point = _alarm_point(scenario)
    results: list[dict] = []

    with broker() as port, api_server(tmp_path) as base:
        with subscriber(port, base, results):
            _publish(port, topic_for(scenario['asset_id']), build_payload(scenario, point))

            assert _await(lambda: results), 'the subscriber never received the published reading'
            assert results[0]['forwarded'], f'forwarding failed: {results[0]}'
            assert results[0]['step'] == scenario['alarms']['M2']

            alerts = _await(lambda: [a for a in httpx.get(base + '/alerts', timeout=10).json()
                                     if a['asset_id'] == scenario['asset_id']])
            assert alerts, 'the API never raised an alert from the ingested reading'
            assert alerts[0]['severity'] == 'Warning'
            assert alerts[0]['provenance'] == scenario['provenance']


def test_a_forged_reading_cannot_inject_telemetry(tmp_path):
    """Only scenario id and step cross the boundary; the API re-reads the point from its own bundle."""
    scenario = find_scenario('sim-gearbox-drift')
    forged = build_payload(scenario, _alarm_point(scenario))
    forged['point'] = {**forged['point'], 'gearbox_temperature': 9999.0}
    results: list[dict] = []

    with broker() as port, api_server(tmp_path) as base:
        with subscriber(port, base, results):
            _publish(port, topic_for(scenario['asset_id']), forged)
            assert _await(lambda: results) and results[0]['forwarded']

        stored = httpx.get(base + f'/scenarios/{scenario["id"]}/telemetry', timeout=10).json()['data']
        assert all(row.get('gearbox_temperature') != 9999.0 for row in stored), \
            'a forged value off the wire must never reach stored telemetry'
